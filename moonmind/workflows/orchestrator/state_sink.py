"""State sink abstractions for orchestrator runtime execution state."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from api_service.db import models as db_models
from api_service.db.base import get_async_session_context
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.storage import ArtifactStorage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrchestratorStateSink(ABC):
    """Abstract sink for orchestrator task + step execution state."""

    @abstractmethod
    async def record_task_status(
        self,
        *,
        task_id: UUID,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
    ) -> None:
        """Record top-level task status transition."""

    @abstractmethod
    async def record_step_status(
        self,
        *,
        task_id: UUID,
        step_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record one step status transition."""

    @abstractmethod
    async def record_artifact(
        self,
        *,
        task_id: UUID,
        path: str,
        artifact_type: str,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        """Record one artifact reference."""

    @abstractmethod
    async def flush(self) -> None:
        """Flush sink buffers where applicable."""


class DbStateSink(OrchestratorStateSink):
    """State sink backed by SQLAlchemy orchestrator repositories."""

    async def record_task_status(
        self,
        *,
        task_id: UUID,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
    ) -> None:
        del message
        async with get_async_session_context() as session:
            repo = OrchestratorRepository(session)
            run = await repo.get_run(task_id)
            if run is None:
                return
            run_status = db_models.OrchestratorRunStatus(status)
            await repo.update_run(
                run,
                status=run_status,
                started_at=started_at,
                completed_at=finished_at,
            )
            await repo.commit()

    async def record_step_status(
        self,
        *,
        task_id: UUID,
        step_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        del metadata
        async with get_async_session_context() as session:
            repo = OrchestratorRepository(session)
            step = await repo.get_task_step(run_id=task_id, step_id=step_id)
            if step is None:
                return
            await repo.update_task_step_state(
                step,
                status=db_models.OrchestratorTaskStepStatus(status),
                message=message,
                started_at=started_at,
                finished_at=finished_at,
                artifact_refs=artifact_refs,
            )
            await repo.commit()

    async def record_artifact(
        self,
        *,
        task_id: UUID,
        path: str,
        artifact_type: str,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        async with get_async_session_context() as session:
            repo = OrchestratorRepository(session)
            await repo.add_artifact(
                run_id=task_id,
                artifact_type=db_models.OrchestratorRunArtifactType(artifact_type),
                path=path,
                checksum=checksum,
                size_bytes=size_bytes,
            )
            await repo.commit()

    async def flush(self) -> None:
        return


class ArtifactStateSink(OrchestratorStateSink):
    """File-backed JSONL sink used when DB persistence is unavailable."""

    def __init__(self, *, storage: ArtifactStorage) -> None:
        self._storage = storage

    def _state_log_path(self, task_id: UUID) -> Path:
        return self._storage.ensure_run_directory(task_id) / "state-snapshots.jsonl"

    def _append(self, task_id: UUID, payload: Mapping[str, Any]) -> None:
        line = json.dumps(
            {
                "recordedAt": _utcnow().isoformat(),
                **dict(payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        log_path = self._state_log_path(task_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    async def record_task_status(
        self,
        *,
        task_id: UUID,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
    ) -> None:
        self._append(
            task_id,
            {
                "type": "task_status",
                "taskId": str(task_id),
                "status": status,
                "startedAt": started_at.isoformat() if started_at else None,
                "finishedAt": finished_at.isoformat() if finished_at else None,
                "message": message,
            },
        )

    async def record_step_status(
        self,
        *,
        task_id: UUID,
        step_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._append(
            task_id,
            {
                "type": "step_status",
                "taskId": str(task_id),
                "stepId": step_id,
                "status": status,
                "startedAt": started_at.isoformat() if started_at else None,
                "finishedAt": finished_at.isoformat() if finished_at else None,
                "message": message,
                "artifactRefs": list(artifact_refs or []),
                "metadata": dict(metadata or {}),
            },
        )

    async def record_artifact(
        self,
        *,
        task_id: UUID,
        path: str,
        artifact_type: str,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        self._append(
            task_id,
            {
                "type": "artifact",
                "taskId": str(task_id),
                "path": path,
                "artifactType": artifact_type,
                "checksum": checksum,
                "sizeBytes": size_bytes,
            },
        )

    async def flush(self) -> None:
        return


class FallbackStateSink(OrchestratorStateSink):
    """Primary DB sink with artifact fallback when DB writes fail."""

    def __init__(
        self,
        *,
        db_sink: OrchestratorStateSink,
        artifact_sink: OrchestratorStateSink,
    ) -> None:
        self._db_sink = db_sink
        self._artifact_sink = artifact_sink
        self._db_available = True

    async def _run(
        self,
        db_call,
        fallback_call,
    ) -> None:
        if self._db_available:
            try:
                await db_call()
                return
            except Exception:
                self._db_available = False
        await fallback_call()

    async def record_task_status(
        self,
        *,
        task_id: UUID,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
    ) -> None:
        await self._run(
            lambda: self._db_sink.record_task_status(
                task_id=task_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                message=message,
            ),
            lambda: self._artifact_sink.record_task_status(
                task_id=task_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                message=message,
            ),
        )

    async def record_step_status(
        self,
        *,
        task_id: UUID,
        step_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        message: str | None = None,
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._run(
            lambda: self._db_sink.record_step_status(
                task_id=task_id,
                step_id=step_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                message=message,
                artifact_refs=artifact_refs,
                metadata=metadata,
            ),
            lambda: self._artifact_sink.record_step_status(
                task_id=task_id,
                step_id=step_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                message=message,
                artifact_refs=artifact_refs,
                metadata=metadata,
            ),
        )

    async def record_artifact(
        self,
        *,
        task_id: UUID,
        path: str,
        artifact_type: str,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        await self._run(
            lambda: self._db_sink.record_artifact(
                task_id=task_id,
                path=path,
                artifact_type=artifact_type,
                checksum=checksum,
                size_bytes=size_bytes,
            ),
            lambda: self._artifact_sink.record_artifact(
                task_id=task_id,
                path=path,
                artifact_type=artifact_type,
                checksum=checksum,
                size_bytes=size_bytes,
            ),
        )

    async def flush(self) -> None:
        await self._artifact_sink.flush()


__all__ = [
    "ArtifactStateSink",
    "DbStateSink",
    "FallbackStateSink",
    "OrchestratorStateSink",
]
