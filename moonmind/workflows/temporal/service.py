"""Temporal execution lifecycle service.

This module implements the workflow type catalog and lifecycle contract described in
`docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
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

ALLOWED_OWNER_TYPES: set[str] = {"user", "system", "service"}
ALLOWED_ENTRY_VALUES: set[str] = {"run", "manifest"}
ALLOWED_UPDATE_NAMES: set[str] = {"UpdateInputs", "SetTitle", "RequestRerun"}
ALLOWED_SIGNAL_NAMES: set[str] = {"ExternalEvent", "Approve", "Pause", "Resume"}
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
PAGINATION_ORDERING = "mm_updated_at_desc__workflow_id_desc"

DASHBOARD_STATUS_BY_STATE: dict[MoonMindWorkflowState, str] = {
    MoonMindWorkflowState.INITIALIZING: "queued",
    MoonMindWorkflowState.PLANNING: "queued",
    MoonMindWorkflowState.EXECUTING: "running",
    MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    MoonMindWorkflowState.FINALIZING: "running",
    MoonMindWorkflowState.SUCCEEDED: "succeeded",
    MoonMindWorkflowState.FAILED: "failed",
    MoonMindWorkflowState.CANCELED: "cancelled",
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
        run_continue_as_new_step_threshold: int = 500,
        manifest_continue_as_new_phase_threshold: int = 5,
    ) -> None:
        self._session = session
        self._namespace = namespace
        self._run_continue_as_new_step_threshold = run_continue_as_new_step_threshold
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
    ) -> TemporalExecutionRecord:
        workflow_type_enum = self._parse_workflow_type(workflow_type)
        resolved_owner_type, owner = self._resolve_owner(
            owner_type=owner_type,
            owner_id=owner_id,
        )

        if workflow_type_enum is TemporalWorkflowType.MANIFEST_INGEST:
            if not manifest_artifact_ref:
                raise TemporalExecutionValidationError(
                    "manifestArtifactRef is required for MoonMind.ManifestIngest"
                )

        if idempotency_key:
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_type=resolved_owner_type,
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
            "mm_owner_type": resolved_owner_type,
            "mm_owner_id": owner,
            "mm_state": MoonMindWorkflowState.INITIALIZING.value,
            "mm_updated_at": _format_search_attribute_datetime(now),
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
            owner_type=resolved_owner_type,
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
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if not idempotency_key:
                raise
            existing = await self._find_by_create_idempotency(
                idempotency_key=idempotency_key,
                owner_type=resolved_owner_type,
                owner_id=owner,
                workflow_type=workflow_type_enum,
            )
            if existing is None:
                raise exc
            return existing
        await self._session.refresh(record)
        return record

    async def list_executions(
        self,
        *,
        workflow_type: str | None,
        owner_type: str | None,
        state: str | None,
        owner_id: UUID | str | None,
        entry: str | None,
        page_size: int,
        next_page_token: str | None,
    ) -> TemporalExecutionListResult:
        owner = str(owner_id) if owner_id is not None else None
        page_size = max(1, min(page_size, 200))
        workflow_type_enum = (
            self._parse_workflow_type(workflow_type) if workflow_type else None
        )
        owner_type_value = self._parse_owner_type(owner_type) if owner_type else None
        state_enum = self._parse_state(state) if state else None
        entry_value = self._parse_entry(entry) if entry else None
        query_scope = self._page_token_scope(
            workflow_type=workflow_type_enum,
            owner_type=owner_type_value,
            state=state_enum,
            owner_id=owner,
            entry=entry_value,
        )
        offset = self._decode_page_token(next_page_token, expected_scope=query_scope)

        stmt = select(TemporalExecutionRecord)
        stmt = self._apply_filters(
            stmt,
            workflow_type=workflow_type_enum,
            owner_type=owner_type_value,
            state=state_enum,
            owner_id=owner,
            entry=entry_value,
        )
        stmt = stmt.order_by(
            TemporalExecutionRecord.updated_at.desc(),
            TemporalExecutionRecord.workflow_id.desc(),
        )
        stmt = stmt.offset(offset).limit(page_size + 1)

        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > page_size
        items = rows[:page_size]
        await self._repair_projection_records(items)

        next_token = (
            self._encode_page_token(offset + page_size, scope=query_scope)
            if has_more
            else None
        )

        count_stmt = select(func.count()).select_from(TemporalExecutionRecord)
        count_stmt = self._apply_filters(
            count_stmt,
            workflow_type=workflow_type_enum,
            owner_type=owner_type_value,
            state=state_enum,
            owner_id=owner,
            entry=entry_value,
        )
        count = int((await self._session.execute(count_stmt)).scalar_one())

        return TemporalExecutionListResult(
            items=items,
            next_page_token=next_token,
            count=count,
        )

    async def describe_execution(self, workflow_id: str) -> TemporalExecutionRecord:
        canonical_workflow_id = self.canonicalize_workflow_id(workflow_id)
        record = await self._session.get(TemporalExecutionRecord, canonical_workflow_id)
        if record is None:
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {canonical_workflow_id} was not found"
            )
        await self._session.refresh(record)
        await self._repair_projection_records([record])
        return record

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
            if not record.paused:
                self._clear_waiting_metadata(record)
                self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(
                record,
                f"Processed external event '{event_type}' from '{source}'.",
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
            self._update_summary(record, "Execution paused.")
        elif signal_name == "Resume":
            record.paused = False
            self._clear_waiting_metadata(record)
            self._set_state(record, MoonMindWorkflowState.EXECUTING)
            self._update_summary(record, "Execution resumed.")

        await self._session.commit()
        await self._session.refresh(record)
        return record

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

        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def mark_execution_awaiting_external(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
        waiting_reason: str | None = None,
        attention_required: bool | None = None,
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        resolved_waiting_reason = waiting_reason or "unknown_external"
        resolved_attention_required = (
            attention_required if attention_required is not None else False
        )
        self._set_waiting_metadata(
            record,
            waiting_reason=resolved_waiting_reason,
            attention_required=resolved_attention_required,
        )
        self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
        if summary:
            self._update_summary(record, summary)
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        if not completed_steps and not completed_wait_cycles:
            return record
        self._touch(record)

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new after lifecycle threshold.",
            )
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
        await self._session.commit()
        await self._session.refresh(record)
        return record

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
            )
            return {
                "accepted": True,
                "applied": "continue_as_new",
                "message": "Update applied via Continue-As-New.",
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
        )
        return {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
        }

    def _continue_as_new(
        self, record: TemporalExecutionRecord, *, summary: str
    ) -> None:
        record.run_id = str(uuid4())
        record.rerun_count = int(record.rerun_count or 0) + 1
        record.step_count = 0
        record.wait_cycle_count = 0
        record.paused = False
        self._clear_waiting_metadata(record)
        record.closed_at = None
        record.close_status = None
        record.pending_parameters_patch = None

        if record.workflow_type is TemporalWorkflowType.RUN:
            next_state = (
                MoonMindWorkflowState.EXECUTING
                if record.plan_ref
                else MoonMindWorkflowState.PLANNING
            )
        else:
            next_state = MoonMindWorkflowState.EXECUTING

        self._set_state(record, next_state)
        self._update_summary(record, summary)

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
        if state is not MoonMindWorkflowState.AWAITING_EXTERNAL:
            self._clear_waiting_metadata(record)
        self._touch(record)

    def _touch(self, record: TemporalExecutionRecord) -> None:
        now = _utc_now()
        record.updated_at = now

        attrs = dict(record.search_attributes or {})
        attrs["mm_state"] = record.state.value
        attrs["mm_updated_at"] = _format_search_attribute_datetime(now)
        attrs["mm_entry"] = record.entry
        attrs["mm_owner_type"] = record.owner_type
        attrs["mm_owner_id"] = record.owner_id
        record.search_attributes = attrs

    def _update_summary(
        self,
        record: TemporalExecutionRecord,
        summary: str,
        *,
        error_category: str | None = None,
    ) -> None:
        memo = dict(record.memo or {})
        memo["summary"] = summary
        if error_category:
            memo["error_category"] = error_category
        if record.input_ref:
            memo["input_ref"] = record.input_ref
        if record.manifest_ref:
            memo["manifest_ref"] = record.manifest_ref
        record.memo = memo

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

    def _parse_owner_type(self, raw: str) -> str:
        candidate = str(raw).strip()
        if candidate in ALLOWED_OWNER_TYPES:
            return candidate
        supported = ", ".join(sorted(ALLOWED_OWNER_TYPES))
        raise TemporalExecutionValidationError(
            f"Unsupported ownerType '{raw}'. Supported values: {supported}"
        )

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

    def _default_summary_for_state(self, state: MoonMindWorkflowState) -> str:
        if state is MoonMindWorkflowState.AWAITING_EXTERNAL:
            return "Execution is waiting on external input."
        if state is MoonMindWorkflowState.SUCCEEDED:
            return "Execution succeeded."
        if state is MoonMindWorkflowState.FAILED:
            return "Execution failed."
        if state is MoonMindWorkflowState.CANCELED:
            return "Execution canceled."
        if state is MoonMindWorkflowState.INITIALIZING:
            return "Execution initialized."
        return "Execution in progress."

    async def _find_by_create_idempotency(
        self,
        *,
        idempotency_key: str,
        owner_type: str,
        owner_id: str,
        workflow_type: TemporalWorkflowType,
    ) -> TemporalExecutionRecord | None:
        stmt = select(TemporalExecutionRecord).where(
            TemporalExecutionRecord.create_idempotency_key == idempotency_key,
            TemporalExecutionRecord.owner_type == owner_type,
            TemporalExecutionRecord.owner_id == owner_id,
            TemporalExecutionRecord.workflow_type == workflow_type,
        )
        return (await self._session.execute(stmt.limit(1))).scalars().first()

    def _apply_filters(
        self,
        stmt: Select[Any],
        *,
        workflow_type: TemporalWorkflowType | None,
        owner_type: str | None,
        state: MoonMindWorkflowState | None,
        owner_id: str | None,
        entry: str | None,
    ) -> Select[Any]:
        if workflow_type:
            stmt = stmt.where(TemporalExecutionRecord.workflow_type == workflow_type)
        if owner_type:
            stmt = stmt.where(TemporalExecutionRecord.owner_type == owner_type)
        if state:
            stmt = stmt.where(TemporalExecutionRecord.state == state)
        if owner_id:
            stmt = stmt.where(TemporalExecutionRecord.owner_id == owner_id)
        if entry:
            stmt = stmt.where(TemporalExecutionRecord.entry == entry)
        return stmt

    def _resolve_owner(
        self,
        *,
        owner_type: str | None,
        owner_id: UUID | str | None,
    ) -> tuple[str, str]:
        resolved_owner_type = (
            self._parse_owner_type(owner_type)
            if owner_type is not None
            else ("user" if owner_id is not None else "system")
        )
        raw_owner_id = str(owner_id).strip() if owner_id is not None else ""
        if resolved_owner_type == "system":
            return ("system", "system")
        if not raw_owner_id:
            raise TemporalExecutionValidationError(
                "ownerId is required when ownerType is user or service"
            )
        return (resolved_owner_type, raw_owner_id)

    def _set_waiting_metadata(
        self,
        record: TemporalExecutionRecord,
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

    def _clear_waiting_metadata(self, record: TemporalExecutionRecord) -> None:
        record.awaiting_external = False
        record.waiting_reason = None
        record.attention_required = False

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
        payload = {
            "offset": offset,
            "scope": scope,
        }
        return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode(
            "ascii"
        )

    def _page_token_scope(
        self,
        *,
        workflow_type: TemporalWorkflowType | None,
        owner_type: str | None,
        state: MoonMindWorkflowState | None,
        owner_id: str | None,
        entry: str | None,
    ) -> dict[str, str | None]:
        return {
            "endpoint": "executions:list",
            "ordering": PAGINATION_ORDERING,
            "workflow_type": workflow_type.value if workflow_type else None,
            "owner_type": owner_type,
            "state": state.value if state else None,
            "owner_id": owner_id,
            "entry": entry,
        }

    def _ensure_non_terminal(self, record: TemporalExecutionRecord) -> None:
        if record.state not in NON_TERMINAL_STATES:
            raise TemporalExecutionValidationError(
                "Workflow is in a terminal state and cannot be progressed."
            )

    def _should_continue_as_new(self, record: TemporalExecutionRecord) -> bool:
        if record.workflow_type is TemporalWorkflowType.RUN:
            return (
                int(record.step_count or 0) >= self._run_continue_as_new_step_threshold
            )
        if record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST:
            return (
                int(record.wait_cycle_count or 0)
                >= self._manifest_continue_as_new_phase_threshold
            )
        return False

    async def _repair_projection_records(
        self, records: list[TemporalExecutionRecord]
    ) -> None:
        changed_records = [
            record for record in records if self._repair_projection_record(record)
        ]
        if not changed_records:
            return
        await self._session.commit()
        for record in changed_records:
            await self._session.refresh(record)

    def _repair_projection_record(self, record: TemporalExecutionRecord) -> bool:
        changed = False

        canonical_entry = WORKFLOW_ENTRY_BY_TYPE[record.workflow_type]
        if record.entry != canonical_entry:
            record.entry = canonical_entry
            changed = True

        expected_owner_type, expected_owner_id = self._reconcile_owner_identity(record)
        if record.owner_type != expected_owner_type:
            record.owner_type = expected_owner_type
            changed = True
        if record.owner_id != expected_owner_id:
            record.owner_id = expected_owner_id
            changed = True

        if record.state is MoonMindWorkflowState.AWAITING_EXTERNAL:
            if not record.waiting_reason:
                record.waiting_reason = "unknown_external"
                changed = True
            if not record.awaiting_external:
                record.awaiting_external = True
                changed = True
        else:
            if record.awaiting_external:
                record.awaiting_external = False
                changed = True
            if record.waiting_reason is not None:
                record.waiting_reason = None
                changed = True
            if record.attention_required:
                record.attention_required = False
                changed = True

        memo = dict(record.memo or {})
        title = str(memo.get("title") or "").strip()
        summary = str(memo.get("summary") or "").strip()
        if not title:
            memo["title"] = self._default_title_for_type(record.workflow_type)
            changed = True
        if not summary:
            memo["summary"] = self._default_summary_for_state(record.state)
            changed = True
        if changed:
            record.memo = memo

        expected_attrs = {
            "mm_owner_type": record.owner_type,
            "mm_owner_id": record.owner_id,
            "mm_state": record.state.value,
            "mm_updated_at": _format_search_attribute_datetime(record.updated_at),
            "mm_entry": record.entry,
        }
        attrs = dict(record.search_attributes or {})
        for key, value in expected_attrs.items():
            if attrs.get(key) != value:
                attrs[key] = value
                changed = True
        if changed:
            record.search_attributes = attrs

        return changed

    def _reconcile_owner_identity(
        self, record: TemporalExecutionRecord
    ) -> tuple[str, str]:
        attrs = dict(record.search_attributes or {})
        candidate_owner_type = str(
            attrs.get("mm_owner_type") or record.owner_type or ""
        ).strip()
        candidate_owner_id = str(attrs.get("mm_owner_id") or record.owner_id or "").strip()

        if candidate_owner_type not in ALLOWED_OWNER_TYPES:
            if candidate_owner_id and candidate_owner_id != "unknown":
                candidate_owner_type = "user"
            else:
                candidate_owner_type = "system"

        if candidate_owner_type == "system":
            return ("system", "system")

        if not candidate_owner_id or candidate_owner_id == "unknown":
            candidate_owner_id = str(record.owner_id or "").strip()

        if not candidate_owner_id or candidate_owner_id == "unknown":
            return ("system", "system")

        return (candidate_owner_type, candidate_owner_id)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_search_attribute_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
