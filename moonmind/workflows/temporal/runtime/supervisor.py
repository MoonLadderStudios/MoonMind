"""Managed run supervision: heartbeats, timeout, exit classification, cancellation."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
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
NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS = 30  # seconds
_DUPLICATE_WARNING_THRESHOLD = 3
_WARNING_SUBSTRINGS = ("warning", "warn", "deprecated", "rate limit")
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
        self._deferred_cleanup_paths: dict[str, tuple[str, ...]] = {}

    async def supervise(
        self,
        *,
        run_id: str,
        process: asyncio.subprocess.Process,
        timeout_seconds: int = 3600,
        exit_code_path: str | None = None,
        cleanup_paths: list[str] | None = None,
        deferred_cleanup_paths: list[str] | None = None,
    ) -> ManagedRunRecord:
        """Supervise a process and track heartbeat, completion, and cleanup."""
        self._active_processes[run_id] = process
        registered_paths: list[str] = list(cleanup_paths or [])
        if exit_code_path:
            registered_paths.append(exit_code_path)
        self._cleanup_paths[run_id] = tuple(
            path for path in dict.fromkeys(registered_paths) if path
        )
        self._deferred_cleanup_paths[run_id] = tuple(
            path for path in dict.fromkeys(deferred_cleanup_paths or []) if path
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
            stalled_progress_detected = asyncio.Event()
            timed_out_by_supervisor = False
            live_rate_limit_requested = False
            stalled_no_progress = False
            stalled_progress_reason: str | None = None
            last_output_seen_at = start_time
            last_no_output_annotation_at = start_time
            first_stdout_seen = False
            first_stderr_seen = False
            stderr_buffer = ""
            warning_counts: dict[str, int] = {}
            warning_dedup_announced: set[str] = set()
            progress_probe_warning_logged = False
            progress_timeout_seconds = (
                strategy.progress_stall_timeout_seconds(timeout_seconds=timeout_seconds)
                if strategy is not None
                else None
            )

            def _record_annotation(
                annotation_type: str,
                text: str,
                *,
                reason: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> None:
                metadata = dict(metadata or {})
                metadata.setdefault("annotation_type", annotation_type)
                if reason is not None:
                    metadata["reason"] = reason
                metadata.setdefault("source", "supervisor")
                metadata["text"] = text
                self._log_streamer.emit_system_annotation(
                    run_id=run_id,
                    workspace_path=record.workspace_path if record else None,
                    text=text,
                    metadata=metadata,
                    annotation_type=annotation_type,
                )

            def _is_warning_text(text: str) -> bool:
                lowered = text.lower()
                return any(keyword in lowered for keyword in _WARNING_SUBSTRINGS)

            def _handle_stream_chunk(stream_name: str, text: str) -> None:
                nonlocal first_stdout_seen, first_stderr_seen, last_output_seen_at, stderr_buffer
                if not text:
                    return
                if not text.isspace():
                    last_output_seen_at = datetime.now(tz=UTC)
                if not first_stdout_seen and stream_name == "stdout":
                    first_stdout_seen = True
                    _record_annotation(
                        annotation_type="first_stdout_seen",
                        text="Supervisor: first stdout output received.",
                        reason="stream_observed",
                    )
                if not first_stderr_seen and stream_name == "stderr":
                    first_stderr_seen = True
                    _record_annotation(
                        annotation_type="first_stderr_seen",
                        text="Supervisor: first stderr output received.",
                        reason="stream_observed",
                    )
                if stream_name != "stderr":
                    return
                
                stderr_buffer += text
                if "\n" not in stderr_buffer:
                    return
                
                lines = stderr_buffer.split("\n")
                # The last element is either an empty string (if text ended with \n)
                # or a partial line that we need to buffer.
                stderr_buffer = lines.pop()
                
                for raw_line in lines:
                    line = raw_line.strip()
                    if not line:
                        continue
                    line_key = line.lower()
                    if not _is_warning_text(line_key):
                        continue
                    count = warning_counts.get(line_key, 0) + 1
                    warning_counts[line_key] = count
                    if (
                        count >= _DUPLICATE_WARNING_THRESHOLD
                        and line_key not in warning_dedup_announced
                    ):
                        _record_annotation(
                            annotation_type="warning_deduplicated",
                            text=(
                                "Supervisor: repeated config warning observed "
                                f"{count} times; suppressing duplicates in live view."
                            ),
                            reason="warning_deduplication",
                            metadata={
                                "duplicate_count": count,
                                "warning_text": line,
                            },
                        )
                        warning_dedup_announced.add(line_key)

            _record_annotation(
                annotation_type="run_started",
                text="Supervisor: managed run started.",
                reason="supervisor_state",
            )
            _record_annotation(
                annotation_type="command_launched",
                text="Supervisor: runtime command launched in managed mode.",
                reason="supervisor_state",
            )
            if not (record and record.workspace_path):
                _record_annotation(
                    annotation_type="live_stream_unavailable",
                    text="Supervisor: live streaming unavailable; durable artifact capture continues.",
                    reason="stream_unavailable",
                )

            async def _handle_stream_events(events: list[dict[str, Any]]) -> None:
                nonlocal live_rate_limit_requested
                if strategy is None or not strategy.terminate_on_live_rate_limit():
                    return
                for event in events:
                    if self._is_live_rate_limit_event(event):
                        live_rate_limit_detected.set()
                        live_rate_limit_requested = True
                        break

            async def _latest_progress_at() -> datetime:
                nonlocal progress_probe_warning_logged
                latest = last_output_seen_at
                if (
                    strategy is None
                    or progress_timeout_seconds is None
                    or record is None
                    or not record.workspace_path
                ):
                    return latest
                progress_started_at = record.started_at or start_time
                if progress_started_at.tzinfo is None:
                    progress_started_at = progress_started_at.replace(tzinfo=UTC)
                try:
                    progress_at = await asyncio.to_thread(
                        strategy.probe_progress_at,
                        workspace_path=record.workspace_path,
                        run_id=run_id,
                        started_at=progress_started_at,
                    )
                except Exception:
                    if not progress_probe_warning_logged:
                        logger.warning(
                            "Progress probe failed for managed run %s",
                            run_id,
                            exc_info=True,
                        )
                        progress_probe_warning_logged = True
                    return latest
                if progress_at is None:
                    return latest
                if progress_at.tzinfo is None:
                    progress_at = progress_at.replace(tzinfo=UTC)
                return max(latest, progress_at)

            async def _emit_no_output_annotation(now: datetime) -> None:
                nonlocal last_no_output_annotation_at, stalled_no_progress, stalled_progress_reason
                latest_progress_at = await _latest_progress_at()
                idle_progress_seconds = max(
                    0.0,
                    (now - latest_progress_at).total_seconds(),
                )
                if (
                    progress_timeout_seconds is not None
                    and not stalled_progress_detected.is_set()
                    and idle_progress_seconds >= progress_timeout_seconds
                ):
                    stalled_no_progress = True
                    stalled_progress_reason = (
                        "Managed runtime made no observable progress for "
                        f"{int(idle_progress_seconds)}s."
                    )
                    _record_annotation(
                        annotation_type="termination_requested_stalled_progress",
                        text=(
                            "Supervisor: process termination requested after "
                            f"{int(idle_progress_seconds)}s without observable progress."
                        ),
                        reason="stalled_no_progress",
                        metadata={
                            "progress_timeout_seconds": progress_timeout_seconds,
                            "idle_progress_seconds": int(idle_progress_seconds),
                            "last_progress_at": latest_progress_at.isoformat(),
                        },
                    )
                    stalled_progress_detected.set()
                    return
                if (
                    now - last_output_seen_at
                ).total_seconds() < NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS:
                    return
                if (
                    now - last_no_output_annotation_at
                ).total_seconds() < NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS:
                    return
                _record_annotation(
                    annotation_type="no_output_interval",
                    text=(
                        "Supervisor: no stdout/stderr observed for "
                        f"{NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS}s; process still running."
                    ),
                    reason="no_output",
                )
                last_no_output_annotation_at = now

            # Run heartbeat/wait and log streaming CONCURRENTLY so that OS
            # pipe buffers are drained in real-time.  Sequential streaming
            # (heartbeat first, then stream) fills the kernel pipe buffer for
            # processes with large output, causing the subprocess write-end to
            # block indefinitely — a deadlock.  Concurrent streaming also means
            # output is captured as it is produced, enabling true live output.
            heartbeat_task = asyncio.create_task(
                self._heartbeat_and_wait_with_timeout(
                    run_id,
                    process,
                    timeout_seconds,
                    no_output_callback=_emit_no_output_annotation,
                )
            )
            stream_task = asyncio.create_task(
                self._log_streamer.stream_and_parse(
                    process.stdout,
                    process.stderr,
                    run_id=run_id,
                    workspace_path=record.workspace_path if record else None,
                    parser=parser,
                    chunk_callback=_handle_stream_chunk,
                    event_callback=_handle_stream_events,
                )
            )
            terminate_on_rate_limit_task = None
            if strategy is not None and strategy.terminate_on_live_rate_limit():
                terminate_on_rate_limit_task = asyncio.create_task(
                    self._terminate_on_signal(
                        process=process,
                        trigger=live_rate_limit_detected,
                    )
                )
            terminate_on_stall_task = None
            if progress_timeout_seconds is not None:
                terminate_on_stall_task = asyncio.create_task(
                    self._terminate_on_signal(
                        process=process,
                        trigger=stalled_progress_detected,
                    )
                )
            (
                (process_exit_code, timed_out),
                (log_refs, stdout_content, stderr_content, parsed_output, events),
            ) = await asyncio.gather(heartbeat_task, stream_task)
            if terminate_on_rate_limit_task is not None:
                if live_rate_limit_detected.is_set():
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_rate_limit_task
                else:
                    terminate_on_rate_limit_task.cancel()
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_rate_limit_task
            stalled_progress_termination_performed = False
            if terminate_on_stall_task is not None:
                if stalled_progress_detected.is_set():
                    with suppress(asyncio.CancelledError):
                        stalled_progress_termination_performed = bool(
                            await terminate_on_stall_task
                        )
                else:
                    terminate_on_stall_task.cancel()
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_stall_task

            if timed_out:
                exit_code = None
                timed_out_by_supervisor = True
            else:
                if exit_code_path:
                    exit_code = self._resolve_effective_exit_code(
                        process_exit_code=process_exit_code,
                        exit_code_path=exit_code_path,
                    )
                    _record_annotation(
                        annotation_type="exit_code_resolved",
                        text=f"Supervisor: authoritative exit code resolved to {exit_code}.",
                        reason="exit_code",
                    )
                else:
                    exit_code = process_exit_code
            if timed_out_by_supervisor:
                _record_annotation(
                    annotation_type="termination_requested_timeout",
                    text="Supervisor: process termination requested after timeout.",
                    reason="timeout",
                )
            if live_rate_limit_requested:
                _record_annotation(
                    annotation_type="termination_requested_rate_limit",
                    text=(
                        "Supervisor: process termination requested due to "
                        "live rate-limit detection."
                    ),
                    reason="rate_limit",
                )
            if stalled_no_progress and stalled_progress_termination_performed:
                _record_annotation(
                    annotation_type="run_classified_stalled_progress",
                    text=(
                        "Supervisor: managed runtime stalled without progress; "
                        "classifying as failed."
                    ),
                    reason="stalled_no_progress",
                )
            elif stalled_no_progress:
                _record_annotation(
                    annotation_type="run_completed_before_stalled_progress_termination",
                    text=(
                        "Supervisor: stalled-progress threshold was reached, but the "
                        "process exited before termination completed; using normal "
                        "exit classification."
                    ),
                    reason="stalled_no_progress",
                )

            # Classify exit
            if stalled_no_progress and stalled_progress_termination_performed:
                exit_result = ManagedRuntimeExitResult(
                    status="failed",
                    failure_class="system_error",
                )
            else:
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
                if stalled_no_progress and stalled_progress_termination_performed:
                    error_message = stalled_progress_reason or (
                        "Managed runtime stalled without observable progress"
                    )
                elif runtime_id == "gemini_cli" and exit_result.provider_error_code == "429":
                    error_message = "Gemini API rate limit exceeded"
                else:
                    error_message = f"Process exited with code {exit_code}"
                    if parsed_output.error_messages:
                        error_message += f": {parsed_output.error_messages[0]}"
            elif status == "timed_out":
                error_message = (
                    f"Process timed out after {timeout_seconds}s"
                )
            if status == "completed":
                _record_annotation(
                    annotation_type="run_classified_completed",
                    text="Supervisor: run classified as completed.",
                    reason="classification",
                )
            elif status == "timed_out":
                _record_annotation(
                    annotation_type="run_classified_timed_out",
                    text="Supervisor: run classified as timed_out.",
                    reason="classification",
                )
            else:
                _record_annotation(
                    annotation_type="run_classified_failed",
                    text=(
                        f"Supervisor: run classified as failed "
                        f"({failure_class or 'unknown'})."
                    ),
                    reason="classification",
                    metadata={"failure_class": failure_class},
                )
            duration = (datetime.now(tz=UTC) - start_time).total_seconds()
            _record_annotation(
                annotation_type="diagnostics_collection_started",
                text="Supervisor: persisting diagnostics bundle.",
                reason="diagnostics",
            )
            annotations = self._log_streamer.consume_annotations(run_id)

            diagnostics_ref = self._log_streamer.collect_diagnostics(
                run_id=run_id,
                exit_code=exit_code,
                duration_seconds=duration,
                log_refs=log_refs,
                annotations=annotations,
                parsed_output=parsed_output,
                events=events,
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
            self._log_streamer.consume_annotations(run_id)
            self._active_processes.pop(run_id, None)
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))

    async def _heartbeat_and_wait(
        self,
        run_id: str,
        process: asyncio.subprocess.Process,
        no_output_callback: Callable[[datetime], Awaitable[None]] | None = None,
    ) -> int:
        """Send heartbeats while waiting for the process to complete."""
        while True:
            try:
                exit_code = await asyncio.wait_for(
                    process.wait(), timeout=HEARTBEAT_INTERVAL
                )
                return exit_code
            except asyncio.TimeoutError:
                if no_output_callback is not None:
                    await no_output_callback(datetime.now(tz=UTC))
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
        no_output_callback: Callable[[datetime], Awaitable[None]] | None = None,
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
                self._heartbeat_and_wait(
                    run_id,
                    process,
                    no_output_callback=no_output_callback,
                ),
                timeout=timeout_seconds,
            )
            return exit_code, False
        except asyncio.TimeoutError:
            # Terminate the process so the concurrent streaming task sees EOF
            # and can complete, allowing asyncio.gather() to unblock.
            await self._terminate_process(process)
            if no_output_callback is not None:
                await no_output_callback(datetime.now(tz=UTC))
            return None, True

    async def cancel(self, run_id: str) -> None:
        """Cancel a running managed process: terminate -> wait -> kill."""
        process = self._active_processes.get(run_id)
        record = self._store.load(run_id)

        def _record_cancel_annotation() -> None:
            self._log_streamer.emit_system_annotation(
                run_id=run_id,
                workspace_path=record.workspace_path if record else None,
                text="Supervisor: process termination requested due to operator cancel.",
                annotation_type="termination_requested_cancel",
                metadata={"source": "supervisor", "reason": "operator_cancel"},
            )

        if process is None:
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
            self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))
            _record_cancel_annotation()
            self._store.update_status(
                run_id,
                "canceled",
                finished_at=datetime.now(tz=UTC),
                error_message="Cancelled (process not found in supervisor)",
            )
            self._log_streamer.consume_annotations(run_id)
            return

        _record_cancel_annotation()
        await self._terminate_process(process)
        self._active_processes.pop(run_id, None)
        self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
        self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))
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
                self._cleanup_runtime_files(self._cleanup_paths.pop(record.run_id, ()))
                self._cleanup_runtime_files(
                    self._deferred_cleanup_paths.pop(record.run_id, ())
                )
                reconciled.append(updated)

        return reconciled

    def cleanup_deferred_run_files(self, run_id: str) -> None:
        """Best-effort cleanup for runtime files needed after process exit."""
        self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))

    @staticmethod
    async def _terminate_on_signal(
        *,
        process: asyncio.subprocess.Process,
        trigger: asyncio.Event,
    ) -> bool:
        await trigger.wait()
        if process.returncode is not None:
            return False
        await ManagedRunSupervisor._terminate_process(process)
        return True

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
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
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
