"""Session-level supervision for durable Codex managed session records."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord

from .log_streamer import RuntimeLogStreamer
from .managed_session_store import ManagedSessionStore


class ArtifactStorageWriter(Protocol):
    def write_artifact(
        self,
        *,
        job_id: str,
        artifact_name: str,
        data: bytes,
    ) -> tuple[Path, str]:
        pass


class ManagedSessionSupervisor:
    """Track session spool progress and publish durable observability artifacts."""

    def __init__(
        self,
        *,
        store: ManagedSessionStore,
        log_streamer: RuntimeLogStreamer,
        artifact_storage: ArtifactStorageWriter,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._log_streamer = log_streamer
        self._artifact_storage = artifact_storage
        self._poll_interval_seconds = poll_interval_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    @staticmethod
    def _stdout_path(record: CodexManagedSessionRecord) -> Path:
        return Path(record.artifact_spool_path) / "stdout.log"

    @staticmethod
    def _stderr_path(record: CodexManagedSessionRecord) -> Path:
        return Path(record.artifact_spool_path) / "stderr.log"

    @staticmethod
    def _combined_offset(record: CodexManagedSessionRecord) -> int:
        total = 0
        for path in (
            ManagedSessionSupervisor._stdout_path(record),
            ManagedSessionSupervisor._stderr_path(record),
        ):
            if path.exists():
                total += path.stat().st_size
        return total

    async def _watch(self, session_id: str) -> None:
        stop_event = self._stop_events[session_id]
        while not stop_event.is_set():
            record = self._store.load(session_id)
            if record is None:
                return
            combined_offset = self._combined_offset(record)
            if combined_offset != (record.last_log_offset or 0):
                await self._store.update(
                    session_id,
                    last_log_offset=combined_offset,
                    last_log_at=datetime.now(tz=UTC),
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def start(self, record: CodexManagedSessionRecord) -> None:
        existing = self._tasks.get(record.session_id)
        if existing is not None and not existing.done():
            return
        stop_event = asyncio.Event()
        self._stop_events[record.session_id] = stop_event
        self._tasks[record.session_id] = asyncio.create_task(
            self._watch(record.session_id)
        )

    @staticmethod
    def _read_spool_bytes(record: CodexManagedSessionRecord) -> tuple[bytes, bytes]:
        stdout_bytes = b""
        stderr_bytes = b""
        stdout_path = ManagedSessionSupervisor._stdout_path(record)
        stderr_path = ManagedSessionSupervisor._stderr_path(record)
        if stdout_path.exists():
            stdout_bytes = stdout_path.read_bytes()
        if stderr_path.exists():
            stderr_bytes = stderr_path.read_bytes()
        return stdout_bytes, stderr_bytes

    def _write_json_artifact(
        self,
        *,
        job_id: str,
        artifact_name: str,
        payload: dict[str, object],
    ) -> str:
        _path, ref = self._artifact_storage.write_artifact(
            job_id=job_id,
            artifact_name=artifact_name,
            data=(json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return ref

    async def _publish_record(
        self,
        record: CodexManagedSessionRecord,
        *,
        status: str,
        error_message: str | None,
    ) -> CodexManagedSessionRecord:
        stdout_bytes, stderr_bytes = self._read_spool_bytes(record)
        _, stdout_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name="stdout.log",
            data=stdout_bytes,
        )
        _, stderr_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name="stderr.log",
            data=stderr_bytes,
        )
        diagnostics_ref = self._log_streamer.collect_diagnostics(
            run_id=record.session_id,
            exit_code=None,
            duration_seconds=0.0,
            log_refs={"stdout": stdout_ref, "stderr": stderr_ref},
            annotations=[],
            events=[],
        )
        summary_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.summary.json",
            payload={
                "sessionId": record.session_id,
                "sessionEpoch": record.session_epoch,
                "containerId": record.container_id,
                "threadId": record.thread_id,
                "status": status,
                "stdoutArtifactRef": stdout_ref,
                "stderrArtifactRef": stderr_ref,
                "diagnosticsRef": diagnostics_ref,
                "errorMessage": error_message,
            },
        )
        checkpoint_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.step_checkpoint.json",
            payload={
                "sessionState": record.session_state().model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
                "status": status,
                "runtimeArtifacts": {
                    "stdout": stdout_ref,
                    "stderr": stderr_ref,
                    "diagnostics": diagnostics_ref,
                },
                "recordUpdatedAt": datetime.now(tz=UTC).isoformat(),
            },
        )
        now = datetime.now(tz=UTC)
        return await self._store.update(
            record.session_id,
            status=status,
            stdout_artifact_ref=stdout_ref,
            stderr_artifact_ref=stderr_ref,
            diagnostics_ref=diagnostics_ref,
            latest_summary_ref=summary_ref,
            latest_checkpoint_ref=checkpoint_ref,
            last_log_offset=len(stdout_bytes) + len(stderr_bytes),
            last_log_at=now,
            updated_at=now,
            error_message=error_message,
        )

    async def publish_snapshot(self, session_id: str) -> CodexManagedSessionRecord:
        record = self._store.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        return await self._publish_record(
            record,
            status=record.status,
            error_message=record.error_message,
        )

    async def finalize(
        self,
        session_id: str,
        *,
        status: str,
        error_message: str | None = None,
    ) -> CodexManagedSessionRecord:
        stop_event = self._stop_events.pop(session_id, None)
        if stop_event is not None:
            stop_event.set()
        task = self._tasks.pop(session_id, None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

        record = self._store.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        return await self._publish_record(
            record,
            status=status,
            error_message=error_message,
        )

    async def publish_reset_artifacts(
        self,
        session_id: str,
        *,
        control_event: dict[str, object],
        reset_boundary: dict[str, object],
    ) -> CodexManagedSessionRecord:
        record = self._store.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        control_ref = self._write_json_artifact(
            job_id=session_id,
            artifact_name="session.control_event.json",
            payload=control_event,
        )
        reset_ref = self._write_json_artifact(
            job_id=session_id,
            artifact_name="session.reset_boundary.json",
            payload=reset_boundary,
        )
        return await self._store.update(
            session_id,
            latest_control_event_ref=control_ref,
            latest_reset_boundary_ref=reset_ref,
            updated_at=datetime.now(tz=UTC),
        )
