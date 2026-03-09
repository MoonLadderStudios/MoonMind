"""Temporal execution lifecycle service.

This module implements the workflow type catalog and lifecycle contract described in
`docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalIntegrationCorrelationRecord,
    TemporalWorkflowType,
)
from moonmind.config.settings import settings
from moonmind.schemas.manifest_ingest_models import (
    ManifestNodePageModel,
    ManifestStatusSnapshotModel,
)
from moonmind.schemas.temporal_models import (
    SUPPORTED_FAILURE_POLICIES,
    SUPPORTED_SIGNAL_NAMES,
    SUPPORTED_UPDATE_NAMES,
)
from moonmind.workflows.temporal.manifest_ingest import (
    MANIFEST_UPDATE_NAMES,
    ManifestIngestValidationError,
    apply_manifest_update,
    build_manifest_status_snapshot,
    initialize_manifest_projection,
    list_manifest_nodes,
)

logger = logging.getLogger(__name__)

TERMINAL_STATES: set[MoonMindWorkflowState] = {
    MoonMindWorkflowState.SUCCEEDED,
    MoonMindWorkflowState.FAILED,
    MoonMindWorkflowState.CANCELED,
}

NON_TERMINAL_STATES: set[MoonMindWorkflowState] = {
    MoonMindWorkflowState.INITIALIZING,
    MoonMindWorkflowState.PLANNING,
    MoonMindWorkflowState.EXECUTING,
    MoonMindWorkflowState.AWAITING_EXTERNAL,
    MoonMindWorkflowState.FINALIZING,
}

TERMINAL_STATE_TO_CLOSE_STATUS: dict[
    MoonMindWorkflowState, TemporalExecutionCloseStatus
] = {
    MoonMindWorkflowState.SUCCEEDED: TemporalExecutionCloseStatus.COMPLETED,
    MoonMindWorkflowState.FAILED: TemporalExecutionCloseStatus.FAILED,
    MoonMindWorkflowState.CANCELED: TemporalExecutionCloseStatus.CANCELED,
}

WORKFLOW_ENTRY_BY_TYPE: dict[TemporalWorkflowType, str] = {
    TemporalWorkflowType.RUN: "run",
    TemporalWorkflowType.MANIFEST_INGEST: "manifest",
}

ALLOWED_OWNER_TYPES: set[str] = {item.value for item in TemporalExecutionOwnerType}
ALLOWED_ENTRY_VALUES: set[str] = set(WORKFLOW_ENTRY_BY_TYPE.values())
ALLOWED_UPDATE_NAMES: frozenset[str] = frozenset(SUPPORTED_UPDATE_NAMES)
ALLOWED_SIGNAL_NAMES: frozenset[str] = frozenset(SUPPORTED_SIGNAL_NAMES)
ALLOWED_FAILURE_POLICIES: frozenset[str] = frozenset(SUPPORTED_FAILURE_POLICIES)
ALLOWED_ERROR_CATEGORIES: set[str] = {
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
}
ALLOWED_WAITING_REASONS: set[str] = {
    "approval_required",
    "external_callback",
    "external_completion",
    "operator_paused",
    "retry_backoff",
    "unknown_external",
}
ALLOWED_INTEGRATION_STATUSES: set[str] = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "unknown",
}
TERMINAL_INTEGRATION_STATUSES: set[str] = {"succeeded", "failed", "canceled"}
_SEEN_PROVIDER_EVENT_LIMIT = 50
_CORRELATION_EXPIRY_DAYS = 30
PAGINATION_ORDERING = "mm_updated_at_desc__workflow_id_desc"
CONTINUE_AS_NEW_CAUSES: set[str] = {
    "manual_rerun",
    "lifecycle_threshold",
    "major_reconfiguration",
}


class TemporalExecutionError(RuntimeError):
    """Base class for temporal execution service errors."""


class TemporalExecutionNotFoundError(TemporalExecutionError):
    """Raised when a workflow execution cannot be located."""


class TemporalExecutionValidationError(TemporalExecutionError):
    """Raised when lifecycle invariants are violated."""


@dataclass(slots=True)
class TemporalExecutionListResult:
    """Paginated temporal execution list response payload."""

    items: list[TemporalExecutionRecord | TemporalExecutionCanonicalRecord]
    next_page_token: str | None
    count: int


class TemporalExecutionService:
    """Canonical execution store with a projection mirror for compatibility reads."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        namespace: str = "moonmind",
        integration_task_queue: str = "mm.activity.integrations",
        integration_poll_initial_seconds: int = 5,
        integration_poll_max_seconds: int = 300,
        integration_poll_jitter_ratio: float = 0.2,
        run_continue_as_new_step_threshold: int = 500,
        run_continue_as_new_wait_cycle_threshold: int = 200,
        manifest_continue_as_new_phase_threshold: int = 5,
    ) -> None:
        self._session = session
        self._namespace = namespace
        self._integration_task_queue = str(integration_task_queue).strip() or (
            "mm.activity.integrations"
        )
        from moonmind.workflows.temporal.client import TemporalClientAdapter

        self._client_adapter = TemporalClientAdapter()
        self._integration_poll_initial_seconds = max(
            1, int(integration_poll_initial_seconds)
        )
        self._integration_poll_max_seconds = max(
            self._integration_poll_initial_seconds,
            int(integration_poll_max_seconds),
        )
        self._integration_poll_jitter_ratio = min(
            1.0, max(0.0, float(integration_poll_jitter_ratio))
        )
        self._run_continue_as_new_step_threshold = run_continue_as_new_step_threshold
        self._run_continue_as_new_wait_cycle_threshold = (
            run_continue_as_new_wait_cycle_threshold
        )
        self._manifest_continue_as_new_phase_threshold = (
            manifest_continue_as_new_phase_threshold
        )

    async def create_execution(
        self,
        *,
        workflow_type: str,
        owner_id: UUID | str | None,
        owner_type: str | None = None,
        title: str | None,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        manifest_artifact_ref: str | None,
        failure_policy: str | None,
        initial_parameters: dict[str, Any] | None,
        idempotency_key: str | None,
        repository: str | None = None,
        integration: str | None = None,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        workflow_type_enum = self._parse_workflow_type(workflow_type)
        owner_type_enum, owner = self._resolve_owner_metadata(
            owner_id=owner_id,
            owner_type=owner_type,
        )

        if workflow_type_enum is TemporalWorkflowType.MANIFEST_INGEST:
            if not manifest_artifact_ref:
                raise TemporalExecutionValidationError(
                    "manifestArtifactRef is required for MoonMind.ManifestIngest"
                )

        if (
            failure_policy is not None
            and failure_policy not in ALLOWED_FAILURE_POLICIES
        ):
            supported = ", ".join(sorted(ALLOWED_FAILURE_POLICIES))
            raise TemporalExecutionValidationError(
                f"Unsupported failurePolicy '{failure_policy}'. Supported values: {supported}"
            )

        if idempotency_key:
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_id=owner,
                owner_type=owner_type_enum,
                workflow_type=workflow_type_enum,
            )
            if existing is not None:
                return await self._sync_projection_best_effort(existing)

        now = _utc_now()
        workflow_id = f"mm:{uuid4()}"
        params = dict(initial_parameters or {})
        if failure_policy is not None:
            params.setdefault("failurePolicy", failure_policy)

        resolved_title = title or self._default_title_for_type(workflow_type_enum)
        memo = {
            "title": resolved_title,
            "summary": summary or "Execution initialized.",
        }
        if input_artifact_ref:
            memo["input_ref"] = input_artifact_ref
        if manifest_artifact_ref:
            memo["manifest_ref"] = manifest_artifact_ref
        if idempotency_key:
            memo["idempotency_key"] = idempotency_key

        search_attributes = {
            "mm_owner_type": owner_type_enum.value,
            "mm_owner_id": owner,
            "mm_state": MoonMindWorkflowState.INITIALIZING.value,
            "mm_updated_at": _format_search_attribute_datetime(now),
            "mm_entry": WORKFLOW_ENTRY_BY_TYPE[workflow_type_enum],
        }
        if repository:
            search_attributes["mm_repo"] = repository
        if integration:
            search_attributes["mm_integration"] = integration

        artifact_refs = [
            ref
            for ref in (input_artifact_ref, plan_artifact_ref, manifest_artifact_ref)
            if ref
        ]

        record = TemporalExecutionCanonicalRecord(
            workflow_id=workflow_id,
            run_id=str(uuid4()),
            namespace=self._namespace,
            workflow_type=workflow_type_enum,
            owner_id=owner,
            owner_type=owner_type_enum,
            state=MoonMindWorkflowState.INITIALIZING,
            close_status=None,
            entry=WORKFLOW_ENTRY_BY_TYPE[workflow_type_enum],
            search_attributes=search_attributes,
            memo=memo,
            artifact_refs=artifact_refs,
            input_ref=input_artifact_ref,
            plan_ref=plan_artifact_ref,
            manifest_ref=manifest_artifact_ref,
            parameters=params,
            integration_state=None,
            pending_parameters_patch=None,
            paused=False,
            awaiting_external=False,
            waiting_reason=None,
            attention_required=False,
            step_count=0,
            wait_cycle_count=0,
            rerun_count=0,
            create_idempotency_key=idempotency_key,
            last_update_idempotency_key=None,
            last_update_response=None,
            started_at=now,
            updated_at=now,
            closed_at=None,
        )
        self._session.add(record)
        if workflow_type_enum is TemporalWorkflowType.MANIFEST_INGEST:
            initialize_manifest_projection(record)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if not idempotency_key:
                raise
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_id=owner,
                owner_type=owner_type_enum,
                workflow_type=workflow_type_enum,
            )
            if existing is None:
                raise exc
            return await self._sync_projection_best_effort(existing)
        try:
            temporal_search_attributes = {
                k: [v] if not isinstance(v, list) else v
                for k, v in search_attributes.items()
            }
            start_result = await self._client_adapter.start_workflow(
                workflow_type=workflow_type_enum.value,
                workflow_id=workflow_id,
                input_args=params,
                memo=memo,
                search_attributes=temporal_search_attributes,
            )
        except Exception:
            await self._session.delete(record)
            await self._session.commit()
            raise

        record.run_id = start_result.run_id
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            await self._session.delete(record)
            await self._session.commit()
            raise
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def list_executions(
        self,
        *,
        workflow_type: str | None = None,
        state: str | None = None,
        entry: str | None = None,
        owner_type: str | None = None,
        owner_id: UUID | str | None = None,
        repo: str | None = None,
        integration: str | None = None,
        page_size: int,
        next_page_token: str | None = None,
    ) -> TemporalExecutionListResult:
        owner = str(owner_id) if owner_id is not None else None
        page_size = max(1, min(page_size, 200))
        workflow_type_enum = (
            self._parse_workflow_type(workflow_type) if workflow_type else None
        )
        owner_type_enum = self._parse_owner_type(owner_type) if owner_type else None
        state_enum = self._parse_state(state) if state else None
        entry_value = self._parse_entry(entry) if entry else None
        query_scope = self._page_token_scope(
            workflow_type=workflow_type_enum,
            owner_type=owner_type_enum,
            state=state_enum,
            owner_id=owner,
            entry=entry_value,
            repo=repo,
            integration=integration,
        )
        offset = self._decode_page_token(next_page_token, expected_scope=query_scope)

        stmt = select(TemporalExecutionCanonicalRecord)
        stmt = self._apply_filters(
            stmt,
            model=TemporalExecutionCanonicalRecord,
            workflow_type=workflow_type_enum,
            owner_type=owner_type_enum,
            state=state_enum,
            entry=entry_value,
            owner_id=owner,
            repo=repo,
            integration=integration,
        )
        stmt = stmt.order_by(
            TemporalExecutionCanonicalRecord.updated_at.desc(),
            TemporalExecutionCanonicalRecord.workflow_id.desc(),
        )
        stmt = stmt.offset(offset).limit(page_size + 1)

        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > page_size
        items = await self._sync_projections_best_effort(rows[:page_size])

        next_token = (
            self._encode_page_token(offset + page_size, scope=query_scope)
            if has_more
            else None
        )

        count_stmt = select(func.count()).select_from(TemporalExecutionCanonicalRecord)
        count_stmt = self._apply_filters(
            count_stmt,
            model=TemporalExecutionCanonicalRecord,
            workflow_type=workflow_type_enum,
            owner_type=owner_type_enum,
            state=state_enum,
            entry=entry_value,
            owner_id=owner,
            repo=repo,
            integration=integration,
        )
        count = int((await self._session.execute(count_stmt)).scalar_one())

        return TemporalExecutionListResult(
            items=items,
            next_page_token=next_token,
            count=count,
        )

    async def describe_execution(
        self,
        workflow_id: str,
        *,
        include_orphaned: bool = False,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        canonical_workflow_id = self.canonicalize_workflow_id(workflow_id)

        try:
            await self._client_adapter.describe_workflow(canonical_workflow_id)
        except Exception as exc:
            logger.debug(
                "Temporal describe failed for %s: %s", canonical_workflow_id, exc
            )

        record = await self._load_source_execution(
            canonical_workflow_id,
        )
        if record is None:
            if settings.temporal.temporal_authoritative_read_enabled:
                try:
                    from moonmind.workflows.temporal.client import (
                        fetch_workflow_execution,
                        get_temporal_client,
                    )

                    temporal_client = await get_temporal_client(
                        settings.temporal.address, settings.temporal.namespace
                    )
                    desc = await fetch_workflow_execution(
                        temporal_client,
                        canonical_workflow_id,
                    )
                    projection = await self._upsert_projection_from_temporal(
                        desc,
                        synced_at=None,
                        source=None,
                    )
                    await self._session.commit()
                    await self._session.refresh(projection)
                    return projection
                except Exception as exc:
                    logger.warning(
                        "Failed to rehydrate execution %s from Temporal: %s",
                        canonical_workflow_id,
                        exc,
                        exc_info=True,
                    )
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {canonical_workflow_id} was not found"
            )
        if include_orphaned:
            projection = await self._load_projection_execution(
                canonical_workflow_id,
                include_orphaned=True,
            )
            if projection is not None:
                return projection
        return await self._sync_projection_best_effort(record)

    def canonicalize_workflow_id(self, workflow_id: str) -> str:
        candidate = TemporalExecutionRecord.canonicalize_identifier(workflow_id)
        if candidate:
            return candidate
        raise TemporalExecutionValidationError("workflowId is required")

    async def update_execution(
        self,
        *,
        workflow_id: str,
        update_name: str,
        input_artifact_ref: str | None = None,
        plan_artifact_ref: str | None = None,
        parameters_patch: dict[str, Any] | None = None,
        title: str | None = None,
        new_manifest_artifact_ref: str | None = None,
        mode: str | None = None,
        max_concurrency: int | None = None,
        node_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if update_name not in ALLOWED_UPDATE_NAMES:
            raise TemporalExecutionValidationError(
                f"Unsupported update name: {update_name}"
            )

        record = await self._require_source_execution(workflow_id)

        try:
            update_arg = {
                "input_artifact_ref": input_artifact_ref,
                "plan_artifact_ref": plan_artifact_ref,
                "parameters_patch": parameters_patch,
                "title": title,
                "new_manifest_artifact_ref": new_manifest_artifact_ref,
                "mode": mode,
                "max_concurrency": max_concurrency,
                "node_ids": node_ids,
                "idempotency_key": idempotency_key,
            }
            # Remove None values
            update_arg = {k: v for k, v in update_arg.items() if v is not None}
            await self._client_adapter.update_workflow(
                record.workflow_id, update_name, update_arg
            )
        except Exception as exc:
            raise TemporalExecutionValidationError(
                f"Temporal update failed: {exc}"
            ) from exc

        if (
            update_name in MANIFEST_UPDATE_NAMES
            and record.workflow_type is not TemporalWorkflowType.MANIFEST_INGEST
        ):
            raise TemporalExecutionValidationError(
                f"Update {update_name} is only supported for "
                "MoonMind.ManifestIngest workflows"
            )

        if idempotency_key and idempotency_key == record.last_update_idempotency_key:
            cached = record.last_update_response
            if isinstance(cached, dict):
                return dict(cached)

        if record.state in TERMINAL_STATES:
            return {
                "accepted": False,
                "applied": "immediate",
                "message": "Workflow is in a terminal state and no longer accepts updates.",
            }

        if record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST and (
            update_name in MANIFEST_UPDATE_NAMES
        ):
            try:
                response = apply_manifest_update(
                    record,
                    update_name=update_name,
                    new_manifest_artifact_ref=new_manifest_artifact_ref,
                    mode=mode,
                    max_concurrency=max_concurrency,
                    node_ids=node_ids,
                )
            except ManifestIngestValidationError as exc:
                raise TemporalExecutionValidationError(str(exc)) from exc
            self._touch(record)
            if response.get("message"):
                self._update_summary(record, str(response["message"]))
        elif update_name == "UpdateInputs":
            response = self._apply_update_inputs(
                record,
                input_artifact_ref=input_artifact_ref,
                plan_artifact_ref=plan_artifact_ref,
                parameters_patch=parameters_patch,
            )
        elif update_name == "SetTitle":
            if not title:
                raise TemporalExecutionValidationError(
                    "title is required when updateName is SetTitle"
                )
            response = self._apply_set_title(record, title)
        elif update_name == "RequestRerun":
            response = self._apply_request_rerun(
                record,
                input_artifact_ref=input_artifact_ref,
                plan_artifact_ref=plan_artifact_ref,
                parameters_patch=parameters_patch,
            )
        else:
            raise TemporalExecutionValidationError(
                f"Unsupported update name: {update_name}"
            )

        if idempotency_key:
            record.last_update_idempotency_key = idempotency_key
            record.last_update_response = dict(response)

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        await self._sync_projection_best_effort(record)
        return response

    async def describe_manifest_status(
        self,
        workflow_id: str,
    ) -> ManifestStatusSnapshotModel:
        record = await self.describe_execution(workflow_id)
        try:
            return build_manifest_status_snapshot(record)
        except ManifestIngestValidationError as exc:
            raise TemporalExecutionValidationError(str(exc)) from exc

    async def list_manifest_nodes(
        self,
        workflow_id: str,
        *,
        state: str | None,
        cursor: str | None,
        limit: int,
    ) -> ManifestNodePageModel:
        record = await self.describe_execution(workflow_id)
        try:
            return list_manifest_nodes(
                record,
                state=state,
                cursor=cursor,
                limit=limit,
            )
        except ManifestIngestValidationError as exc:
            raise TemporalExecutionValidationError(str(exc)) from exc

    async def signal_execution(
        self,
        *,
        workflow_id: str,
        signal_name: str,
        payload: dict[str, Any] | None,
        payload_artifact_ref: str | None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        if signal_name not in ALLOWED_SIGNAL_NAMES:
            raise TemporalExecutionValidationError(
                f"Unsupported signal name: {signal_name}"
            )
        record = await self._require_source_execution(workflow_id)

        try:
            signal_arg = {
                "payload": payload,
                "payload_artifact_ref": payload_artifact_ref,
            }
            await self._client_adapter.signal_workflow(
                record.workflow_id, signal_name, signal_arg
            )
        except Exception as exc:
            raise TemporalExecutionValidationError(
                f"Temporal signal failed: {exc}"
            ) from exc

        signal_payload = dict(payload or {})
        if signal_name == "ExternalEvent":
            source_raw = signal_payload.get("source")
            event_type_raw = signal_payload.get("event_type")
            if not source_raw or not event_type_raw:
                raise TemporalExecutionValidationError(
                    "ExternalEvent requires payload.source and payload.event_type"
                )
            source = str(source_raw)
            event_type = str(event_type_raw)
            if payload_artifact_ref:
                refs = list(record.artifact_refs or [])
                if payload_artifact_ref not in refs:
                    refs.append(payload_artifact_ref)
                record.artifact_refs = refs
            integration_state = self._integration_state(record)
            if integration_state is None:
                self._clear_waiting_metadata(record)
                if not record.paused:
                    self._set_state(record, MoonMindWorkflowState.EXECUTING)
                    self._clear_wait_metadata(record)
                else:
                    self._set_waiting_metadata(
                        record,
                        waiting_reason="operator_paused",
                        attention_required=True,
                    )
                    self._set_wait_metadata(
                        record,
                        waiting_reason="operator_paused",
                        attention_required=True,
                    )
                self._update_summary(
                    record,
                    f"Processed external event '{event_type}' from '{source}'.",
                )
            else:
                self._apply_external_event(
                    record,
                    source=source,
                    event_type=event_type,
                    payload=signal_payload,
                )
        elif signal_name == "Approve":
            approval_type = signal_payload.get("approval_type")
            if not approval_type:
                raise TemporalExecutionValidationError(
                    "Approve requires payload.approval_type"
                )
            record.paused = False
            self._clear_waiting_metadata(record)
            self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(record, "Approval signal received.")
        elif signal_name == "Pause":
            record.paused = True
            self._set_waiting_metadata(
                record,
                waiting_reason="operator_paused",
                attention_required=True,
            )
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
            self._set_wait_metadata(
                record,
                waiting_reason="operator_paused",
                attention_required=True,
            )
            self._update_summary(record, "Execution paused.")
        elif signal_name == "Resume":
            record.paused = False
            self._clear_waiting_metadata(record)
            self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(record, "Execution resumed.")

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def configure_integration_monitoring(
        self,
        *,
        workflow_id: str,
        integration_name: str,
        correlation_id: str | None,
        external_operation_id: str,
        normalized_status: str,
        provider_status: str | None,
        callback_supported: bool,
        callback_correlation_key: str | None,
        recommended_poll_seconds: int | None,
        external_url: str | None,
        provider_summary: dict[str, Any] | None,
        result_refs: list[str] | None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)

        now = _utc_now()
        normalized = self._parse_integration_status(normalized_status)
        callback_key = (
            str(callback_correlation_key).strip() or None
            if callback_correlation_key is not None
            else None
        )
        if callback_supported and not callback_key:
            callback_key = uuid4().hex

        state = {
            "integration_name": self._normalize_integration_name(integration_name),
            "correlation_id": (
                str(correlation_id).strip() if correlation_id else uuid4().hex
            ),
            "external_operation_id": self._require_text(
                external_operation_id,
                field_name="external_operation_id",
            ),
            "normalized_status": normalized,
            "provider_status": self._clean_text(provider_status),
            "started_at": now.isoformat(),
            "last_observed_at": now.isoformat(),
            "monitor_attempt_count": 0,
            "callback_supported": bool(callback_supported),
            "result_refs": self._merge_refs((), result_refs or []),
            "callback_correlation_key": callback_key,
            "provider_event_ids_seen": [],
            "next_poll_at": None,
            "poll_interval_seconds": None,
            "external_url": self._clean_text(external_url),
            "provider_summary": dict(provider_summary or {}),
        }
        state["provider_summary"].setdefault("task_queue", self._integration_task_queue)
        self._set_poll_schedule(
            state,
            observed_at=now,
            recommended_poll_seconds=recommended_poll_seconds,
            status_changed=True,
        )

        record.integration_state = state
        record.artifact_refs = self._merge_refs(
            record.artifact_refs or [], state["result_refs"]
        )

        if normalized in TERMINAL_INTEGRATION_STATUSES:
            self._clear_waiting_metadata(record)
            if not record.paused:
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(
                record,
                f"Integration '{state['integration_name']}' is already {normalized}.",
                external_url=state.get("external_url"),
            )
        else:
            self._set_waiting_metadata(
                record,
                waiting_reason=self._integration_waiting_reason(state),
                attention_required=False,
            )
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
            self._update_summary(
                record,
                f"Waiting on integration '{state['integration_name']}' ({normalized}).",
                external_url=state.get("external_url"),
            )

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def record_integration_poll(
        self,
        *,
        workflow_id: str,
        normalized_status: str,
        provider_status: str | None,
        observed_at: datetime | None,
        recommended_poll_seconds: int | None,
        external_url: str | None,
        provider_summary: dict[str, Any] | None,
        result_refs: list[str] | None,
        completed_wait_cycles: int,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        if completed_wait_cycles < 0:
            raise TemporalExecutionValidationError(
                "completed_wait_cycles must be non-negative."
            )

        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        state = self._require_integration_state(record)

        observed = observed_at or _utc_now()
        normalized = self._parse_integration_status(normalized_status)
        current_status = self._parse_integration_status(state["normalized_status"])
        status_changed = normalized != current_status
        if (
            current_status in TERMINAL_INTEGRATION_STATUSES
            and normalized not in TERMINAL_INTEGRATION_STATUSES
        ):
            self._update_summary(
                record,
                f"Ignored late non-terminal poll result for integration '{state['integration_name']}'.",
                external_url=state.get("external_url"),
            )
            await self._sync_integration_correlation_record(record)
            await self._session.commit()
            await self._session.refresh(record)
            return await self._sync_projection_best_effort(record)

        state["normalized_status"] = normalized
        state["provider_status"] = self._clean_text(provider_status)
        state["last_observed_at"] = observed.isoformat()
        state["monitor_attempt_count"] = int(state.get("monitor_attempt_count", 0)) + 1
        if external_url is not None:
            state["external_url"] = self._clean_text(external_url)
        if provider_summary:
            state["provider_summary"] = dict(provider_summary)
        state["result_refs"] = self._merge_refs(
            state.get("result_refs", []),
            result_refs or [],
        )
        self._set_poll_schedule(
            state,
            observed_at=observed,
            recommended_poll_seconds=recommended_poll_seconds,
            status_changed=status_changed,
        )
        record.integration_state = state
        record.artifact_refs = self._merge_refs(
            record.artifact_refs or [], state["result_refs"]
        )

        if completed_wait_cycles:
            record.wait_cycle_count = (
                int(record.wait_cycle_count or 0) + completed_wait_cycles
            )

        if normalized in TERMINAL_INTEGRATION_STATUSES:
            self._clear_waiting_metadata(record)
            if not record.paused:
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(
                record,
                f"Integration '{state['integration_name']}' reached {normalized}.",
                error_category=(
                    "integration_error" if normalized == "failed" else None
                ),
                external_url=state.get("external_url"),
            )
        else:
            self._set_waiting_metadata(
                record,
                waiting_reason=self._integration_waiting_reason(state),
                attention_required=False,
            )
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
            self._update_summary(
                record,
                f"Polled integration '{state['integration_name']}' and observed {normalized}.",
                external_url=state.get("external_url"),
            )

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new during integration monitoring.",
                cause="lifecycle_threshold",
            )

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def ingest_integration_callback(
        self,
        *,
        integration_name: str,
        callback_correlation_key: str,
        payload: dict[str, Any] | None,
        payload_artifact_ref: str | None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        correlation, record = await self.resolve_integration_callback_target(
            integration_name=integration_name,
            callback_correlation_key=callback_correlation_key,
        )
        if record.state in TERMINAL_STATES:
            return record

        callback_payload = dict(payload or {})
        callback_payload.setdefault("source", integration_name)
        callback_payload.setdefault(
            "external_operation_id",
            correlation.external_operation_id,
        )
        return await self.signal_execution(
            workflow_id=correlation.workflow_id,
            signal_name="ExternalEvent",
            payload=callback_payload,
            payload_artifact_ref=payload_artifact_ref,
        )

    async def resolve_integration_callback_target(
        self,
        *,
        integration_name: str,
        callback_correlation_key: str,
    ) -> tuple[
        TemporalIntegrationCorrelationRecord,
        TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
    ]:
        """Return the durable correlation row and current execution for one callback."""

        correlation = await self._find_integration_correlation(
            integration_name=integration_name,
            callback_correlation_key=callback_correlation_key,
        )
        if correlation is None:
            raise TemporalExecutionNotFoundError(
                f"Integration callback target for '{integration_name}' was not found"
            )
        record = await self.describe_execution(correlation.workflow_id)
        return correlation, record

    async def cancel_execution(
        self,
        *,
        workflow_id: str,
        reason: str | None,
        graceful: bool,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)

        try:
            await self._client_adapter.cancel_workflow(record.workflow_id)
        except Exception as exc:
            raise TemporalExecutionValidationError(
                f"Temporal cancel failed: {exc}"
            ) from exc

        if record.state in TERMINAL_STATES:
            return await self._sync_projection_best_effort(record)

        reason_text = (reason or "Canceled by user.").strip() or "Canceled by user."
        record.paused = False
        self._clear_waiting_metadata(record)
        if graceful:
            self._set_state(
                record,
                MoonMindWorkflowState.CANCELED,
                close_status=TemporalExecutionCloseStatus.CANCELED,
            )
            self._update_summary(record, reason_text)
        else:
            self._set_state(
                record,
                MoonMindWorkflowState.FAILED,
                close_status=TemporalExecutionCloseStatus.TERMINATED,
            )
            self._update_summary(record, f"forced_termination: {reason_text}")

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_succeeded(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.FINALIZING)
        self._set_state(
            record,
            MoonMindWorkflowState.SUCCEEDED,
            close_status=TemporalExecutionCloseStatus.COMPLETED,
        )
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_planning(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.PLANNING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_executing(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.EXECUTING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_awaiting_external(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
        waiting_reason: str | None = None,
        attention_required: bool | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_waiting_metadata(
            record,
            waiting_reason=waiting_reason or "unknown_external",
            attention_required=bool(attention_required),
        )
        self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
        self._set_wait_metadata(
            record,
            waiting_reason=waiting_reason or "unknown_external",
            attention_required=bool(attention_required),
        )
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_finalizing(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.FINALIZING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def record_progress(
        self,
        *,
        workflow_id: str,
        completed_steps: int = 0,
        completed_wait_cycles: int = 0,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        if completed_steps < 0 or completed_wait_cycles < 0:
            raise TemporalExecutionValidationError(
                "Progress increments must be non-negative."
            )

        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)

        if completed_steps:
            record.step_count = int(record.step_count or 0) + completed_steps
        if completed_wait_cycles:
            record.wait_cycle_count = (
                int(record.wait_cycle_count or 0) + completed_wait_cycles
            )
        if not completed_steps and not completed_wait_cycles:
            return await self._sync_projection_best_effort(record)
        self._touch(record)

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new after lifecycle threshold.",
                cause="lifecycle_threshold",
            )
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_failed(
        self,
        *,
        workflow_id: str,
        error_category: str,
        message: str,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        if error_category not in ALLOWED_ERROR_CATEGORIES:
            supported = ", ".join(sorted(ALLOWED_ERROR_CATEGORIES))
            raise TemporalExecutionValidationError(
                f"Unsupported error_category '{error_category}'. Supported values: {supported}"
            )
        record = await self._require_source_execution(workflow_id)
        self._set_state(
            record,
            MoonMindWorkflowState.FAILED,
            close_status=TemporalExecutionCloseStatus.FAILED,
        )
        self._update_summary(
            record,
            f"{error_category}: {message}",
            error_category=error_category,
        )
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_projection_stale(
        self,
        *,
        workflow_id: str,
        sync_error: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self._require_projection_execution(
            workflow_id,
            include_orphaned=True,
        )
        record.sync_state = TemporalExecutionProjectionSyncState.STALE
        record.sync_error = (sync_error or "").strip() or None
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def mark_projection_repair_pending(
        self,
        *,
        workflow_id: str,
        sync_error: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self._require_projection_execution(
            workflow_id,
            include_orphaned=True,
        )
        record.sync_state = TemporalExecutionProjectionSyncState.REPAIR_PENDING
        record.sync_error = (sync_error or "").strip() or None
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def mark_projection_orphaned(
        self,
        *,
        workflow_id: str,
        sync_error: str | None = None,
    ) -> TemporalExecutionRecord:
        source = await self._load_source_execution(workflow_id)
        record = await self._require_projection_execution(
            workflow_id,
            include_orphaned=True,
        )
        if source is not None:
            self._touch(source)
        record.sync_state = TemporalExecutionProjectionSyncState.ORPHANED
        record.sync_error = (sync_error or "").strip() or None
        await self._session.commit()
        await self._session.refresh(record)
        return record

    def _apply_update_inputs(
        self,
        record: TemporalExecutionCanonicalRecord,
        *,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        parameters_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        updated = False
        major_reconfiguration = False

        if input_artifact_ref and input_artifact_ref != record.input_ref:
            record.input_ref = input_artifact_ref
            updated = True
            refs = list(record.artifact_refs or [])
            if input_artifact_ref not in refs:
                refs.append(input_artifact_ref)
                record.artifact_refs = refs

        if plan_artifact_ref and plan_artifact_ref != record.plan_ref:
            major_reconfiguration = bool(record.plan_ref)
            record.plan_ref = plan_artifact_ref
            updated = True
            refs = list(record.artifact_refs or [])
            if plan_artifact_ref not in refs:
                refs.append(plan_artifact_ref)
                record.artifact_refs = refs

        if parameters_patch:
            merged = dict(record.parameters or {})
            merged.update(parameters_patch)
            record.parameters = merged
            updated = True
            if parameters_patch.get("request_continue_as_new") is True:
                major_reconfiguration = True

        if major_reconfiguration:
            self._continue_as_new(
                record,
                summary="Applied input update via Continue-As-New.",
                cause="major_reconfiguration",
            )
            return {
                "accepted": True,
                "applied": "continue_as_new",
                "message": "Update applied via Continue-As-New.",
                "continue_as_new_cause": "major_reconfiguration",
            }

        if not updated:
            return {
                "accepted": True,
                "applied": "immediate",
                "message": "No changes were requested.",
            }

        if record.state in {
            MoonMindWorkflowState.EXECUTING,
            MoonMindWorkflowState.AWAITING_EXTERNAL,
        }:
            record.pending_parameters_patch = dict(parameters_patch or {})
            self._touch(record)
            self._update_summary(record, "Update accepted for next safe point.")
            return {
                "accepted": True,
                "applied": "next_safe_point",
                "message": "Update accepted and will be applied at the next safe point.",
            }

        self._touch(record)
        self._update_summary(record, "Inputs updated.")
        return {
            "accepted": True,
            "applied": "immediate",
            "message": "Update applied immediately.",
        }

    def _apply_set_title(
        self,
        record: TemporalExecutionCanonicalRecord,
        title: str,
    ) -> dict[str, Any]:
        memo = dict(record.memo or {})
        memo["title"] = title
        record.memo = memo
        self._touch(record)
        return {
            "accepted": True,
            "applied": "immediate",
            "message": "Title updated.",
        }

    def _apply_request_rerun(
        self,
        record: TemporalExecutionCanonicalRecord,
        *,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        parameters_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if input_artifact_ref:
            record.input_ref = input_artifact_ref
        if plan_artifact_ref:
            record.plan_ref = plan_artifact_ref
        if input_artifact_ref or plan_artifact_ref:
            refs = list(record.artifact_refs or [])
            for ref in (input_artifact_ref, plan_artifact_ref):
                if ref and ref not in refs:
                    refs.append(ref)
            record.artifact_refs = refs
        if parameters_patch:
            params = dict(record.parameters or {})
            params.update(parameters_patch)
            record.parameters = params
        self._continue_as_new(
            record,
            summary="Rerun requested via Continue-As-New.",
            cause="manual_rerun",
        )
        return {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
            "continue_as_new_cause": "manual_rerun",
        }

    def _continue_as_new(
        self,
        record: TemporalExecutionCanonicalRecord,
        *,
        summary: str,
        cause: str,
    ) -> None:
        if cause not in CONTINUE_AS_NEW_CAUSES:
            raise TemporalExecutionValidationError(
                f"Unsupported Continue-As-New cause: {cause}"
            )
        integration_state = self._integration_state(record)
        integration_wait_active = False
        if integration_state is not None:
            integration_wait_active = (
                self._parse_integration_status(integration_state["normalized_status"])
                not in TERMINAL_INTEGRATION_STATUSES
            )
        record.run_id = str(uuid4())
        record.rerun_count = int(record.rerun_count or 0) + 1
        record.step_count = 0
        record.wait_cycle_count = 0
        record.paused = False
        self._clear_waiting_metadata(record)
        if integration_wait_active:
            self._set_waiting_metadata(
                record,
                waiting_reason="external_completion",
                attention_required=False,
            )
        record.closed_at = None
        record.close_status = None
        record.pending_parameters_patch = None

        if integration_wait_active:
            next_state = MoonMindWorkflowState.AWAITING_EXTERNAL
        elif record.workflow_type is TemporalWorkflowType.RUN:
            next_state = (
                MoonMindWorkflowState.EXECUTING
                if record.plan_ref
                else MoonMindWorkflowState.PLANNING
            )
        else:
            next_state = MoonMindWorkflowState.EXECUTING

        self._set_state(record, next_state)
        self._update_summary(record, summary, continue_as_new_cause=cause)

    def _set_state(
        self,
        record: TemporalExecutionCanonicalRecord,
        state: MoonMindWorkflowState,
        *,
        close_status: TemporalExecutionCloseStatus | None = None,
    ) -> None:
        if state is not MoonMindWorkflowState.AWAITING_EXTERNAL:
            self._clear_wait_metadata(record)

        if state in TERMINAL_STATES:
            enforced_status = close_status or TERMINAL_STATE_TO_CLOSE_STATUS[state]
            record.close_status = enforced_status
            record.closed_at = _utc_now()
        else:
            record.close_status = None
            record.closed_at = None

        record.state = state
        if state is not MoonMindWorkflowState.AWAITING_EXTERNAL:
            self._clear_waiting_metadata(record)
        self._touch(record)

    def _touch(self, record: TemporalExecutionCanonicalRecord) -> None:
        now = _utc_now()
        record.updated_at = now

        attrs = dict(record.search_attributes or {})
        attrs["mm_owner_type"] = record.owner_type.value
        attrs["mm_state"] = record.state.value
        attrs["mm_updated_at"] = _format_search_attribute_datetime(now)
        attrs["mm_entry"] = record.entry
        attrs["mm_owner_id"] = self._default_owner_id(record)
        integration_state = self._integration_state(record)
        if integration_state is None:
            attrs.pop("mm_integration", None)
            attrs.pop("mm_stage", None)
        else:
            attrs["mm_integration"] = integration_state["integration_name"]
            attrs["mm_stage"] = integration_state.get("normalized_status", "unknown")
        record.search_attributes = attrs

    def _update_summary(
        self,
        record: TemporalExecutionCanonicalRecord,
        summary: str,
        *,
        error_category: str | None = None,
        external_url: str | None = None,
        continue_as_new_cause: str | None = None,
    ) -> None:
        memo = dict(record.memo or {})
        memo["summary"] = summary
        if error_category:
            memo["error_category"] = error_category
        elif "error_category" in memo and "integration_error:" not in summary:
            memo.pop("error_category", None)
        if continue_as_new_cause:
            memo["continue_as_new_cause"] = continue_as_new_cause
            memo["latest_temporal_run_id"] = record.run_id
            attrs = dict(record.search_attributes or {})
            attrs["mm_continue_as_new_cause"] = continue_as_new_cause
            record.search_attributes = attrs
        if record.input_ref:
            memo["input_ref"] = record.input_ref
        if record.manifest_ref:
            memo["manifest_ref"] = record.manifest_ref
        resolved_external_url = external_url
        if resolved_external_url is None:
            integration_state = self._integration_state(record)
            if integration_state is not None:
                resolved_external_url = integration_state.get("external_url")
        if resolved_external_url:
            memo["external_url"] = resolved_external_url
        else:
            memo.pop("external_url", None)
        record.memo = memo

    def _set_wait_metadata(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
        *,
        waiting_reason: str,
        attention_required: bool,
    ) -> None:
        if waiting_reason not in ALLOWED_WAITING_REASONS:
            supported = ", ".join(sorted(ALLOWED_WAITING_REASONS))
            raise TemporalExecutionValidationError(
                f"Unsupported waiting_reason '{waiting_reason}'. Supported values: {supported}"
            )
        memo = dict(record.memo or {})
        memo["waiting_reason"] = waiting_reason
        memo["attention_required"] = bool(attention_required)
        record.memo = memo

    def _clear_wait_metadata(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> None:
        memo = dict(record.memo or {})
        memo.pop("waiting_reason", None)
        memo.pop("attention_required", None)
        record.memo = memo

    def _resolve_owner_metadata(
        self,
        *,
        owner_id: UUID | str | None,
        owner_type: str | None,
    ) -> tuple[TemporalExecutionOwnerType, str]:
        owner_value = str(owner_id).strip() if owner_id is not None else ""
        if owner_type:
            try:
                owner_type_enum = TemporalExecutionOwnerType(owner_type)
            except ValueError as exc:
                supported = ", ".join(item.value for item in TemporalExecutionOwnerType)
                raise TemporalExecutionValidationError(
                    f"Unsupported owner type: {owner_type}. Supported values: {supported}"
                ) from exc
        elif owner_value:
            owner_type_enum = TemporalExecutionOwnerType.USER
        else:
            owner_type_enum = TemporalExecutionOwnerType.SYSTEM

        if owner_type_enum is TemporalExecutionOwnerType.USER:
            if not owner_value:
                raise TemporalExecutionValidationError(
                    "owner_id is required when owner_type is user"
                )
            return owner_type_enum, owner_value

        if owner_type_enum is TemporalExecutionOwnerType.SYSTEM:
            return (
                owner_type_enum,
                owner_value or TemporalExecutionOwnerType.SYSTEM.value,
            )

        if not owner_value:
            raise TemporalExecutionValidationError(
                "owner_id is required when owner_type is service"
            )
        return owner_type_enum, owner_value

    def _default_owner_id(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> str:
        if record.owner_type is TemporalExecutionOwnerType.SYSTEM:
            return TemporalExecutionOwnerType.SYSTEM.value
        return record.owner_id or TemporalExecutionOwnerType.SYSTEM.value

    def _integration_waiting_reason(self, state: dict[str, Any]) -> str:
        if bool(state.get("callback_supported")):
            return "external_callback"
        return "external_completion"

    def _integration_state(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> dict[str, Any] | None:
        raw = record.integration_state
        if not isinstance(raw, dict):
            return None
        return dict(raw)

    def _require_integration_state(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> dict[str, Any]:
        state = self._integration_state(record)
        if state is None:
            raise TemporalExecutionValidationError(
                "Execution is not configured for integration monitoring."
            )
        return state

    def _parse_integration_status(self, raw: str) -> str:
        normalized = str(raw or "").strip().lower()
        if normalized not in ALLOWED_INTEGRATION_STATUSES:
            supported = ", ".join(sorted(ALLOWED_INTEGRATION_STATUSES))
            raise TemporalExecutionValidationError(
                f"Unsupported normalized integration status '{raw}'. Supported values: {supported}"
            )
        return normalized

    def _normalize_integration_name(self, raw: str) -> str:
        normalized = str(raw or "").strip().lower()
        if not normalized:
            raise TemporalExecutionValidationError("integration_name is required")
        return normalized

    def _set_waiting_metadata(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
        *,
        waiting_reason: str,
        attention_required: bool,
    ) -> None:
        if waiting_reason not in ALLOWED_WAITING_REASONS:
            supported = ", ".join(sorted(ALLOWED_WAITING_REASONS))
            raise TemporalExecutionValidationError(
                f"Unsupported waitingReason '{waiting_reason}'. Supported values: {supported}"
            )
        record.awaiting_external = True
        record.waiting_reason = waiting_reason
        record.attention_required = attention_required

    def _clear_waiting_metadata(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> None:
        record.awaiting_external = False
        record.waiting_reason = None
        record.attention_required = False

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _require_text(self, value: Any, *, field_name: str) -> str:
        text = self._clean_text(value)
        if text is None:
            raise TemporalExecutionValidationError(f"{field_name} is required")
        return text

    def _merge_refs(
        self,
        existing: list[str] | tuple[str, ...],
        incoming: list[str] | tuple[str, ...],
    ) -> list[str]:
        merged: list[str] = []
        for ref in list(existing) + list(incoming):
            text = self._clean_text(ref)
            if text and text not in merged:
                merged.append(text)
        return merged

    def _set_poll_schedule(
        self,
        state: dict[str, Any],
        *,
        observed_at: datetime,
        recommended_poll_seconds: int | None,
        status_changed: bool = False,
    ) -> None:
        normalized_status = state.get("normalized_status")
        if normalized_status in TERMINAL_INTEGRATION_STATUSES:
            state["poll_interval_seconds"] = None
            state["next_poll_at"] = None
            return
        previous_interval = int(state.get("poll_interval_seconds") or 0)
        if recommended_poll_seconds is not None:
            base_interval = int(recommended_poll_seconds)
        elif previous_interval <= 0 or status_changed:
            base_interval = self._integration_poll_initial_seconds
        else:
            base_interval = min(
                previous_interval * 2,
                self._integration_poll_max_seconds,
            )

        jitter_bucket = (int(state.get("monitor_attempt_count", 0)) % 3) - 1
        jitter_multiplier = 1.0 + (
            self._integration_poll_jitter_ratio * 0.25 * jitter_bucket
        )
        interval = max(
            self._integration_poll_initial_seconds,
            min(
                self._integration_poll_max_seconds,
                int(round(base_interval * jitter_multiplier)),
            ),
        )
        state["poll_interval_seconds"] = interval
        state["next_poll_at"] = (observed_at + timedelta(seconds=interval)).isoformat()

    def _apply_external_event(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
        *,
        source: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        state = self._require_integration_state(record)
        integration_name = state["integration_name"]
        if self._normalize_integration_name(source) != integration_name:
            raise TemporalExecutionValidationError(
                f"ExternalEvent source '{source}' does not match active integration '{integration_name}'."
            )

        external_operation_id = self._clean_text(payload.get("external_operation_id"))
        if (
            external_operation_id is not None
            and external_operation_id != state["external_operation_id"]
        ):
            raise TemporalExecutionValidationError(
                "ExternalEvent external_operation_id does not match active integration state."
            )

        provider_event_id = self._clean_text(payload.get("provider_event_id"))
        seen_ids = list(state.get("provider_event_ids_seen", []))
        if provider_event_id and provider_event_id in seen_ids:
            self._update_summary(
                record,
                f"Ignored duplicate external event '{event_type}' from '{integration_name}'.",
                external_url=state.get("external_url"),
            )
            record.integration_state = state
            return

        observed_at = payload.get("observed_at")
        observed = observed_at if isinstance(observed_at, datetime) else _utc_now()
        incoming_status_raw = payload.get("normalized_status")
        current_status = self._parse_integration_status(state["normalized_status"])
        incoming_status = (
            self._parse_integration_status(incoming_status_raw)
            if incoming_status_raw is not None
            else current_status
        )
        status_changed = incoming_status != current_status
        if (
            current_status in TERMINAL_INTEGRATION_STATUSES
            and incoming_status not in TERMINAL_INTEGRATION_STATUSES
        ):
            self._update_summary(
                record,
                f"Ignored late non-terminal external event '{event_type}' from '{integration_name}'.",
                external_url=state.get("external_url"),
            )
            record.integration_state = state
            return

        if provider_event_id:
            seen_ids.append(provider_event_id)
            state["provider_event_ids_seen"] = seen_ids[-_SEEN_PROVIDER_EVENT_LIMIT:]
        state["normalized_status"] = incoming_status
        state["provider_status"] = self._clean_text(payload.get("provider_status"))
        state["last_observed_at"] = observed.isoformat()
        if payload.get("provider_summary"):
            state["provider_summary"] = dict(payload["provider_summary"])
        if payload.get("external_url") is not None:
            state["external_url"] = self._clean_text(payload.get("external_url"))
        self._set_poll_schedule(
            state,
            observed_at=observed,
            recommended_poll_seconds=None,
            status_changed=status_changed,
        )
        record.integration_state = state

        if incoming_status in TERMINAL_INTEGRATION_STATUSES:
            self._clear_waiting_metadata(record)
            if not record.paused:
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
        else:
            self._set_waiting_metadata(
                record,
                waiting_reason=self._integration_waiting_reason(state),
                attention_required=False,
            )
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)

        self._update_summary(
            record,
            f"Processed external event '{event_type}' from '{integration_name}'.",
            error_category=(
                "integration_error" if incoming_status == "failed" else None
            ),
            external_url=state.get("external_url"),
        )

    async def _sync_integration_correlation_record(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
    ) -> None:
        state = self._integration_state(record)
        if state is None:
            return

        integration_name = state["integration_name"]
        stmt = (
            select(TemporalIntegrationCorrelationRecord)
            .where(
                TemporalIntegrationCorrelationRecord.workflow_id == record.workflow_id,
                TemporalIntegrationCorrelationRecord.integration_name
                == integration_name,
            )
            .limit(1)
        )
        correlation = (await self._session.execute(stmt)).scalars().first()
        if correlation is None:
            correlation = TemporalIntegrationCorrelationRecord(
                integration_name=integration_name,
                correlation_id=state["correlation_id"],
                workflow_id=record.workflow_id,
                run_id=record.run_id,
                lifecycle_status="active",
            )
            self._session.add(correlation)

        correlation.correlation_id = state["correlation_id"]
        correlation.callback_correlation_key = state.get("callback_correlation_key")
        correlation.external_operation_id = state["external_operation_id"]
        correlation.run_id = record.run_id
        correlation.lifecycle_status = self._integration_lifecycle_status(record, state)
        correlation.expires_at = _utc_now() + timedelta(days=_CORRELATION_EXPIRY_DAYS)

    def _integration_lifecycle_status(
        self,
        record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
        state: dict[str, Any],
    ) -> str:
        if record.state is MoonMindWorkflowState.CANCELED:
            return "canceled"
        if record.state is MoonMindWorkflowState.FAILED:
            return "failed"
        if record.state is MoonMindWorkflowState.SUCCEEDED:
            return "succeeded"
        normalized_status = self._parse_integration_status(
            state.get("normalized_status", "unknown")
        )
        if normalized_status in TERMINAL_INTEGRATION_STATUSES:
            return normalized_status
        return "active"

    async def _find_integration_correlation(
        self,
        *,
        integration_name: str,
        callback_correlation_key: str,
    ) -> TemporalIntegrationCorrelationRecord | None:
        stmt = (
            select(TemporalIntegrationCorrelationRecord)
            .where(
                TemporalIntegrationCorrelationRecord.integration_name
                == self._normalize_integration_name(integration_name),
                TemporalIntegrationCorrelationRecord.callback_correlation_key
                == str(callback_correlation_key).strip(),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    def _parse_workflow_type(self, raw: str) -> TemporalWorkflowType:
        try:
            return TemporalWorkflowType(raw)
        except ValueError as exc:
            raise TemporalExecutionValidationError(
                f"Unsupported workflow type: {raw}"
            ) from exc

    def _parse_state(self, raw: str) -> MoonMindWorkflowState:
        try:
            return MoonMindWorkflowState(raw)
        except ValueError as exc:
            raise TemporalExecutionValidationError(f"Unsupported state: {raw}") from exc

    def _parse_owner_type(self, raw: str) -> TemporalExecutionOwnerType:
        candidate = str(raw).strip()
        try:
            return TemporalExecutionOwnerType(candidate)
        except ValueError as exc:
            supported = ", ".join(sorted(ALLOWED_OWNER_TYPES))
            raise TemporalExecutionValidationError(
                f"Unsupported ownerType '{raw}'. Supported values: {supported}"
            ) from exc

    def _parse_entry(self, raw: str) -> str:
        candidate = str(raw).strip()
        if candidate in ALLOWED_ENTRY_VALUES:
            return candidate
        supported = ", ".join(sorted(ALLOWED_ENTRY_VALUES))
        raise TemporalExecutionValidationError(
            f"Unsupported entry '{raw}'. Supported values: {supported}"
        )

    def _default_title_for_type(self, workflow_type: TemporalWorkflowType) -> str:
        if workflow_type is TemporalWorkflowType.MANIFEST_INGEST:
            return "Manifest Ingest"
        return "Run"

    async def _find_by_create_idempotency(
        self,
        *,
        idempotency_key: str,
        owner_id: str | None,
        owner_type: TemporalExecutionOwnerType,
        workflow_type: TemporalWorkflowType,
    ) -> TemporalExecutionCanonicalRecord | None:
        stmt = select(TemporalExecutionCanonicalRecord).where(
            TemporalExecutionCanonicalRecord.create_idempotency_key == idempotency_key,
            TemporalExecutionCanonicalRecord.owner_type == owner_type,
            TemporalExecutionCanonicalRecord.workflow_type == workflow_type,
        )
        if owner_id is None:
            stmt = stmt.where(TemporalExecutionCanonicalRecord.owner_id.is_(None))
        else:
            stmt = stmt.where(TemporalExecutionCanonicalRecord.owner_id == owner_id)
        return (await self._session.execute(stmt.limit(1))).scalars().first()

    async def _load_source_execution(
        self,
        workflow_id: str,
    ) -> TemporalExecutionCanonicalRecord | None:
        canonical_workflow_id = self.canonicalize_workflow_id(workflow_id)
        return await self._session.get(
            TemporalExecutionCanonicalRecord, canonical_workflow_id
        )

    async def _require_source_execution(
        self,
        workflow_id: str,
    ) -> TemporalExecutionCanonicalRecord:
        record = await self._load_source_execution(workflow_id)
        if record is None:
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {workflow_id} was not found"
            )
        return record

    async def _load_projection_execution(
        self,
        workflow_id: str,
        *,
        include_orphaned: bool,
    ) -> TemporalExecutionRecord | None:
        canonical_workflow_id = self.canonicalize_workflow_id(workflow_id)
        record = await self._session.get(TemporalExecutionRecord, canonical_workflow_id)
        if (
            record is not None
            and not include_orphaned
            and record.sync_state is TemporalExecutionProjectionSyncState.ORPHANED
        ):
            return None
        return record

    async def _require_projection_execution(
        self,
        workflow_id: str,
        *,
        include_orphaned: bool,
    ) -> TemporalExecutionRecord:
        record = await self._load_projection_execution(
            workflow_id,
            include_orphaned=include_orphaned,
        )
        if record is None:
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {workflow_id} was not found"
            )
        return record

    def _apply_filters(
        self,
        stmt: Select[Any],
        *,
        model: type[TemporalExecutionCanonicalRecord] | type[TemporalExecutionRecord],
        workflow_type: TemporalWorkflowType | None,
        owner_type: TemporalExecutionOwnerType | None,
        state: MoonMindWorkflowState | None,
        entry: str | None,
        owner_id: str | None,
        repo: str | None,
        integration: str | None,
    ) -> Select[Any]:
        if model is TemporalExecutionRecord:
            stmt = stmt.where(
                TemporalExecutionRecord.sync_state
                != TemporalExecutionProjectionSyncState.ORPHANED
            )
        if workflow_type:
            stmt = stmt.where(model.workflow_type == workflow_type)
        if owner_type:
            stmt = stmt.where(model.owner_type == owner_type)
        if state:
            stmt = stmt.where(model.state == state)
        if entry:
            stmt = stmt.where(model.entry == entry)
        if owner_id:
            stmt = stmt.where(model.owner_id == owner_id)
        if repo:
            stmt = stmt.where(model.search_attributes["mm_repo"].as_string() == repo)
        if integration:
            stmt = stmt.where(
                model.search_attributes["mm_integration"].as_string() == integration
            )
        return stmt

    def _decode_page_token(
        self, token: str | None, *, expected_scope: dict[str, str | None]
    ) -> int:
        if token is None:
            return 0
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            payload = json.loads(raw)
            offset = int(payload.get("offset", 0))
            if offset < 0:
                raise ValueError("offset must be non-negative")
            actual_scope = payload.get("scope")
            if actual_scope != expected_scope:
                raise ValueError("token scope mismatch")
            return offset
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise TemporalExecutionValidationError("Invalid nextPageToken") from exc

    def _encode_page_token(self, offset: int, *, scope: dict[str, str | None]) -> str:
        payload = {"offset": offset, "scope": scope}
        return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode(
            "ascii"
        )

    def _page_token_scope(
        self,
        *,
        workflow_type: TemporalWorkflowType | None,
        owner_type: TemporalExecutionOwnerType | None,
        state: MoonMindWorkflowState | None,
        owner_id: str | None,
        entry: str | None,
        repo: str | None,
        integration: str | None,
    ) -> dict[str, str | None]:
        return {
            "endpoint": "executions:list",
            "ordering": PAGINATION_ORDERING,
            "workflow_type": workflow_type.value if workflow_type else None,
            "owner_type": owner_type.value if owner_type else None,
            "state": state.value if state else None,
            "owner_id": owner_id,
            "entry": entry,
            "repo": repo,
            "integration": integration,
        }

    def _ensure_non_terminal(self, record: TemporalExecutionCanonicalRecord) -> None:
        if record.state not in NON_TERMINAL_STATES:
            raise TemporalExecutionValidationError(
                "Workflow is in a terminal state and cannot be progressed."
            )

    def _should_continue_as_new(self, record: TemporalExecutionCanonicalRecord) -> bool:
        if record.workflow_type is TemporalWorkflowType.RUN:
            return (
                int(record.step_count or 0) >= self._run_continue_as_new_step_threshold
                or int(record.wait_cycle_count or 0)
                >= self._run_continue_as_new_wait_cycle_threshold
            )
        if record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST:
            return (
                int(record.wait_cycle_count or 0)
                >= self._manifest_continue_as_new_phase_threshold
            )
        return False

    async def _sync_projection_best_effort(
        self,
        source: TemporalExecutionCanonicalRecord,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        snapshot = self._snapshot_source(source)
        temporal_client = None
        try:
            if settings.temporal.temporal_authoritative_read_enabled:
                try:
                    from moonmind.workflows.temporal.client import get_temporal_client

                    temporal_client = await get_temporal_client(
                        settings.temporal.address, settings.temporal.namespace
                    )
                except Exception:
                    logger.exception(
                        "Failed to initialize Temporal client for projection sync, using fallback source payload for %s",
                        source.workflow_id,
                    )

            projection = await self._upsert_projection_from_source(
                source,
                synced_at=None,
                temporal_client=temporal_client,
            )
            await self._session.commit()
            await self._session.refresh(projection)
            return projection
        except Exception as exc:
            await self._session.rollback()
            projection = await self._mark_projection_repair_pending_from_snapshot(
                snapshot,
                sync_error=str(exc),
            )
            if projection is not None:
                return projection
            return self._build_projection_fallback(
                snapshot,
                sync_error=str(exc),
            )
        finally:
            if temporal_client is not None:
                try:
                    await temporal_client.close()
                except Exception:
                    logger.exception(
                        "Failed to close Temporal client for projection sync %s",
                        source.workflow_id,
                    )

    async def _sync_projections_best_effort(
        self,
        sources: list[TemporalExecutionCanonicalRecord],
    ) -> list[TemporalExecutionRecord | TemporalExecutionCanonicalRecord]:
        if not sources:
            return []

        temporal_client = None
        synced_at = _utc_now()
        try:
            if settings.temporal.temporal_authoritative_read_enabled:
                try:
                    from moonmind.workflows.temporal.client import get_temporal_client

                    temporal_client = await get_temporal_client(
                        settings.temporal.address, settings.temporal.namespace
                    )
                except Exception:
                    logger.exception(
                        "Failed to initialize Temporal client for projection sync, using fallback source payload",
                    )

            projections = [
                await self._upsert_projection_from_source(
                    source,
                    synced_at=synced_at,
                    temporal_client=temporal_client,
                )
                for source in sources
            ]
            await self._session.commit()
            for projection in projections:
                await self._session.refresh(projection)
            return projections
        except Exception:
            await self._session.rollback()
            return [
                await self._sync_projection_best_effort(source) for source in sources
            ]
        finally:
            if temporal_client is not None:
                try:
                    await temporal_client.close()
                except Exception:
                    logger.exception(
                        "Failed to close shared Temporal client for projection sync batch",
                    )

    async def _mark_projection_repair_pending_from_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        sync_error: str,
    ) -> TemporalExecutionRecord | None:
        try:
            projection = await self._load_projection_execution(
                snapshot["workflow_id"],
                include_orphaned=True,
            )
            if projection is None:
                return None

            projection.sync_state = TemporalExecutionProjectionSyncState.REPAIR_PENDING
            projection.sync_error = sync_error[:1000] or "projection_sync_failed"
            projection.source_mode = (
                TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
            )
            await self._session.commit()
            await self._session.refresh(projection)
            return projection
        except Exception:
            await self._session.rollback()
            return None

    async def _projection_payload_from_temporal(
        self,
        handle_description: WorkflowExecutionDescription,
        source: TemporalExecutionCanonicalRecord | None = None,
    ) -> dict[str, Any]:
        memo = await handle_description.memo()

        status_map = {
            WorkflowExecutionStatus.COMPLETED: (
                MoonMindWorkflowState.SUCCEEDED,
                TemporalExecutionCloseStatus.COMPLETED,
            ),
            WorkflowExecutionStatus.FAILED: (
                MoonMindWorkflowState.FAILED,
                TemporalExecutionCloseStatus.FAILED,
            ),
            WorkflowExecutionStatus.CANCELED: (
                MoonMindWorkflowState.CANCELED,
                TemporalExecutionCloseStatus.CANCELED,
            ),
            WorkflowExecutionStatus.TERMINATED: (
                MoonMindWorkflowState.FAILED,
                TemporalExecutionCloseStatus.TERMINATED,
            ),
            WorkflowExecutionStatus.TIMED_OUT: (
                MoonMindWorkflowState.FAILED,
                TemporalExecutionCloseStatus.TIMED_OUT,
            ),
            WorkflowExecutionStatus.CONTINUED_AS_NEW: (
                MoonMindWorkflowState.SUCCEEDED,
                TemporalExecutionCloseStatus.COMPLETED,
            ),
        }

        state_value, close_status = status_map.get(
            handle_description.status,
            (MoonMindWorkflowState.EXECUTING, None),
        )

        workflow_type_str = handle_description.workflow_type
        try:
            workflow_type = TemporalWorkflowType(workflow_type_str)
        except ValueError:
            workflow_type = TemporalWorkflowType.RUN

        source_entry = source.entry if source is not None else None
        try:
            entry = self._parse_entry(str(memo.get("entry")))
        except Exception:
            fallback_entry = source_entry or WORKFLOW_ENTRY_BY_TYPE.get(
                workflow_type,
                "run",
            )
            logger.warning(
                "Invalid Temporal memo entry '%s' for %s; using fallback '%s'.",
                memo.get("entry"),
                handle_description.id,
                fallback_entry,
            )
            entry = fallback_entry

        search_attributes = {}
        try:
            raw_search_attributes = handle_description.search_attributes or {}
            for key, value in raw_search_attributes.items():
                raw_value = getattr(value, "data", value)
                if isinstance(raw_value, bytes):
                    try:
                        search_attributes[key] = json.loads(raw_value.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        search_attributes[key] = raw_value.decode(
                            "utf-8", errors="replace"
                        )
                else:
                    search_attributes[key] = raw_value
        except Exception:
            logger.exception(
                "Failed to decode Temporal search attributes for %s",
                handle_description.id,
            )
            search_attributes = {}

        artifact_refs = memo.get("artifact_refs", [])

        return {
            "workflow_id": handle_description.id,
            "run_id": handle_description.run_id,
            "namespace": handle_description.namespace,
            "workflow_type": workflow_type,
            "owner_id": source.owner_id if source is not None else memo.get("owner_id"),
            "owner_type": (
                source.owner_type
                if source is not None
                else TemporalExecutionOwnerType.USER
            ),
            "state": state_value,
            "close_status": close_status,
            "entry": entry,
            "search_attributes": search_attributes,
            "memo": dict(memo),
            "artifact_refs": artifact_refs,
            "input_ref": memo.get("input_ref"),
            "plan_ref": memo.get("plan_ref"),
            "manifest_ref": memo.get("manifest_ref"),
            "parameters": memo.get("parameters", {}),
            "integration_state": memo.get("integration_state"),
            "pending_parameters_patch": memo.get("pending_parameters_patch"),
            "paused": memo.get("paused", False),
            "awaiting_external": memo.get("awaiting_external", False),
            "waiting_reason": memo.get("waiting_reason"),
            "attention_required": memo.get("attention_required", False),
            "step_count": memo.get("step_count", 0),
            "wait_cycle_count": memo.get("wait_cycle_count", 0),
            "rerun_count": memo.get("rerun_count", 0),
            "create_idempotency_key": memo.get("create_idempotency_key"),
            "last_update_idempotency_key": memo.get("last_update_idempotency_key"),
            "last_update_response": memo.get("last_update_response"),
            "started_at": handle_description.start_time,
            "updated_at": _utc_now(),
            "closed_at": handle_description.close_time,
        }

    async def _upsert_projection_from_temporal(
        self,
        handle_description: WorkflowExecutionDescription,
        *,
        synced_at: datetime | None = None,
        source: TemporalExecutionCanonicalRecord | None = None,
    ) -> TemporalExecutionRecord:
        payload = await self._projection_payload_from_temporal(
            handle_description,
            source=source,
        )
        projection = await self._session.get(
            TemporalExecutionRecord, handle_description.id
        )
        previous_version = int(projection.projection_version or 0) if projection else 0
        if projection is None:
            projection = TemporalExecutionRecord(
                **payload,
                projection_version=1,
                last_synced_at=synced_at or _utc_now(),
                sync_state=TemporalExecutionProjectionSyncState.FRESH,
                sync_error=None,
                source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
            )
            self._session.add(projection)

        self._apply_projection_payload(projection, payload)
        projection.projection_version = max(previous_version + 1, 1)
        projection.last_synced_at = synced_at or _utc_now()
        projection.sync_state = TemporalExecutionProjectionSyncState.FRESH
        projection.sync_error = None
        projection.source_mode = (
            TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        return projection

    async def _upsert_projection_from_source(
        self,
        source: TemporalExecutionCanonicalRecord,
        *,
        synced_at: datetime | None = None,
        temporal_client=None,
    ) -> TemporalExecutionRecord:
        if (
            settings.temporal.temporal_authoritative_read_enabled
            and temporal_client is not None
        ):
            try:
                from moonmind.workflows.temporal.client import fetch_workflow_execution

                desc = await fetch_workflow_execution(
                    temporal_client,
                    source.workflow_id,
                )
                return await self._upsert_projection_from_temporal(
                    desc,
                    synced_at=synced_at,
                    source=source,
                )
            except Exception as exc:
                # If temporal fetch fails, fallback to source logic below
                logger.warning(
                    "Failed to fetch Temporal execution description for %s, falling back to source snapshot: %s",
                    source.workflow_id,
                    exc,
                    exc_info=True,
                )

        payload = self._projection_payload_from_source(source)
        projection = await self._load_projection_execution(
            source.workflow_id,
            include_orphaned=True,
        )
        previous_version = int(projection.projection_version or 0) if projection else 0
        if projection is None:
            projection = TemporalExecutionRecord(
                **payload,
                projection_version=1,
                last_synced_at=synced_at or _utc_now(),
                sync_state=TemporalExecutionProjectionSyncState.FRESH,
                sync_error=None,
                source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
            )
            self._session.add(projection)

        self._apply_projection_payload(projection, payload)
        projection.projection_version = max(previous_version + 1, 1)
        projection.last_synced_at = synced_at or _utc_now()
        projection.sync_state = TemporalExecutionProjectionSyncState.FRESH
        projection.sync_error = None
        projection.source_mode = (
            TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        return projection

    def _build_projection_fallback(
        self,
        source: dict[str, Any],
        *,
        sync_error: str,
    ) -> TemporalExecutionRecord:
        return TemporalExecutionRecord(
            **source,
            projection_version=0,
            last_synced_at=source["updated_at"],
            sync_state=TemporalExecutionProjectionSyncState.REPAIR_PENDING,
            sync_error=sync_error[:1000] or "projection_sync_failed",
            source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
        )

    def _snapshot_source(
        self,
        source: TemporalExecutionCanonicalRecord,
    ) -> dict[str, Any]:
        return self._projection_payload_from_source(source)

    def _projection_payload_from_source(
        self,
        source: TemporalExecutionCanonicalRecord,
    ) -> dict[str, Any]:
        return {
            "workflow_id": source.workflow_id,
            "run_id": source.run_id,
            "namespace": source.namespace,
            "workflow_type": source.workflow_type,
            "owner_id": source.owner_id,
            "owner_type": source.owner_type,
            "state": source.state,
            "close_status": source.close_status,
            "entry": source.entry,
            "search_attributes": dict(source.search_attributes or {}),
            "memo": dict(source.memo or {}),
            "artifact_refs": list(source.artifact_refs or []),
            "input_ref": source.input_ref,
            "plan_ref": source.plan_ref,
            "manifest_ref": source.manifest_ref,
            "parameters": dict(source.parameters or {}),
            "integration_state": (
                dict(source.integration_state)
                if isinstance(source.integration_state, dict)
                else None
            ),
            "pending_parameters_patch": (
                dict(source.pending_parameters_patch)
                if isinstance(source.pending_parameters_patch, dict)
                else None
            ),
            "paused": source.paused,
            "awaiting_external": source.awaiting_external,
            "waiting_reason": source.waiting_reason,
            "attention_required": source.attention_required,
            "step_count": source.step_count,
            "wait_cycle_count": source.wait_cycle_count,
            "rerun_count": source.rerun_count,
            "create_idempotency_key": source.create_idempotency_key,
            "last_update_idempotency_key": source.last_update_idempotency_key,
            "last_update_response": (
                dict(source.last_update_response)
                if isinstance(source.last_update_response, dict)
                else None
            ),
            "started_at": source.started_at,
            "updated_at": source.updated_at,
            "closed_at": source.closed_at,
        }

    def _apply_projection_payload(
        self,
        projection: TemporalExecutionRecord,
        payload: dict[str, Any],
    ) -> None:
        for field, value in payload.items():
            setattr(projection, field, value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_search_attribute_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()
