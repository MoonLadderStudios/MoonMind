"""Compatibility helpers for unified task list/detail APIs."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.schemas.task_compatibility_models import (
    TaskActionAvailability,
    TaskCompatibilityDetail,
    TaskCompatibilityListResponse,
    TaskCompatibilityRow,
    TaskDebugContext,
)
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE
from moonmind.workflows.tasks.source_mapping import TaskSourceMappingService

TaskSourceFilter = Literal["queue", "orchestrator", "temporal", "all"] | None
TaskStatusFilter = Literal[
    "queued",
    "running",
    "awaiting_action",
    "succeeded",
    "failed",
    "cancelled",
] | None

_TEMPORAL_STATUS_MAP: dict[db_models.MoonMindWorkflowState, str] = {
    db_models.MoonMindWorkflowState.INITIALIZING: "queued",
    db_models.MoonMindWorkflowState.PLANNING: "running",
    db_models.MoonMindWorkflowState.EXECUTING: "running",
    db_models.MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    db_models.MoonMindWorkflowState.FINALIZING: "running",
    db_models.MoonMindWorkflowState.SUCCEEDED: "succeeded",
    db_models.MoonMindWorkflowState.FAILED: "failed",
    db_models.MoonMindWorkflowState.CANCELED: "cancelled",
}
_ORCHESTRATOR_STATUS_MAP: dict[db_models.OrchestratorRunStatus, str] = {
    db_models.OrchestratorRunStatus.PENDING: "queued",
    db_models.OrchestratorRunStatus.RUNNING: "running",
    db_models.OrchestratorRunStatus.AWAITING_APPROVAL: "awaiting_action",
    db_models.OrchestratorRunStatus.SUCCEEDED: "succeeded",
    db_models.OrchestratorRunStatus.ROLLED_BACK: "succeeded",
    db_models.OrchestratorRunStatus.FAILED: "failed",
}
_QUEUE_STATUS_MAP: dict[queue_models.AgentJobStatus, str] = {
    queue_models.AgentJobStatus.QUEUED: "queued",
    queue_models.AgentJobStatus.RUNNING: "running",
    queue_models.AgentJobStatus.SUCCEEDED: "succeeded",
    queue_models.AgentJobStatus.FAILED: "failed",
    queue_models.AgentJobStatus.CANCELLED: "cancelled",
    queue_models.AgentJobStatus.DEAD_LETTER: "failed",
}
_ALLOWED_SEARCH_ATTRIBUTE_KEYS = {
    "mm_owner_type",
    "mm_owner_id",
    "mm_state",
    "mm_updated_at",
    "mm_entry",
    "mm_repo",
    "mm_integration",
}
_ALLOWED_MEMO_KEYS = {
    "title",
    "summary",
    "input_ref",
    "plan_ref",
    "manifest_ref",
    "waiting_reason",
    "attention_required",
}
_MAX_METADATA_VALUE_LENGTH = 512
_MAX_PARAMETER_PREVIEW_ITEMS = 10


@dataclass(slots=True)
class _ListCursor:
    offset: int
    page_size: int
    source: str
    entry: str | None
    workflow_type: str | None
    status_filter: str | None
    owner_type: str | None
    owner_id: str | None


class TaskCompatibilityService:
    """Source-agnostic task compatibility list/detail builder."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._source_mappings = TaskSourceMappingService(session)

    async def list_tasks(
        self,
        *,
        user: db_models.User,
        source: TaskSourceFilter,
        entry: Literal["run", "manifest"] | None,
        workflow_type: str | None,
        status_filter: TaskStatusFilter,
        owner_type: Literal["user", "system", "service"] | None,
        owner_id: str | None,
        page_size: int,
        cursor: str | None,
    ) -> TaskCompatibilityListResponse:
        normalized_source = self._normalize_source(source)
        normalized_cursor = self._decode_cursor(
            cursor=cursor,
            page_size=page_size,
            source=normalized_source,
            entry=entry,
            workflow_type=workflow_type,
            status_filter=status_filter,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        rows = await self._load_rows(
            user=user,
            source=normalized_source,
            entry=entry,
            workflow_type=workflow_type,
            status_filter=status_filter,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        total_count = len(rows)
        start = normalized_cursor.offset
        end = start + page_size
        page_items = rows[start:end]
        next_cursor = None
        if end < total_count:
            next_cursor = self._encode_cursor(
                _ListCursor(
                    offset=end,
                    page_size=page_size,
                    source=normalized_source,
                    entry=entry,
                    workflow_type=workflow_type,
                    status_filter=status_filter,
                    owner_type=owner_type,
                    owner_id=owner_id,
                )
            )
        return TaskCompatibilityListResponse(
            items=page_items,
            nextCursor=next_cursor,
            count=total_count,
            countMode="exact",
        )

    async def get_task_detail(
        self,
        *,
        task_id: str,
        source_hint: Literal["queue", "orchestrator", "temporal"] | None,
        user: db_models.User,
    ) -> TaskCompatibilityDetail:
        resolved = await self._source_mappings.resolve_task(
            task_id=task_id,
            source_hint=source_hint,
            user=user,
        )
        if resolved.source == "temporal":
            record = await self._session.get(
                db_models.TemporalExecutionRecord, resolved.source_record_id
            )
            if record is None:
                raise RuntimeError(f"Temporal execution {task_id} disappeared.")
            return self._build_temporal_detail(record, user)
        if resolved.source == "queue":
            job = await self._session.get(
                queue_models.AgentJob, UUID(resolved.source_record_id)
            )
            if job is None:
                raise RuntimeError(f"Queue task {task_id} disappeared.")
            return self._build_queue_detail(job)
        run = await self._session.get(
            db_models.OrchestratorRun, UUID(resolved.source_record_id)
        )
        if run is None:
            raise RuntimeError(f"Orchestrator task {task_id} disappeared.")
        return self._build_orchestrator_detail(run)

    async def _load_rows(
        self,
        *,
        user: db_models.User,
        source: Literal["queue", "orchestrator", "temporal", "all"],
        entry: Literal["run", "manifest"] | None,
        workflow_type: str | None,
        status_filter: TaskStatusFilter,
        owner_type: Literal["user", "system", "service"] | None,
        owner_id: str | None,
    ) -> list[TaskCompatibilityRow]:
        rows: list[TaskCompatibilityRow] = []
        if source in {"all", "queue"}:
            rows.extend(
                await self._load_queue_rows(
                    entry=entry,
                    status_filter=status_filter,
                    owner_id=owner_id,
                )
            )
        if source in {"all", "orchestrator"}:
            rows.extend(
                await self._load_orchestrator_rows(
                    entry=entry,
                    status_filter=status_filter,
                )
            )
        if source in {"all", "temporal"}:
            rows.extend(
                await self._load_temporal_rows(
                    user=user,
                    entry=entry,
                    workflow_type=workflow_type,
                    status_filter=status_filter,
                    owner_type=owner_type,
                    owner_id=owner_id,
                )
            )
        rows.sort(
            key=lambda row: (row.updated_at, row.created_at, row.task_id),
            reverse=True,
        )
        return rows

    async def _load_queue_rows(
        self,
        *,
        entry: Literal["run", "manifest"] | None,
        status_filter: TaskStatusFilter,
        owner_id: str | None,
    ) -> list[TaskCompatibilityRow]:
        stmt = select(queue_models.AgentJob)
        if entry == "manifest":
            stmt = stmt.where(queue_models.AgentJob.type == MANIFEST_JOB_TYPE)
        elif entry == "run":
            stmt = stmt.where(queue_models.AgentJob.type != MANIFEST_JOB_TYPE)
        jobs = list((await self._session.execute(stmt)).scalars().all())
        normalized: list[TaskCompatibilityRow] = []
        for job in jobs:
            row = self._build_queue_row(job)
            if status_filter and row.status != status_filter:
                continue
            if owner_id and str(row.owner_id or "").strip() != str(owner_id).strip():
                continue
            await self._source_mappings.upsert_queue_job(job)
            normalized.append(row)
        return normalized

    async def _load_orchestrator_rows(
        self,
        *,
        entry: Literal["run", "manifest"] | None,
        status_filter: TaskStatusFilter,
    ) -> list[TaskCompatibilityRow]:
        if entry == "manifest":
            return []
        runs = list((await self._session.execute(select(db_models.OrchestratorRun))).scalars().all())
        normalized: list[TaskCompatibilityRow] = []
        for run in runs:
            row = self._build_orchestrator_row(run)
            if status_filter and row.status != status_filter:
                continue
            await self._source_mappings.upsert_orchestrator_run(run)
            normalized.append(row)
        return normalized

    async def _load_temporal_rows(
        self,
        *,
        user: db_models.User,
        entry: Literal["run", "manifest"] | None,
        workflow_type: str | None,
        status_filter: TaskStatusFilter,
        owner_type: Literal["user", "system", "service"] | None,
        owner_id: str | None,
    ) -> list[TaskCompatibilityRow]:
        stmt = select(db_models.TemporalExecutionRecord)
        if entry:
            stmt = stmt.where(db_models.TemporalExecutionRecord.entry == entry)
        if workflow_type:
            stmt = stmt.where(db_models.TemporalExecutionRecord.workflow_type == workflow_type)
        if not bool(getattr(user, "is_superuser", False)):
            stmt = stmt.where(db_models.TemporalExecutionRecord.owner_id == str(user.id))
        elif owner_id:
            stmt = stmt.where(db_models.TemporalExecutionRecord.owner_id == str(owner_id))
        records = list((await self._session.execute(stmt)).scalars().all())
        normalized: list[TaskCompatibilityRow] = []
        for record in records:
            row = self._build_temporal_row(record)
            if status_filter and row.status != status_filter:
                continue
            if owner_type and row.owner_type != owner_type:
                continue
            if owner_id and str(row.owner_id or "").strip() != str(owner_id).strip():
                continue
            await self._source_mappings.upsert_temporal_execution(record)
            normalized.append(row)
        return normalized

    def _build_queue_row(self, job: queue_models.AgentJob) -> TaskCompatibilityRow:
        payload = dict(job.payload or {})
        task_node = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        instructions = ""
        if isinstance(task_node, dict):
            instructions = str(task_node.get("instructions") or "").strip()
        if not instructions:
            instructions = str(payload.get("instruction") or "").strip()
        title = self._summarize_text(instructions) or job.type
        owner_id = job.created_by_user_id or job.requested_by_user_id
        return TaskCompatibilityRow(
            taskId=str(job.id),
            source="queue",
            entry="manifest" if job.type == MANIFEST_JOB_TYPE else "run",
            title=title,
            summary=instructions or None,
            status=_QUEUE_STATUS_MAP.get(job.status, "queued"),
            rawState=job.status.value,
            workflowId=None,
            workflowType=None,
            ownerType="user" if owner_id else None,
            ownerId=str(owner_id) if owner_id is not None else None,
            createdAt=job.created_at,
            startedAt=job.started_at or job.created_at,
            updatedAt=job.updated_at,
            closedAt=job.finished_at,
            artifactsCount=len(getattr(job, "artifacts", []) or []),
            detailHref=f"/tasks/{job.id}?source=queue",
            queueName="agent_jobs",
            runtimeMode=self._extract_runtime(payload),
            skillId=self._extract_skill(payload),
            publishMode=self._extract_publish_mode(payload),
        )

    def _build_orchestrator_row(
        self,
        run: db_models.OrchestratorRun,
    ) -> TaskCompatibilityRow:
        return TaskCompatibilityRow(
            taskId=str(run.id),
            source="orchestrator",
            entry="run",
            title=str(run.target_service or "").strip() or "Orchestrator Task",
            summary=str(run.instruction or "").strip() or None,
            status=_ORCHESTRATOR_STATUS_MAP.get(run.status, "queued"),
            rawState=run.status.value,
            workflowId=None,
            workflowType=None,
            ownerType=None,
            ownerId=None,
            createdAt=run.queued_at,
            startedAt=run.started_at or run.queued_at,
            updatedAt=run.updated_at,
            closedAt=run.completed_at,
            artifactsCount=len(run.artifacts or []),
            detailHref=f"/tasks/{run.id}?source=orchestrator",
            queueName="-",
            runtimeMode="orchestrator",
            skillId=None,
            publishMode=None,
        )

    def _build_temporal_row(
        self,
        record: db_models.TemporalExecutionRecord,
    ) -> TaskCompatibilityRow:
        search_attributes = self._sanitize_metadata(
            record.search_attributes or {},
            allowed_keys=_ALLOWED_SEARCH_ATTRIBUTE_KEYS,
        )
        memo = self._sanitize_metadata(
            record.memo or {},
            allowed_keys=_ALLOWED_MEMO_KEYS,
        )
        close_status = record.close_status.value if record.close_status else None
        if record.close_status is db_models.TemporalExecutionCloseStatus.COMPLETED:
            temporal_status = "completed"
        elif record.close_status is db_models.TemporalExecutionCloseStatus.CANCELED:
            temporal_status = "canceled"
        elif record.close_status is None:
            temporal_status = "running"
        else:
            temporal_status = "failed"
        title = str(memo.get("title") or "").strip() or record.workflow_type.value
        summary = str(memo.get("summary") or "").strip() or "Execution updated."
        return TaskCompatibilityRow(
            taskId=record.workflow_id,
            source="temporal",
            entry=str(search_attributes.get("mm_entry") or record.entry or "").strip().lower()
            or None,
            title=title,
            summary=summary,
            status=_TEMPORAL_STATUS_MAP.get(record.state, "queued"),
            rawState=record.state.value,
            temporalStatus=temporal_status,
            closeStatus=close_status,
            workflowId=record.workflow_id,
            workflowType=record.workflow_type.value,
            ownerType=str(search_attributes.get("mm_owner_type") or "").strip().lower()
            or self._default_owner_type(record),
            ownerId=str(search_attributes.get("mm_owner_id") or record.owner_id or "").strip()
            or None,
            createdAt=record.started_at,
            startedAt=record.started_at,
            updatedAt=record.updated_at,
            closedAt=record.closed_at,
            artifactsCount=len(record.artifact_refs or []),
            detailHref=f"/tasks/{record.workflow_id}",
            queueName="-",
            runtimeMode="temporal",
            skillId=None,
            publishMode=None,
        )

    def _build_queue_detail(
        self,
        job: queue_models.AgentJob,
    ) -> TaskCompatibilityDetail:
        row = self._build_queue_row(job)
        return TaskCompatibilityDetail(
            **row.model_dump(),
            artifactRefs=[],
            actions=TaskActionAvailability(cancel=row.status in {"queued", "running"}),
            debug=TaskDebugContext(),
        )

    def _build_orchestrator_detail(
        self,
        run: db_models.OrchestratorRun,
    ) -> TaskCompatibilityDetail:
        row = self._build_orchestrator_row(run)
        return TaskCompatibilityDetail(
            **row.model_dump(),
            artifactRefs=[str(artifact.id) for artifact in (run.artifacts or [])],
            actions=TaskActionAvailability(
                approve=run.status is db_models.OrchestratorRunStatus.AWAITING_APPROVAL
            ),
            debug=TaskDebugContext(),
        )

    def _build_temporal_detail(
        self,
        record: db_models.TemporalExecutionRecord,
        user: db_models.User,
    ) -> TaskCompatibilityDetail:
        row = self._build_temporal_row(record)
        memo = self._sanitize_metadata(
            record.memo or {},
            allowed_keys=_ALLOWED_MEMO_KEYS,
        )
        search_attributes = self._sanitize_metadata(
            record.search_attributes or {},
            allowed_keys=_ALLOWED_SEARCH_ATTRIBUTE_KEYS,
        )
        raw_state = record.state.value
        is_terminal = row.status in {"succeeded", "failed", "cancelled"}
        can_pause = not is_terminal and raw_state != "awaiting_external"
        can_resume = not is_terminal and raw_state == "awaiting_external"
        waiting_reason = str(memo.get("waiting_reason") or "").strip() or None
        attention_required = bool(memo.get("attention_required") or False)
        return TaskCompatibilityDetail(
            **row.model_dump(),
            namespace=record.namespace,
            temporalRunId=record.run_id,
            artifactRefs=list(record.artifact_refs or []),
            searchAttributes=search_attributes,
            memo=memo,
            inputArtifactRef=record.input_ref,
            planArtifactRef=record.plan_ref,
            manifestArtifactRef=record.manifest_ref,
            parameterPreview=self._build_parameter_preview(record.parameters or {}),
            actions=TaskActionAvailability(
                rename=not is_terminal,
                editInputs=not is_terminal,
                rerun=not is_terminal,
                approve=not is_terminal,
                pause=can_pause,
                resume=can_resume,
                deliverCallback=not is_terminal,
                cancel=not is_terminal,
                forceTerminate=bool(getattr(user, "is_superuser", False)) and not is_terminal,
            ),
            debug=TaskDebugContext(
                namespace=record.namespace,
                workflowType=record.workflow_type.value,
                workflowId=record.workflow_id,
                temporalRunId=record.run_id,
                temporalStatus=row.temporal_status,
                closeStatus=row.close_status,
                waitingReason=waiting_reason,
                attentionRequired=attention_required,
            ),
        )

    def _normalize_source(
        self,
        source: TaskSourceFilter,
    ) -> Literal["queue", "orchestrator", "temporal", "all"]:
        normalized = str(source or "all").strip().lower()
        if normalized not in {"queue", "orchestrator", "temporal", "all"}:
            raise ValueError(f"Unsupported source filter: {source}")
        return normalized  # type: ignore[return-value]

    def _encode_cursor(self, cursor: _ListCursor) -> str:
        payload = {
            "offset": cursor.offset,
            "page_size": cursor.page_size,
            "source": cursor.source,
            "entry": cursor.entry,
            "workflow_type": cursor.workflow_type,
            "status_filter": cursor.status_filter,
            "owner_type": cursor.owner_type,
            "owner_id": cursor.owner_id,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        return encoded.decode("ascii").rstrip("=")

    def _decode_cursor(
        self,
        *,
        cursor: str | None,
        page_size: int,
        source: str,
        entry: str | None,
        workflow_type: str | None,
        status_filter: str | None,
        owner_type: str | None,
        owner_id: str | None,
    ) -> _ListCursor:
        default_cursor = _ListCursor(
            offset=0,
            page_size=page_size,
            source=source,
            entry=entry,
            workflow_type=workflow_type,
            status_filter=status_filter,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        if not cursor:
            return default_cursor
        padded = cursor + "=" * (-len(cursor) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            payload = json.loads(decoded)
        except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
            raise ValueError("Invalid compatibility cursor.") from exc
        decoded_cursor = _ListCursor(
            offset=max(0, int(payload.get("offset", 0) or 0)),
            page_size=max(1, int(payload.get("page_size", page_size) or page_size)),
            source=str(payload.get("source") or "all"),
            entry=payload.get("entry"),
            workflow_type=payload.get("workflow_type"),
            status_filter=payload.get("status_filter"),
            owner_type=payload.get("owner_type"),
            owner_id=payload.get("owner_id"),
        )
        if (
            decoded_cursor.page_size != page_size
            or decoded_cursor.source != source
            or decoded_cursor.entry != entry
            or decoded_cursor.workflow_type != workflow_type
            or decoded_cursor.status_filter != status_filter
            or decoded_cursor.owner_type != owner_type
            or decoded_cursor.owner_id != owner_id
        ):
            raise ValueError(
                "Compatibility cursor no longer matches the requested filters."
            )
        return decoded_cursor

    def _sanitize_metadata(
        self,
        payload: dict[str, Any],
        *,
        allowed_keys: set[str],
    ) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key in sorted(allowed_keys):
            if key not in payload:
                continue
            sanitized[key] = self._sanitize_scalar(payload[key])
        return sanitized

    def _sanitize_scalar(self, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= _MAX_METADATA_VALUE_LENGTH:
                return value
            return f"{value[: _MAX_METADATA_VALUE_LENGTH - 3]}..."
        if isinstance(value, (list, tuple)):
            return [
                self._sanitize_scalar(item)
                for item in list(value)[:_MAX_PARAMETER_PREVIEW_ITEMS]
            ]
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in list(value.items())[:_MAX_PARAMETER_PREVIEW_ITEMS]:
                sanitized[str(key)] = self._sanitize_scalar(item)
            return sanitized
        return str(value)[:_MAX_METADATA_VALUE_LENGTH]

    def _build_parameter_preview(self, parameters: dict[str, Any]) -> dict[str, Any]:
        preview: dict[str, Any] = {}
        for key, value in list(parameters.items())[:_MAX_PARAMETER_PREVIEW_ITEMS]:
            preview[str(key)] = self._sanitize_scalar(value)
        return preview

    def _extract_runtime(self, payload: dict[str, Any]) -> str | None:
        task = payload.get("task")
        if isinstance(task, dict):
            runtime = str(task.get("runtime") or task.get("runtimeMode") or "").strip()
            if runtime:
                return runtime
        runtime = str(payload.get("runtime") or payload.get("runtimeMode") or "").strip()
        return runtime or None

    def _extract_skill(self, payload: dict[str, Any]) -> str | None:
        task = payload.get("task")
        if isinstance(task, dict):
            skill = str(task.get("skill") or task.get("skillId") or "").strip()
            if skill:
                return skill
        return None

    def _extract_publish_mode(self, payload: dict[str, Any]) -> str | None:
        publish = payload.get("publish")
        if isinstance(publish, dict):
            mode = str(publish.get("mode") or "").strip().lower()
            return mode or None
        task = payload.get("task")
        if isinstance(task, dict):
            mode = str(task.get("publishMode") or "").strip().lower()
            return mode or None
        return None

    def _default_owner_type(self, record: db_models.TemporalExecutionRecord) -> str:
        attrs = dict(record.search_attributes or {})
        owner_type = str(attrs.get("mm_owner_type") or "").strip().lower()
        if owner_type in {"user", "system", "service"}:
            return owner_type
        return "system" if not record.owner_id or record.owner_id == "system" else "user"

    def _summarize_text(self, value: str, *, max_chars: int = 120) -> str:
        normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return ""
        first_line = normalized.splitlines()[0].strip()
        if len(first_line) <= max_chars:
            return first_line
        truncated = first_line[:max_chars].rstrip()
        safe_cut = truncated.rsplit(" ", 1)[0] or truncated
        return f"{safe_cut}..."
