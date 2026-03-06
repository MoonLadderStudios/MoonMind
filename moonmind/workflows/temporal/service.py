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

ALLOWED_UPDATE_NAMES: set[str] = {"UpdateInputs", "SetTitle", "RequestRerun"}
ALLOWED_SIGNAL_NAMES: set[str] = {"ExternalEvent", "Approve", "Pause", "Resume"}
ALLOWED_ERROR_CATEGORIES: set[str] = {
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
}
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
        await self._session.refresh(record)
        return record

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
    ) -> TemporalExecutionRecord:
        record = await self.describe_execution(workflow_id)
        self._ensure_non_terminal(record)
        record.awaiting_external = True
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
        self._touch(record)

        if self._should_continue_as_new(record):
            self._continue_as_new(
                record,
                summary="Execution continued as new after lifecycle threshold.",
                cause="lifecycle_threshold",
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
        record.search_attributes = attrs

    def _update_summary(
        self,
        record: TemporalExecutionRecord,
        summary: str,
        *,
        error_category: str | None = None,
        continue_as_new_cause: str | None = None,
    ) -> None:
        memo = dict(record.memo or {})
        memo["summary"] = summary
        if error_category:
            memo["error_category"] = error_category
        if continue_as_new_cause:
            memo["continue_as_new_cause"] = continue_as_new_cause
            memo["latest_temporal_run_id"] = record.run_id
        if record.input_ref:
            memo["input_ref"] = record.input_ref
        if record.manifest_ref:
            memo["manifest_ref"] = record.manifest_ref
        record.memo = memo
        if continue_as_new_cause:
            attrs = dict(record.search_attributes or {})
            attrs["mm_continue_as_new_cause"] = continue_as_new_cause
            record.search_attributes = attrs

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
            )
        if record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST:
            return (
                int(record.wait_cycle_count or 0)
                >= self._manifest_continue_as_new_phase_threshold
            )
        return False


def _utc_now() -> datetime:
    return datetime.now(UTC)
