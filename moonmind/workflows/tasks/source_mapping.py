"""Task source mapping persistence and canonical resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE

TaskSource = Literal["queue", "orchestrator", "temporal"]


class TaskResolutionNotFoundError(RuntimeError):
    """Raised when a task cannot be resolved to a source."""


class TaskResolutionAmbiguousError(RuntimeError):
    """Raised when a task matches multiple sources without a canonical mapping."""

    def __init__(self, task_id: str, sources: set[str]) -> None:
        self.task_id = task_id
        self.sources = set(sources)
        joined = ", ".join(sorted(self.sources))
        super().__init__(
            f"Task {task_id} matches multiple execution sources: {joined}. "
            "Retry with an explicit source hint."
        )


@dataclass(slots=True)
class ResolvedTaskSource:
    """Canonical source resolution result."""

    task_id: str
    source: TaskSource
    entry: str | None
    source_record_id: str
    workflow_id: str | None
    owner_type: str | None
    owner_id: str | None


class TaskSourceMappingService:
    """Canonical read/write access for persisted task source mappings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_task(
        self,
        *,
        task_id: str,
        source_hint: TaskSource | None = None,
        user: db_models.User | None = None,
    ) -> ResolvedTaskSource:
        mapping = await self._session.get(db_models.TaskSourceMapping, task_id)
        if mapping is not None:
            if source_hint and mapping.source != source_hint:
                raise TaskResolutionNotFoundError(
                    f"Task {task_id} was not found in source '{source_hint}'."
                )
            return self._serialize_mapping(mapping)

        matches = await self._probe_backing_records(task_id=task_id, user=user)
        if source_hint is not None:
            candidate = matches.get(source_hint)
            if candidate is None:
                raise TaskResolutionNotFoundError(
                    f"Task {task_id} was not found in source '{source_hint}'."
                )
            return candidate

        if not matches:
            raise TaskResolutionNotFoundError(f"Task {task_id} was not found.")
        if len(matches) > 1:
            raise TaskResolutionAmbiguousError(task_id, set(matches))
        return next(iter(matches.values()))

    async def upsert_queue_job(
        self,
        job: queue_models.AgentJob,
    ) -> ResolvedTaskSource:
        owner_id = (
            str(getattr(job, "created_by_user_id", None))
            if getattr(job, "created_by_user_id", None) is not None
            else (
                str(getattr(job, "requested_by_user_id", None))
                if getattr(job, "requested_by_user_id", None) is not None
                else None
            )
        )
        mapping = await self.upsert_mapping(
            task_id=str(job.id),
            source="queue",
            entry=(
                "manifest" if getattr(job, "type", None) == MANIFEST_JOB_TYPE else "run"
            ),
            source_record_id=str(job.id),
            workflow_id=None,
            owner_type="user" if owner_id else None,
            owner_id=owner_id,
        )
        return self._serialize_mapping(mapping)

    async def upsert_orchestrator_run(
        self,
        run: db_models.OrchestratorRun,
    ) -> ResolvedTaskSource:
        mapping = await self.upsert_mapping(
            task_id=str(run.id),
            source="orchestrator",
            entry=None,
            source_record_id=str(run.id),
            workflow_id=None,
            owner_type=None,
            owner_id=None,
        )
        return self._serialize_mapping(mapping)

    async def upsert_temporal_execution(
        self,
        record: db_models.TemporalExecutionRecord,
    ) -> ResolvedTaskSource:
        attrs = dict(getattr(record, "search_attributes", None) or {})
        owner_type = str(attrs.get("mm_owner_type") or "").strip().lower() or None
        owner_id = (
            str(
                attrs.get("mm_owner_id") or getattr(record, "owner_id", None) or ""
            ).strip()
            or None
        )
        mapping = await self.upsert_mapping(
            task_id=record.workflow_id,
            source="temporal",
            entry=str(getattr(record, "entry", None) or "").strip().lower() or None,
            source_record_id=record.workflow_id,
            workflow_id=record.workflow_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        return self._serialize_mapping(mapping)

    async def upsert_mapping(
        self,
        *,
        task_id: str,
        source: TaskSource,
        entry: str | None,
        source_record_id: str,
        workflow_id: str | None,
        owner_type: str | None,
        owner_id: str | None,
    ) -> db_models.TaskSourceMapping:
        if not hasattr(self._session, "add") or not hasattr(self._session, "flush"):
            return db_models.TaskSourceMapping(
                task_id=task_id,
                source=source,
                entry=entry,
                source_record_id=source_record_id,
                workflow_id=workflow_id,
                owner_type=owner_type,
                owner_id=owner_id,
            )

        mapping = await self._session.get(db_models.TaskSourceMapping, task_id)
        if mapping is None:
            mapping = db_models.TaskSourceMapping(
                task_id=task_id,
                source=source,
                entry=entry,
                source_record_id=source_record_id,
                workflow_id=workflow_id,
                owner_type=owner_type,
                owner_id=owner_id,
            )
            self._session.add(mapping)
        else:
            mapping.source = source
            mapping.entry = entry
            mapping.source_record_id = source_record_id
            mapping.workflow_id = workflow_id
            mapping.owner_type = owner_type
            mapping.owner_id = owner_id
        await self._session.flush()
        return mapping

    async def _probe_backing_records(
        self,
        *,
        task_id: str,
        user: db_models.User | None,
    ) -> dict[TaskSource, ResolvedTaskSource]:
        matches: dict[TaskSource, ResolvedTaskSource] = {}

        temporal_record = await self._session.get(
            db_models.TemporalExecutionRecord, task_id
        )
        if temporal_record is not None and self._is_temporal_visible(
            temporal_record, user
        ):
            matches["temporal"] = await self.upsert_temporal_execution(temporal_record)

        parsed_uuid = self._parse_uuid(task_id)
        if parsed_uuid is None:
            return matches

        queue_job = await self._session.get(queue_models.AgentJob, parsed_uuid)
        if queue_job is not None:
            matches["queue"] = await self.upsert_queue_job(queue_job)

        orchestrator_run = await self._session.get(
            db_models.OrchestratorRun, parsed_uuid
        )
        if orchestrator_run is not None:
            matches["orchestrator"] = await self.upsert_orchestrator_run(
                orchestrator_run
            )
        return matches

    def _serialize_mapping(
        self,
        mapping: db_models.TaskSourceMapping,
    ) -> ResolvedTaskSource:
        return ResolvedTaskSource(
            task_id=str(mapping.task_id),
            source=mapping.source,
            entry=mapping.entry,
            source_record_id=mapping.source_record_id,
            workflow_id=mapping.workflow_id,
            owner_type=mapping.owner_type,
            owner_id=mapping.owner_id,
        )

    def _is_temporal_visible(
        self,
        record: db_models.TemporalExecutionRecord,
        user: db_models.User | None,
    ) -> bool:
        if user is None:
            return True
        if bool(getattr(user, "is_superuser", False)):
            return True
        record_owner = str(record.owner_id or "").strip()
        user_id = str(getattr(user, "id", "") or "").strip()
        return bool(record_owner) and record_owner == user_id

    def _parse_uuid(self, raw: str) -> UUID | None:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            return None


async def list_task_source_mappings(
    session: AsyncSession,
) -> list[db_models.TaskSourceMapping]:
    """Return all persisted task source mappings for inspection/tests."""

    result = await session.execute(select(db_models.TaskSourceMapping))
    return list(result.scalars().all())
