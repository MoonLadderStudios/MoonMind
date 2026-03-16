"""Managed run supervision: heartbeats, timeout, exit classification, cancellation."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import UTC, datetime

from moonmind.schemas.agent_runtime_models import (
    AgentRunState,
    FailureClass,
    ManagedRunRecord,
)

from .log_streamer import RuntimeLogStreamer
from .store import ManagedRunStore

HEARTBEAT_INTERVAL = 30  # seconds
GRACEFUL_TERMINATE_WAIT_SECONDS = 2.0  # seconds to wait for graceful SIGTERM before SIGKILL


class ManagedRunSupervisor:
    """Supervises managed agent subprocess lifecycle."""

    def __init__(
        self,
        store: ManagedRunStore,
        log_streamer: RuntimeLogStreamer,
    ) -> None:
        self._store = store
        self._log_streamer = log_streamer
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    async def supervise(
        self,
        *,
        run_id: str,
        process: asyncio.subprocess.Process,
        timeout_seconds: int = 3600,
    ) -> ManagedRunRecord:
        """Supervise a running process: stream logs, heartbeat, handle completion/timeout."""
        self._active_processes[run_id] = process
        self._store.update_status(run_id, "running")
        start_time = datetime.now(tz=UTC)

        try:
            # Start log streaming tasks
            stdout_task = asyncio.create_task(
                self._log_streamer.stream_to_artifact(
                    process.stdout, run_id=run_id, stream_name="stdout"
                )
            ) if process.stdout else None
            stderr_task = asyncio.create_task(
                self._log_streamer.stream_to_artifact(
                    process.stderr, run_id=run_id, stream_name="stderr"
                )
            ) if process.stderr else None

            # Heartbeat + wait for process with timeout
            try:
                exit_code = await asyncio.wait_for(
                    self._heartbeat_and_wait(run_id, process),
                    timeout=timeout_seconds,
                )
                timed_out = False
            except asyncio.TimeoutError:
                exit_code = None
                timed_out = True
                await self._terminate_process(process)

            # Collect log refs
            log_refs: dict[str, str] = {}
            if stdout_task:
                log_refs["stdout"] = await stdout_task
            if stderr_task:
                log_refs["stderr"] = await stderr_task

            duration = (datetime.now(tz=UTC) - start_time).total_seconds()

            diagnostics_ref = self._log_streamer.collect_diagnostics(
                run_id=run_id,
                exit_code=exit_code,
                duration_seconds=duration,
                log_refs=log_refs,
            )

            # Classify exit
            status, failure_class = self._classify_exit(
                exit_code=exit_code, timed_out=timed_out
            )

            error_message = None
            if status == "failed":
                error_message = f"Process exited with code {exit_code}"
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
                log_artifact_ref=log_refs.get("stdout"),
                failure_class=failure_class,
                error_message=error_message,
            )
            return record
        finally:
            self._active_processes.pop(run_id, None)

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
                self._store.update_status(
                    run_id,
                    "running",
                    last_heartbeat_at=datetime.now(tz=UTC),
                )

    async def cancel(self, run_id: str) -> None:
        """Cancel a running managed process: terminate -> wait -> kill."""
        process = self._active_processes.get(run_id)
        if process is None:
            self._store.update_status(
                run_id,
                "cancelled",
                finished_at=datetime.now(tz=UTC),
                error_message="Cancelled (process not found in supervisor)",
            )
            return

        await self._terminate_process(process)
        self._active_processes.pop(run_id, None)
        self._store.update_status(
            run_id,
            "cancelled",
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
                    error_message=f"Process {record.pid} not found during reconciliation",
                )
                reconciled.append(updated)
        return reconciled

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait(2s) -> kill sequence."""
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=GRACEFUL_TERMINATE_WAIT_SECONDS)
        except (asyncio.TimeoutError, ProcessLookupError):
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.wait()

    @staticmethod
    def _classify_exit(
        *, exit_code: int | None, timed_out: bool
    ) -> tuple[AgentRunState, FailureClass | None]:
        """Classify process exit into a run state and optional failure class."""
        if timed_out:
            return "timed_out", "execution_error"
        if exit_code == 0:
            return "completed", None
        return "failed", "execution_error"

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
