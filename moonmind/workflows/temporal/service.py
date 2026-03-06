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
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
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

ALLOWED_UPDATE_NAMES: set[str] = {"UpdateInputs", "SetTitle", "RequestRerun"}
ALLOWED_SIGNAL_NAMES: set[str] = {"ExternalEvent", "Approve", "Pause", "Resume"}
ALLOWED_ERROR_CATEGORIES: set[str] = {
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
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
        owner_type_enum, owner = self._resolve_owner_metadata(
            owner_id=owner_id,
            owner_type=owner_type,
        )

        if workflow_type_enum is TemporalWorkflowType.MANIFEST_INGEST:
            if not manifest_artifact_ref:
                raise TemporalExecutionValidationError(
                    "manifestArtifactRef is required for MoonMind.ManifestIngest"
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
            "mm_owner_type": owner_type_enum.value,
            "mm_owner_id": owner,
            "mm_state": MoonMindWorkflowState.INITIALIZING.value,
            "mm_updated_at": now.isoformat(),
            "mm_entry": WORKFLOW_ENTRY_BY_TYPE[workflow_type_enum],
        }

        artifact_refs = [
            ref
            for ref in (input_artifact_ref, plan_artifact_ref, manifest_artifact_ref)
            if ref
        ]

        record = TemporalExecutionCanonicalRecord(
            workflow_id=workflow_id,
            run_id=run_id,
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
                owner_type=owner_type_enum,
                workflow_type=workflow_type_enum,
            )
            if existing is None:
                raise exc
            return await self._sync_projection_best_effort(existing)
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

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

        stmt = select(TemporalExecutionCanonicalRecord)
        stmt = self._apply_filters(
            stmt,
            model=TemporalExecutionCanonicalRecord,
            workflow_type=workflow_type_enum,
            state=state_enum,
            owner_id=owner,
        )
        stmt = stmt.order_by(
            TemporalExecutionCanonicalRecord.updated_at.desc(),
            TemporalExecutionCanonicalRecord.workflow_id.desc(),
        )
        stmt = stmt.offset(offset).limit(page_size + 1)

        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > page_size
        items = await self._sync_projections_best_effort(rows[:page_size])

        next_token = self._encode_page_token(offset + page_size) if has_more else None

        count_stmt = select(func.count()).select_from(TemporalExecutionCanonicalRecord)
        count_stmt = self._apply_filters(
            count_stmt,
            model=TemporalExecutionCanonicalRecord,
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

    async def describe_execution(
        self,
        workflow_id: str,
        *,
        include_orphaned: bool = False,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._load_source_execution(
            workflow_id,
        )
        if record is None:
            raise TemporalExecutionNotFoundError(
                f"Workflow execution {workflow_id} was not found"
            )
        if include_orphaned:
            projection = await self._load_projection_execution(
                workflow_id,
                include_orphaned=True,
            )
            if projection is not None:
                return projection
        return await self._sync_projection_best_effort(record)

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
        record = await self._require_source_execution(workflow_id)

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
        await self._sync_projection_best_effort(record)
        return response

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
            record.awaiting_external = False
            if not record.paused:
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

        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def cancel_execution(
        self,
        *,
        workflow_id: str,
        reason: str | None,
        graceful: bool,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)

        if record.state in TERMINAL_STATES:
            return await self._sync_projection_best_effort(record)

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
        await self._session.commit()
        await self._session.refresh(record)
        return await self._sync_projection_best_effort(record)

    async def mark_execution_awaiting_external(
        self,
        *,
        workflow_id: str,
        summary: str | None = None,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        record = await self._require_source_execution(workflow_id)
        self._ensure_non_terminal(record)
        record.awaiting_external = True
        self._set_state(record, MoonMindWorkflowState.AWAITING_EXTERNAL)
        if summary:
            self._update_summary(record, summary)
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
        self._touch(record)

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new after lifecycle threshold.",
            )
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
        record = await self._require_projection_execution(
            workflow_id,
            include_orphaned=True,
        )
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
        )
        return {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
        }

    def _continue_as_new(
        self, record: TemporalExecutionCanonicalRecord, *, summary: str
    ) -> None:
        record.run_id = str(uuid4())
        record.rerun_count = int(record.rerun_count or 0) + 1
        record.step_count = 0
        record.wait_cycle_count = 0
        record.paused = False
        record.awaiting_external = False
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
        record: TemporalExecutionCanonicalRecord,
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

    def _touch(self, record: TemporalExecutionCanonicalRecord) -> None:
        now = _utc_now()
        record.updated_at = now

        attrs = dict(record.search_attributes or {})
        attrs["mm_owner_type"] = record.owner_type.value
        attrs["mm_state"] = record.state.value
        attrs["mm_updated_at"] = now.isoformat()
        attrs["mm_entry"] = record.entry
        attrs["mm_owner_id"] = self._default_owner_id(record)
        record.search_attributes = attrs

    def _update_summary(
        self,
        record: TemporalExecutionCanonicalRecord,
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
        return await self._session.get(TemporalExecutionCanonicalRecord, workflow_id)

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
        record = await self._session.get(TemporalExecutionRecord, workflow_id)
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
        state: MoonMindWorkflowState | None,
        owner_id: str | None,
    ) -> Select[Any]:
        if model is TemporalExecutionRecord:
            stmt = stmt.where(
                TemporalExecutionRecord.sync_state
                != TemporalExecutionProjectionSyncState.ORPHANED
            )
        if workflow_type:
            stmt = stmt.where(model.workflow_type == workflow_type)
        if state:
            stmt = stmt.where(model.state == state)
        if owner_id:
            stmt = stmt.where(model.owner_id == owner_id)
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

    def _ensure_non_terminal(self, record: TemporalExecutionCanonicalRecord) -> None:
        if record.state not in NON_TERMINAL_STATES:
            raise TemporalExecutionValidationError(
                "Workflow is in a terminal state and cannot be progressed."
            )

    def _should_continue_as_new(self, record: TemporalExecutionCanonicalRecord) -> bool:
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

    async def _sync_projection_best_effort(
        self,
        source: TemporalExecutionCanonicalRecord,
    ) -> TemporalExecutionRecord | TemporalExecutionCanonicalRecord:
        snapshot = self._snapshot_source(source)
        try:
            projection = await self._upsert_projection_from_source(source)
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

    async def _sync_projections_best_effort(
        self,
        sources: list[TemporalExecutionCanonicalRecord],
    ) -> list[TemporalExecutionRecord | TemporalExecutionCanonicalRecord]:
        if not sources:
            return []

        synced_at = _utc_now()
        try:
            projections = [
                await self._upsert_projection_from_source(source, synced_at=synced_at)
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

    async def _upsert_projection_from_source(
        self,
        source: TemporalExecutionCanonicalRecord,
        *,
        synced_at: datetime | None = None,
    ) -> TemporalExecutionRecord:
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
            "pending_parameters_patch": (
                dict(source.pending_parameters_patch)
                if isinstance(source.pending_parameters_patch, dict)
                else None
            ),
            "paused": source.paused,
            "awaiting_external": source.awaiting_external,
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
