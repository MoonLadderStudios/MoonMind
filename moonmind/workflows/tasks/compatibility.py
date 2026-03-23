"""Compatibility helpers for unified task list/detail APIs."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Literal

from typing_extensions import assert_never
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.schemas.task_compatibility_models import (
    TaskActionAvailability,
    TaskCompatibilityDetail,
    TaskCompatibilityListResponse,
    TaskCompatibilityRow,
    TaskDebugContext,
)
from moonmind.workflows.tasks.source_mapping import TaskSourceMappingService

TaskSourceFilter = Literal["queue", "temporal", "all"] | None
TaskStatusFilter = (
    Literal[
        "queued",
        "running",
        "waiting",
        "awaiting_action",
        "succeeded",
        "failed",
        "cancelled",
    ]
    | None
)

_TEMPORAL_STATUS_MAP: dict[db_models.MoonMindWorkflowState, str] = {
    db_models.MoonMindWorkflowState.INITIALIZING: "queued",
    db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting",
    db_models.MoonMindWorkflowState.PLANNING: "running",
    db_models.MoonMindWorkflowState.EXECUTING: "running",
    db_models.MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    db_models.MoonMindWorkflowState.FINALIZING: "running",
    db_models.MoonMindWorkflowState.SUCCEEDED: "succeeded",
    db_models.MoonMindWorkflowState.FAILED: "failed",
    db_models.MoonMindWorkflowState.CANCELED: "cancelled",
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
_ALLOWED_OWNER_TYPES = {"user", "system", "service"}


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
        rows, total_count = await self._load_rows(
            user=user,
            source=normalized_source,
            entry=entry,
            workflow_type=workflow_type,
            status_filter=status_filter,
            owner_type=owner_type,
            owner_id=owner_id,
            offset=normalized_cursor.offset,
            page_size=page_size,
        )
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
        source_hint: Literal["queue", "temporal"] | None,
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

            from moonmind.config.settings import settings

            if settings.temporal.temporal_authoritative_read_enabled:
                from moonmind.workflows.temporal.client import TemporalClientAdapter

                global _shared_client_adapter
                if "_shared_client_adapter" not in globals():
                    _shared_client_adapter = TemporalClientAdapter()
                client = await _shared_client_adapter.get_client()
                from api_service.core.sync import sync_single_temporal_execution_safely

                synced_record = await sync_single_temporal_execution_safely(
                    self._session, record.workflow_id, client
                )
                if synced_record:
                    record = synced_record

            return self._build_temporal_detail(record, user)
        if resolved.source == "queue":
            job = await self._session.get(
                "Any", UUID(resolved.source_record_id)
            )
            if job is None:
                raise RuntimeError(f"Queue task {task_id} disappeared.")
            return self._build_queue_detail(job)
        assert_never(resolved.source)

    async def _load_rows(
        self,
        *,
        user: db_models.User,
        source: Literal["queue", "temporal", "all"],
        entry: Literal["run", "manifest"] | None,
        workflow_type: str | None,
        status_filter: TaskStatusFilter,
        owner_type: Literal["user", "system", "service"] | None,
        owner_id: str | None,
        offset: int,
        page_size: int,
    ) -> tuple[list[TaskCompatibilityRow], int]:
        window_end = max(0, offset) + max(1, page_size)
        rows: list[TaskCompatibilityRow] = []
        total_count = 0
        if source in {"all", "queue"}:
            queue_rows, queue_count = await self._load_queue_rows(
                entry=entry,
                status_filter=status_filter,
                owner_id=owner_id,
                limit=window_end,
            )
            rows.extend(queue_rows)
            total_count += queue_count
        if source in {"all", "temporal"}:
            temporal_rows, temporal_count = await self._load_temporal_rows(
                user=user,
                entry=entry,
                workflow_type=workflow_type,
                status_filter=status_filter,
                owner_type=owner_type,
                owner_id=owner_id,
                limit=window_end,
            )
            rows.extend(temporal_rows)
            total_count += temporal_count
        rows.sort(
            key=lambda row: (row.created_at, row.updated_at, row.task_id),
            reverse=True,
        )
        return rows[:window_end], total_count

    async def _load_queue_rows(
        self,
        *,
        entry: Literal["run", "manifest"] | None,
        status_filter: TaskStatusFilter,
        owner_id: str | None,
        limit: int,
    ) -> tuple[list[TaskCompatibilityRow], int]:
        stmt = select("Any")
        if entry == "manifest":
            stmt = stmt.where("Any" == MANIFEST_JOB_TYPE)
        elif entry == "run":
            stmt = stmt.where("Any" != MANIFEST_JOB_TYPE)
        queue_statuses = self._queue_statuses_for_filter(status_filter)
        if queue_statuses == ():
            return [], 0
        if queue_statuses is not None:
            stmt = stmt.where("Any".in_(queue_statuses))

        normalized_owner_id = str(owner_id or "").strip()
        if normalized_owner_id:
            try:
                owner_uuid = UUID(normalized_owner_id)
            except ValueError:
                return [], 0
            stmt = stmt.where(self._queue_owner_id_expression() == owner_uuid)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        stmt = stmt.order_by(
            "Any".desc(),
            "Any".desc(),
            "Any".desc(),
        ).limit(limit)
        jobs = list((await self._session.execute(stmt)).scalars().all())
        total_count = int((await self._session.execute(count_stmt)).scalar_one())
        normalized: list[TaskCompatibilityRow] = []
        for job in jobs:
            row = self._build_queue_row(job)
            await self._source_mappings.upsert_queue_job(job)
            normalized.append(row)
        return normalized, total_count

    async def _load_temporal_rows(
        self,
        *,
        user: db_models.User,
        entry: Literal["run", "manifest"] | None,
        workflow_type: str | None,
        status_filter: TaskStatusFilter,
        owner_type: Literal["user", "system", "service"] | None,
        owner_id: str | None,
        limit: int,
    ) -> tuple[list[TaskCompatibilityRow], int]:
        stmt = select(db_models.TemporalExecutionRecord)
        if entry:
            stmt = stmt.where(db_models.TemporalExecutionRecord.entry == entry)
        if workflow_type:
            stmt = stmt.where(
                db_models.TemporalExecutionRecord.workflow_type == workflow_type
            )
        temporal_states = self._temporal_states_for_filter(status_filter)
        if temporal_states == ():
            return [], 0
        if temporal_states is not None:
            stmt = stmt.where(
                db_models.TemporalExecutionRecord.state.in_(temporal_states)
            )
        if not bool(getattr(user, "is_superuser", False)):
            stmt = stmt.where(
                db_models.TemporalExecutionRecord.owner_id == str(user.id)
            )
        normalized_owner_id = str(owner_id or "").strip()
        if owner_type:
            stmt = stmt.where(self._temporal_owner_type_expression() == owner_type)
        if normalized_owner_id:
            stmt = stmt.where(
                self._temporal_owner_id_expression() == normalized_owner_id
            )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        stmt = stmt.order_by(
            db_models.TemporalExecutionRecord.started_at.desc(),
            db_models.TemporalExecutionRecord.updated_at.desc(),
            db_models.TemporalExecutionRecord.workflow_id.desc(),
        ).limit(limit)
        records = list((await self._session.execute(stmt)).scalars().all())
        total_count = int((await self._session.execute(count_stmt)).scalar_one())

        from moonmind.config.settings import settings

        if settings.temporal.temporal_authoritative_read_enabled and records:
            import logging

            from moonmind.workflows.temporal.client import TemporalClientAdapter

            logger = logging.getLogger(__name__)
            global _shared_client_adapter
            if "_shared_client_adapter" not in globals():
                _shared_client_adapter = TemporalClientAdapter()
            client = await _shared_client_adapter.get_client()
            from api_service.core.sync import sync_temporal_executions_safely

            try:
                records = await sync_temporal_executions_safely(
                    self._session, records, client
                )
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )

        normalized: list[TaskCompatibilityRow] = []
        for record in records:
            row = self._build_temporal_row(record)
            await self._source_mappings.upsert_temporal_execution(record)
            normalized.append(row)
        return normalized, total_count

    def _build_queue_row(self, job: "Any") -> TaskCompatibilityRow:
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
            status="queued",  # legacy queue status mapping removed
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
            entry=str(search_attributes.get("mm_entry") or record.entry or "")
            .strip()
            .lower()
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
            ownerId=str(
                search_attributes.get("mm_owner_id") or record.owner_id or ""
            ).strip()
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
        job: "Any",
    ) -> TaskCompatibilityDetail:
        row = self._build_queue_row(job)
        return TaskCompatibilityDetail(
            **row.model_dump(),
            artifactRefs=[],
            actions=TaskActionAvailability(cancel=row.status in {"queued", "running"}),
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
                forceTerminate=bool(getattr(user, "is_superuser", False))
                and not is_terminal,
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
    ) -> Literal["queue", "temporal", "all"]:
        normalized = str(source or "all").strip().lower()
        if normalized not in {"queue", "temporal", "all"}:
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
        runtime = str(
            payload.get("runtime") or payload.get("runtimeMode") or ""
        ).strip()
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
        if owner_type in _ALLOWED_OWNER_TYPES:
            return owner_type
        return (
            "system" if not record.owner_id or record.owner_id == "system" else "user"
        )

    def _queue_statuses_for_filter(
        self,
        status_filter: TaskStatusFilter,
    ) -> tuple["Any", ...] | None:
        if status_filter is None:
            return None
        mapping = {
            "queued": ("Any",),
            "running": ("Any",),
            "awaiting_action": (),
            "succeeded": ("Any",),
            "failed": (
                "Any",
                "Any",
            ),
            "cancelled": ("Any",),
        }
        return mapping[status_filter]

    def _temporal_states_for_filter(
        self,
        status_filter: TaskStatusFilter,
    ) -> tuple[db_models.MoonMindWorkflowState, ...] | None:
        if status_filter is None:
            return None
        mapping = {
            "queued": (db_models.MoonMindWorkflowState.INITIALIZING,),
            "running": (
                db_models.MoonMindWorkflowState.PLANNING,
                db_models.MoonMindWorkflowState.EXECUTING,
                db_models.MoonMindWorkflowState.FINALIZING,
            ),
            "waiting": (db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,),
            "awaiting_action": (db_models.MoonMindWorkflowState.AWAITING_EXTERNAL,),
            "succeeded": (db_models.MoonMindWorkflowState.SUCCEEDED,),
            "failed": (db_models.MoonMindWorkflowState.FAILED,),
            "cancelled": (db_models.MoonMindWorkflowState.CANCELED,),
        }
        return mapping[status_filter]

    def _queue_owner_id_expression(self):
        return case(
            (
                "Any".is_not(None),
                "Any",
            ),
            else_="Any",
        )

    def _temporal_owner_id_expression(self):
        search_owner_id = func.trim(
            func.coalesce(
                db_models.TemporalExecutionRecord.search_attributes[
                    "mm_owner_id"
                ].as_string(),
                "",
            )
        )
        record_owner_id = func.trim(
            func.coalesce(db_models.TemporalExecutionRecord.owner_id, "")
        )
        return func.coalesce(
            func.nullif(search_owner_id, ""),
            func.nullif(record_owner_id, ""),
        )

    def _temporal_owner_type_expression(self):
        search_owner_type = func.lower(
            func.trim(
                func.coalesce(
                    db_models.TemporalExecutionRecord.search_attributes[
                        "mm_owner_type"
                    ].as_string(),
                    "",
                )
            )
        )
        record_owner_id = func.lower(
            func.trim(func.coalesce(db_models.TemporalExecutionRecord.owner_id, ""))
        )
        return case(
            (
                search_owner_type.in_(tuple(sorted(_ALLOWED_OWNER_TYPES))),
                search_owner_type,
            ),
            (
                or_(record_owner_id == "", record_owner_id == "system"),
                "system",
            ),
            else_="user",
        )

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
