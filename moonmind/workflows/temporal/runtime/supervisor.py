"""Managed run supervision: heartbeats, timeout, exit classification, cancellation."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from temporalio import activity
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for the optional callback fired when a supervised process completes.
# Signature: async (result_dict) -> None
# The result_dict is AgentRunResult-compatible.
CompletionCallback = Callable[[dict[str, Any]], Awaitable[None]]

from moonmind.schemas.agent_runtime_models import (
    ManagedRunRecord,
)

from .log_streamer import RuntimeLogStreamer
from .store import ManagedRunStore
from .strategies import get_strategy
from .strategies.base import ManagedRuntimeExitResult
from .output_parser import ParsedOutput

HEARTBEAT_INTERVAL = 30  # seconds
GRACEFUL_TERMINATE_WAIT_SECONDS = (
    1.0  # seconds to wait for graceful SIGTERM before SIGKILL
)


class ManagedRunSupervisor:
    """Supervises managed agent subprocess lifecycle."""

    def __init__(
        self,
        store: ManagedRunStore,
        log_streamer: RuntimeLogStreamer,
        *,
        completion_callback: CompletionCallback | None = None,
    ) -> None:
        self._store = store
        self._log_streamer = log_streamer
        self._completion_callback = completion_callback
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._cleanup_paths: dict[str, tuple[str, ...]] = {}

    async def supervise(
        self,
        *,
        run_id: str,
        process: asyncio.subprocess.Process,
        timeout_seconds: int = 3600,
        exit_code_path: str | None = None,
        cleanup_paths: list[str] | None = None,
    ) -> ManagedRunRecord:
        """Supervise a process and track heartbeat, completion, and cleanup."""
        self._active_processes[run_id] = process
        registered_paths: list[str] = list(cleanup_paths or [])
        if exit_code_path:
            registered_paths.append(exit_code_path)
        self._cleanup_paths[run_id] = tuple(
            path for path in dict.fromkeys(registered_paths) if path
        )
        self._store.update_status(run_id, "running")
        start_time = datetime.now(tz=UTC)

        try:
            # Resolve strategy output parser for this runtime
            record = self._store.load(run_id)
            runtime_id = record.runtime_id if record else None
            strategy = get_strategy(runtime_id) if runtime_id else None
            parser = strategy.create_output_parser() if strategy else None
            live_rate_limit_detected = asyncio.Event()

            async def _handle_stream_events(events: list[dict[str, Any]]) -> None:
                if strategy is None or not strategy.terminate_on_live_rate_limit():
                    return
                for event in events:
                    if self._is_live_rate_limit_event(event):
                        live_rate_limit_detected.set()
                        break

            # Run heartbeat/wait and log streaming CONCURRENTLY so that OS
            # pipe buffers are drained in real-time.  Sequential streaming
            # (heartbeat first, then stream) fills the kernel pipe buffer for
            # processes with large output, causing the subprocess write-end to
            # block indefinitely — a deadlock.  Concurrent streaming also means
            # output is captured as it is produced, enabling true live output.
            heartbeat_task = asyncio.create_task(
                self._heartbeat_and_wait_with_timeout(
                    run_id, process, timeout_seconds
                )
            )
            stream_task = asyncio.create_task(
                self._log_streamer.stream_and_parse(
                    process.stdout,
                    process.stderr,
                    run_id=run_id,
                    workspace_path=record.workspace_path if record else None,
                    parser=parser,
                    event_callback=_handle_stream_events,
                )
            )
            terminate_on_rate_limit_task = None
            if strategy is not None and strategy.terminate_on_live_rate_limit():
                terminate_on_rate_limit_task = asyncio.create_task(
                    self._terminate_on_live_rate_limit(
                        process=process,
                        trigger=live_rate_limit_detected,
                    )
                )
            (
                (process_exit_code, timed_out),
                (log_refs, stdout_content, stderr_content, parsed_output, events),
            ) = await asyncio.gather(heartbeat_task, stream_task)
            if terminate_on_rate_limit_task is not None:
                terminate_on_rate_limit_task.cancel()
                with suppress(asyncio.CancelledError):
                    _ = await terminate_on_rate_limit_task

            if timed_out:
                exit_code = None
                await self._terminate_process(process)
            else:
                exit_code = self._resolve_effective_exit_code(
                    process_exit_code=process_exit_code,
                    exit_code_path=exit_code_path,
                )

            duration = (datetime.now(tz=UTC) - start_time).total_seconds()

            diagnostics_ref = self._log_streamer.collect_diagnostics(
                run_id=run_id,
                exit_code=exit_code,
                duration_seconds=duration,
                log_refs=log_refs,
                parsed_output=parsed_output,
                events=events,
            )

            # Classify exit
            exit_result = self._classify_exit(
                runtime_id=runtime_id,
                exit_code=exit_code,
                timed_out=timed_out,
                stdout=stdout_content,
                stderr=stderr_content,
                parsed_output=parsed_output,
            )
            status = exit_result.status
            failure_class = exit_result.failure_class

            error_message = None
            if status == "failed":
                if runtime_id == "gemini_cli" and exit_result.provider_error_code == "429":
                    error_message = "Gemini API rate limit exceeded"
                else:
                    error_message = f"Process exited with code {exit_code}"
                    if parsed_output.error_messages:
                        error_message += f": {parsed_output.error_messages[0]}"
            elif status == "timed_out":
                error_message = (
                    f"Process timed out after {timeout_seconds}s"
                )

            record = self._store.update_status(
                run_id,
                status,
                exit_code=exit_code,
                finished_at=datetime.now(tz=UTC),
                diagnostics_ref=diagnostics_ref,
                stdout_artifact_ref=log_refs.get("stdout"),
                stderr_artifact_ref=log_refs.get("stderr"),
                last_log_at=datetime.now(tz=UTC),
                failure_class=failure_class,
                provider_error_code=exit_result.provider_error_code,
                error_message=error_message,
            )

            # Fire completion callback (best-effort, never crashes the supervisor).
            if self._completion_callback is not None:
                try:
                    payload = self._build_completion_payload(record, log_refs)
                    await self._completion_callback(payload)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "completion_callback failed for run_id=%s",
                        run_id,
                        exc_info=True,
                    )

            return record
        finally:
            self._active_processes.pop(run_id, None)
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))

    async def _heartbeat_and_wait(
        self, run_id: str, process: asyncio.subprocess.Process
    ) -> int:
        """Send heartbeats while waiting for the process to complete."""
        while True:
            try:
                exit_code = await asyncio.wait_for(
                    process.wait(), timeout=HEARTBEAT_INTERVAL
                )
                return exit_code
            except asyncio.TimeoutError:
                try:
                    activity.heartbeat({"run_id": run_id})
                except Exception as e:
                    # Activity heartbeat failures are non-fatal for the supervisor loop
                    logger.debug("Activity heartbeat failed: %s", e)
                self._store.update_status(
                    run_id,
                    "running",
                    last_heartbeat_at=datetime.now(tz=UTC),
                )

    async def _heartbeat_and_wait_with_timeout(
        self,
        run_id: str,
        process: asyncio.subprocess.Process,
        timeout_seconds: int,
    ) -> tuple[int | None, bool]:
        """Wrap _heartbeat_and_wait with a total timeout.

        Returns ``(exit_code, timed_out)`` so callers can unpack both
        the process exit code and the timeout flag from a single awaitable,
        making it composable with ``asyncio.gather()``.

        On timeout, the process is terminated immediately so that any
        concurrent streaming task observes EOF and exits promptly.
        Without this, ``asyncio.gather()`` would block indefinitely on
        the stream task waiting for EOF that never arrives.
        """
        try:
            exit_code = await asyncio.wait_for(
                self._heartbeat_and_wait(run_id, process),
                timeout=timeout_seconds,
            )
            return exit_code, False
        except asyncio.TimeoutError:
            # Terminate the process so the concurrent streaming task sees EOF
            # and can complete, allowing asyncio.gather() to unblock.
            await self._terminate_process(process)
            return None, True

    async def cancel(self, run_id: str) -> None:
        """Cancel a running managed process: terminate -> wait -> kill."""
        process = self._active_processes.get(run_id)
        if process is None:
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
            self._store.update_status(
                run_id,
                "canceled",
                finished_at=datetime.now(tz=UTC),
                error_message="Cancelled (process not found in supervisor)",
            )
            return

        await self._terminate_process(process)
        self._active_processes.pop(run_id, None)
        self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
        self._store.update_status(
            run_id,
            "canceled",
            finished_at=datetime.now(tz=UTC),
            exit_code=process.returncode,
            error_message="Cancelled by supervisor",
        )

    async def reconcile(self) -> list[ManagedRunRecord]:
        """On startup: scan active records and mark lost PIDs as failed."""
        active_records = self._store.list_active()
        reconciled: list[ManagedRunRecord] = []

        for record in active_records:
            if record.pid is not None and not self._pid_alive(record.pid):
                updated = self._store.update_status(
                    record.run_id,
                    "failed",
                    finished_at=datetime.now(tz=UTC),
                    failure_class="system_error",
                    error_message=(
                        f"Process {record.pid} not found during reconciliation"
                    ),
                )
                reconciled.append(updated)

        return reconciled

    @staticmethod
    async def _terminate_on_live_rate_limit(
        *,
        process: asyncio.subprocess.Process,
        trigger: asyncio.Event,
    ) -> None:
        await trigger.wait()
        await ManagedRunSupervisor._terminate_process(process)

    @staticmethod
    def _is_live_rate_limit_event(event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "rate_limit":
            return True
        status_code = event.get("status_code") or event.get("statusCode")
        try:
            return int(status_code) == 429
        except (TypeError, ValueError):
            return False

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait(2s) -> kill sequence."""
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=GRACEFUL_TERMINATE_WAIT_SECONDS,
            )
        except (asyncio.TimeoutError, ProcessLookupError):
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.wait()

    @staticmethod
    def _classify_exit(
        *,
        runtime_id: str | None,
        exit_code: int | None,
        timed_out: bool,
        stdout: str,
        stderr: str,
        parsed_output: ParsedOutput | None = None,
    ) -> ManagedRuntimeExitResult:
        """Classify process exit into a run state and optional failure class."""

        if timed_out:
            return ManagedRuntimeExitResult(
                status="timed_out",
                failure_class="execution_error",
            )

        if runtime_id:
            strategy = get_strategy(runtime_id)
            if strategy:
                return strategy.classify_result(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    parsed_output=parsed_output,
                )

        if exit_code == 0:
            return ManagedRuntimeExitResult(
                status="completed",
                failure_class=None,
            )
        return ManagedRuntimeExitResult(
            status="failed",
            failure_class="execution_error",
        )

    @staticmethod
    def _resolve_effective_exit_code(
        *,
        process_exit_code: int | None,
        exit_code_path: str | None,
    ) -> int | None:
        """Resolve the authoritative exit code for the managed child process."""
        if not exit_code_path:
            return process_exit_code

        parsed = ManagedRunSupervisor._read_exit_code_file(exit_code_path)
        if parsed is None:
            logger.warning(
                "Missing or invalid managed exit code file at %s; failing closed",
                exit_code_path,
            )
            return 1
        return parsed

    @staticmethod
    def _read_exit_code_file(exit_code_path: str) -> int | None:
        """Read one integer exit code from the given path."""
        try:
            raw_value = Path(exit_code_path).read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return None
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    @staticmethod
    def _cleanup_runtime_files(paths: tuple[str, ...]) -> None:
        """Best-effort cleanup for launcher runtime files."""
        for path in paths:
            with suppress(OSError):
                os.remove(path)

    @staticmethod
    def _build_completion_payload(
        record: ManagedRunRecord,
        log_refs: dict[str, str],
    ) -> dict[str, Any]:
        """Build an AgentRunResult-compatible dict from a completed ManagedRunRecord."""
        output_refs: list[str] = []
        if record.stdout_artifact_ref:
            output_refs.append(record.stdout_artifact_ref)
        if record.stderr_artifact_ref:
            output_refs.append(record.stderr_artifact_ref)
        if record.diagnostics_ref:
            output_refs.append(record.diagnostics_ref)
        for ref in log_refs.values():
            if ref and ref not in output_refs:
                output_refs.append(ref)

        summary = record.error_message or f"Process exited with status {record.status}"
        return {
            "summary": summary,
            "output_refs": output_refs,
            "failure_class": record.failure_class,
            "provider_error_code": record.provider_error_code,
        }

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check whether a process with the given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False
