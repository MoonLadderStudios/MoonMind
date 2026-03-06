"""Temporal execution lifecycle service.

This module implements the workflow type catalog and lifecycle contract described in
`docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
    TemporalIntegrationCorrelationRecord,
    TemporalWorkflowType,
)

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

ALLOWED_UPDATE_NAMES: set[str] = {"UpdateInputs", "SetTitle", "RequestRerun"}
ALLOWED_SIGNAL_NAMES: set[str] = {"ExternalEvent", "Approve", "Pause", "Resume"}
ALLOWED_ERROR_CATEGORIES: set[str] = {
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
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

    items: list[TemporalExecutionRecord]
    next_page_token: str | None
    count: int


class TemporalExecutionService:
    """State machine + visibility facade for Temporal workflow executions."""

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
        title: str | None,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        manifest_artifact_ref: str | None,
        failure_policy: str | None,
        initial_parameters: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> TemporalExecutionRecord:
        workflow_type_enum = self._parse_workflow_type(workflow_type)
        owner = str(owner_id) if owner_id is not None else None

        if workflow_type_enum is TemporalWorkflowType.MANIFEST_INGEST:
            if not manifest_artifact_ref:
                raise TemporalExecutionValidationError(
                    "manifestArtifactRef is required for MoonMind.ManifestIngest"
                )

        if idempotency_key:
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_id=owner,
                workflow_type=workflow_type_enum,
            )
            if existing is not None:
                return existing

        now = _utc_now()
        workflow_id = f"mm:{uuid4()}"
        run_id = str(uuid4())
        params = dict(initial_parameters or {})
        if failure_policy:
            params.setdefault("failurePolicy", failure_policy)

        resolved_title = title or self._default_title_for_type(workflow_type_enum)
        memo = {
            "title": resolved_title,
            "summary": "Execution initialized.",
        }
        if input_artifact_ref:
            memo["input_ref"] = input_artifact_ref
        if manifest_artifact_ref:
            memo["manifest_ref"] = manifest_artifact_ref

        search_attributes = {
            "mm_owner_id": owner or "unknown",
            "mm_state": MoonMindWorkflowState.INITIALIZING.value,
            "mm_updated_at": now.isoformat(),
            "mm_entry": WORKFLOW_ENTRY_BY_TYPE[workflow_type_enum],
        }

        artifact_refs = [
            ref
            for ref in (input_artifact_ref, plan_artifact_ref, manifest_artifact_ref)
            if ref
        ]

        record = TemporalExecutionRecord(
            workflow_id=workflow_id,
            run_id=run_id,
            namespace=self._namespace,
            workflow_type=workflow_type_enum,
            owner_id=owner,
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
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if not idempotency_key:
                raise
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_id=owner,
                workflow_type=workflow_type_enum,
            )
            if existing is None:
                raise exc
            return existing
        return await self._refresh_and_detach_record(record)

    async def list_executions(
        self,
        *,
        workflow_type: str | None,
        state: str | None,
        owner_id: UUID | str | None,
        page_size: int,
        next_page_token: str | None,
    ) -> TemporalExecutionListResult:
        offset = self._decode_page_token(next_page_token)
        owner = str(owner_id) if owner_id is not None else None
        page_size = max(1, min(page_size, 200))
        workflow_type_enum = (
            self._parse_workflow_type(workflow_type) if workflow_type else None
        )
        state_enum = self._parse_state(state) if state else None

        stmt = select(TemporalExecutionRecord)
        stmt = self._apply_filters(
            stmt,
            workflow_type=workflow_type_enum,
            state=state_enum,
            owner_id=owner,
        )
        stmt = stmt.order_by(
            TemporalExecutionRecord.updated_at.desc(),
            TemporalExecutionRecord.workflow_id.desc(),
        )
        stmt = stmt.offset(offset).limit(page_size + 1)

        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > page_size
        items = rows[:page_size]

        next_token = self._encode_page_token(offset + page_size) if has_more else None

        count_stmt = select(func.count()).select_from(TemporalExecutionRecord)
        count_stmt = self._apply_filters(
            count_stmt,
            workflow_type=workflow_type_enum,
            state=state_enum,
            owner_id=owner,
        )
        count = int((await self._session.execute(count_stmt)).scalar_one())

        return TemporalExecutionListResult(
            items=items,
            next_page_token=next_token,
            count=count,
        )

    async def describe_execution(self, workflow_id: str) -> TemporalExecutionRecord:
        record = await self._session.get(TemporalExecutionRecord, workflow_id)
        if record is None:
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {workflow_id} was not found"
            )
        return record

    async def update_execution(
        self,
        *,
        workflow_id: str,
        update_name: str,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        parameters_patch: dict[str, Any] | None,
        title: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if update_name not in ALLOWED_UPDATE_NAMES:
            raise TemporalExecutionValidationError(
                f"Unsupported update name: {update_name}"
            )
        record = await self.describe_execution(workflow_id)

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

        if update_name == "UpdateInputs":
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

        if idempotency_key:
            record.last_update_idempotency_key = idempotency_key
            record.last_update_response = dict(response)

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        await self._session.refresh(record)
        return response

    async def signal_execution(
        self,
        *,
        workflow_id: str,
        signal_name: str,
        payload: dict[str, Any] | None,
        payload_artifact_ref: str | None,
    ) -> TemporalExecutionRecord:
        if signal_name not in ALLOWED_SIGNAL_NAMES:
            raise TemporalExecutionValidationError(
                f"Unsupported signal name: {signal_name}"
            )
        record = await self.describe_execution(workflow_id)

        if record.state in TERMINAL_STATES:
            raise TemporalExecutionValidationError(
                "Workflow is in a terminal state and no longer accepts signals."
            )

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
                record.awaiting_external = False
                if not record.paused:
                    self._set_state(record, MoonMindWorkflowState.EXECUTING)
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
            record.awaiting_external = False
            self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(record, "Approval signal received.")
        elif signal_name == "Pause":
            record.paused = True
            record.awaiting_external = True
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
            self._update_summary(record, "Execution paused.")
        elif signal_name == "Resume":
            record.paused = False
            record.awaiting_external = False
            self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(record, "Execution resumed.")

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

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
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
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
            record.awaiting_external = False
            if not record.paused:
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(
                record,
                f"Integration '{state['integration_name']}' is already {normalized}.",
                external_url=state.get("external_url"),
            )
        else:
            record.awaiting_external = True
            self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
            self._update_summary(
                record,
                f"Waiting on integration '{state['integration_name']}' ({normalized}).",
                external_url=state.get("external_url"),
            )

        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

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
    ) -> TemporalExecutionRecord:
        if completed_wait_cycles < 0:
            raise TemporalExecutionValidationError(
                "completed_wait_cycles must be non-negative."
            )

        record = await self.describe_execution(workflow_id)
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
            return await self._refresh_and_detach_record(record)

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
            record.awaiting_external = False
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
            record.awaiting_external = True
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
        return await self._refresh_and_detach_record(record)

    async def ingest_integration_callback(
        self,
        *,
        integration_name: str,
        callback_correlation_key: str,
        payload: dict[str, Any] | None,
        payload_artifact_ref: str | None,
    ) -> TemporalExecutionRecord:
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
    ) -> tuple[TemporalIntegrationCorrelationRecord, TemporalExecutionRecord]:
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
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)

        if record.state in TERMINAL_STATES:
            return record

        reason_text = (reason or "Canceled by user.").strip() or "Canceled by user."
        record.paused = False
        record.awaiting_external = False
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
        return await self._refresh_and_detach_record(record)

    async def mark_execution_succeeded(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
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
        return await self._refresh_and_detach_record(record)

    async def mark_execution_planning(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.PLANNING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

    async def mark_execution_executing(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.EXECUTING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

    async def mark_execution_awaiting_external(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        record.awaiting_external = True
        self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

    async def mark_execution_finalizing(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        self._set_state(record, MoonMindWorkflowState.FINALIZING)
        if summary:
            self._update_summary(record, summary)
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

    async def record_progress(
        self,
        *,
        workflow_id: str,
        completed_steps: int = 0,
        completed_wait_cycles: int = 0,
    ) -> TemporalExecutionRecord:
        if completed_steps < 0 or completed_wait_cycles < 0:
            raise TemporalExecutionValidationError(
                "Progress increments must be non-negative."
            )

        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)

        if completed_steps:
            record.step_count = int(record.step_count or 0) + completed_steps
        if completed_wait_cycles:
            record.wait_cycle_count = (
                int(record.wait_cycle_count or 0) + completed_wait_cycles
            )
        self._touch(record)

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new after lifecycle threshold.",
                cause="lifecycle_threshold",
            )
        await self._sync_integration_correlation_record(record)
        await self._session.commit()
        return await self._refresh_and_detach_record(record)

    async def mark_execution_failed(
        self,
        *,
        workflow_id: str,
        error_category: str,
        message: str,
    ) -> TemporalExecutionRecord:
        if error_category not in ALLOWED_ERROR_CATEGORIES:
            supported = ", ".join(sorted(ALLOWED_ERROR_CATEGORIES))
            raise TemporalExecutionValidationError(
                f"Unsupported error_category '{error_category}'. Supported values: {supported}"
            )
        record = await self.describe_execution(workflow_id)
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
        return await self._refresh_and_detach_record(record)

    def _apply_update_inputs(
        self,
        record: TemporalExecutionRecord,
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
        record: TemporalExecutionRecord,
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
        record: TemporalExecutionRecord,
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
        record: TemporalExecutionRecord,
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
        record.awaiting_external = integration_wait_active
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
        record: TemporalExecutionRecord,
        state: MoonMindWorkflowState,
        *,
        close_status: TemporalExecutionCloseStatus | None = None,
    ) -> None:
        if state in TERMINAL_STATES:
            enforced_status = close_status or TERMINAL_STATE_TO_CLOSE_STATUS[state]
            record.close_status = enforced_status
            record.closed_at = _utc_now()
        else:
            record.close_status = None
            record.closed_at = None

        record.state = state
        self._touch(record)

    def _touch(self, record: TemporalExecutionRecord) -> None:
        now = _utc_now()
        record.updated_at = now

        attrs = dict(record.search_attributes or {})
        attrs["mm_state"] = record.state.value
        attrs["mm_updated_at"] = now.isoformat()
        attrs.setdefault("mm_entry", record.entry)
        attrs.setdefault("mm_owner_id", record.owner_id or "unknown")
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
        record: TemporalExecutionRecord,
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

    def _integration_state(
        self, record: TemporalExecutionRecord
    ) -> dict[str, Any] | None:
        raw = record.integration_state
        if not isinstance(raw, dict):
            return None
        return dict(raw)

    def _require_integration_state(
        self, record: TemporalExecutionRecord
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
        record: TemporalExecutionRecord,
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
            record.awaiting_external = False
            if not record.paused:
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
        else:
            record.awaiting_external = True
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
        self, record: TemporalExecutionRecord
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
        record: TemporalExecutionRecord,
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

    def _default_title_for_type(self, workflow_type: TemporalWorkflowType) -> str:
        if workflow_type is TemporalWorkflowType.MANIFEST_INGEST:
            return "Manifest Ingest"
        return "Run"

    async def _find_by_create_idempotency(
        self,
        *,
        idempotency_key: str,
        owner_id: str | None,
        workflow_type: TemporalWorkflowType,
    ) -> TemporalExecutionRecord | None:
        stmt = select(TemporalExecutionRecord).where(
            TemporalExecutionRecord.create_idempotency_key == idempotency_key,
            TemporalExecutionRecord.workflow_type == workflow_type,
        )
        if owner_id is None:
            stmt = stmt.where(TemporalExecutionRecord.owner_id.is_(None))
        else:
            stmt = stmt.where(TemporalExecutionRecord.owner_id == owner_id)
        return (await self._session.execute(stmt.limit(1))).scalars().first()

    def _apply_filters(
        self,
        stmt: Select[Any],
        *,
        workflow_type: TemporalWorkflowType | None,
        state: MoonMindWorkflowState | None,
        owner_id: str | None,
    ) -> Select[Any]:
        if workflow_type:
            stmt = stmt.where(TemporalExecutionRecord.workflow_type == workflow_type)
        if state:
            stmt = stmt.where(TemporalExecutionRecord.state == state)
        if owner_id:
            stmt = stmt.where(TemporalExecutionRecord.owner_id == owner_id)
        return stmt

    def _decode_page_token(self, token: str | None) -> int:
        if token is None:
            return 0
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            payload = json.loads(raw)
            offset = int(payload.get("offset", 0))
            if offset < 0:
                raise ValueError("offset must be non-negative")
            return offset
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise TemporalExecutionValidationError("Invalid nextPageToken") from exc

    def _encode_page_token(self, offset: int) -> str:
        payload = {"offset": offset}
        return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode(
            "ascii"
        )

    def _ensure_non_terminal(self, record: TemporalExecutionRecord) -> None:
        if record.state not in NON_TERMINAL_STATES:
            raise TemporalExecutionValidationError(
                "Workflow is in a terminal state and cannot be progressed."
            )

    def _should_continue_as_new(self, record: TemporalExecutionRecord) -> bool:
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

    async def _refresh_and_detach_record(
        self, record: TemporalExecutionRecord
    ) -> TemporalExecutionRecord:
        await self._session.refresh(record)
        self._session.expunge(record)
        return record


def _utc_now() -> datetime:
    return datetime.now(UTC)
