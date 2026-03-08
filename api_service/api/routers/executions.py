"""Temporal execution lifecycle API router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
    User,
)
from moonmind.config.settings import settings
from moonmind.schemas.agent_queue_models import CreateJobRequest
from moonmind.schemas.manifest_ingest_models import (
    ManifestNodePageModel,
    ManifestStatusSnapshotModel,
)
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    ConfigureIntegrationMonitoringRequest,
    CreateExecutionRequest,
    ExecutionActionCapabilityModel,
    ExecutionDebugFieldsModel,
    ExecutionListResponse,
    ExecutionModel,
    ExecutionRefreshEnvelope,
    PollIntegrationRequest,
    SignalExecutionRequest,
    UpdateExecutionRequest,
    UpdateExecutionResponse,
)
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    build_manifest_status_snapshot,
)

router = APIRouter(prefix="/api/executions", tags=["executions"])
_TEMPORAL_SOURCE = "temporal"
_ALLOWED_OWNER_TYPES = {"user", "system", "service"}
_DASHBOARD_STATUS_BY_STATE: dict[MoonMindWorkflowState, str] = {
    MoonMindWorkflowState.INITIALIZING: "queued",
    MoonMindWorkflowState.PLANNING: "running",
    MoonMindWorkflowState.EXECUTING: "running",
    MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    MoonMindWorkflowState.FINALIZING: "running",
    MoonMindWorkflowState.SUCCEEDED: "succeeded",
    MoonMindWorkflowState.FAILED: "failed",
    MoonMindWorkflowState.CANCELED: "cancelled",
}

_MAX_TASK_TITLE_LENGTH = 120
_MAX_TASK_SUMMARY_LENGTH = 180
_TASK_SUMMARY_ELLIPSIS = "..."


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _owner_id(user: User | None) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None else None


def _canonicalize_execution_identifier(raw_identifier: str) -> tuple[str, bool]:
    canonical = TemporalExecutionRecord.canonicalize_identifier(raw_identifier)
    return canonical, canonical != raw_identifier


def _mark_execution_alias_usage(
    response: Response, *, raw_identifier: str, canonical_identifier: str
) -> None:
    if raw_identifier == canonical_identifier:
        return
    response.headers["Deprecation"] = "true"
    response.headers["X-MoonMind-Canonical-WorkflowId"] = canonical_identifier
    response.headers["X-MoonMind-Deprecated-Identifier"] = raw_identifier


def _compatibility_refreshed_at(record) -> datetime:
    updated_at = record.updated_at
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if updated_at.tzinfo is not None:
        return updated_at
    return updated_at.replace(tzinfo=UTC)


def _manifest_attr(manifest_status, field: str, default=None):
    return getattr(manifest_status, field, default) if manifest_status else default


def _normalize_owner_type(record, search_attributes: dict[str, object]) -> str:
    owner_type = str(search_attributes.get("mm_owner_type") or "").strip().lower()
    if owner_type in _ALLOWED_OWNER_TYPES:
        return owner_type
    owner_id = str(record.owner_id or "").strip().lower()
    return "system" if owner_id == "system" or not owner_id else "user"


def _resolve_execution_entry(record, search_attributes: dict[str, object]) -> str:
    entry = str(
        search_attributes.get("mm_entry") or getattr(record, "entry", "")
    ).strip()
    if entry:
        return entry.lower()

    workflow_type = str(
        getattr(getattr(record, "workflow_type", None), "value", "")
    ).lower()
    if workflow_type.endswith("manifestingest"):
        return "manifest"
    return "run"


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        integration_task_queue=settings.temporal.activity_integrations_task_queue,
        integration_poll_initial_seconds=(
            settings.temporal.integration_poll_initial_seconds
        ),
        integration_poll_max_seconds=settings.temporal.integration_poll_max_seconds,
        integration_poll_jitter_ratio=settings.temporal.integration_poll_jitter_ratio,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        run_continue_as_new_wait_cycle_threshold=(
            settings.temporal.run_continue_as_new_wait_cycle_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _serialize_execution(
    record, *, include_artifact_refs: bool = True
) -> ExecutionModel:
    temporal_status = "running"
    close_status = _enum_value(record.close_status)
    memo = dict(record.memo or {})
    search_attributes = dict(record.search_attributes or {})
    integration_state = getattr(record, "integration_state", None)
    state_value = _enum_value(record.state) or ""
    workflow_type_value = _enum_value(record.workflow_type) or ""
    continue_as_new_cause = memo.get("continue_as_new_cause") or search_attributes.get(
        "mm_continue_as_new_cause"
    )
    if record.close_status is TemporalExecutionCloseStatus.COMPLETED:
        temporal_status = "completed"
    elif record.close_status is TemporalExecutionCloseStatus.CANCELED:
        temporal_status = "canceled"
    elif record.close_status in {
        TemporalExecutionCloseStatus.FAILED,
        TemporalExecutionCloseStatus.TERMINATED,
        TemporalExecutionCloseStatus.TIMED_OUT,
    }:
        temporal_status = "failed"

    raw_state = state_value
    manifest_status = None
    if workflow_type_value == "MoonMind.ManifestIngest":
        manifest_status = build_manifest_status_snapshot(record)
    owner_type = _normalize_owner_type(record, search_attributes)
    owner_id = str(search_attributes.get("mm_owner_id") or record.owner_id or "system")
    entry = _resolve_execution_entry(record, search_attributes)
    title = str(memo.get("title") or "").strip() or workflow_type_value
    summary = str(memo.get("summary") or "").strip() or "Execution updated."
    waiting_reason = (
        str(getattr(record, "waiting_reason", "") or "").strip()
        or str(memo.get("waiting_reason") or "").strip()
        or (
            str(memo.get("summary") or "").strip()
            if raw_state == "awaiting_external"
            else ""
        )
    )
    attention_required = bool(
        getattr(record, "attention_required", False)
        or memo.get("attention_required")
        or False
    )
    if raw_state == "awaiting_external":
        attention_required = True
    dashboard_status = _DASHBOARD_STATUS_BY_STATE.get(record.state, "queued")
    actions = _build_action_capabilities(record)
    debug_fields = _build_debug_fields(
        record=record,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )

    return ExecutionModel(
        task_id=record.workflow_id,
        namespace=record.namespace,
        source=_TEMPORAL_SOURCE,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        workflow_type=workflow_type_value,
        entry=entry or record.entry,
        owner_type=owner_type,
        owner_id=owner_id,
        title=title,
        summary=summary,
        status=dashboard_status,
        dashboard_status=dashboard_status,
        state=state_value,
        raw_state=raw_state,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=str(waiting_reason) if waiting_reason else None,
        attention_required=attention_required,
        search_attributes=search_attributes,
        memo=memo,
        artifact_refs=(
            list(record.artifact_refs or []) if include_artifact_refs else []
        ),
        manifest_artifact_ref=_manifest_attr(
            manifest_status,
            "manifest_artifact_ref",
            getattr(record, "manifest_ref", None),
        ),
        plan_artifact_ref=_manifest_attr(manifest_status, "plan_artifact_ref"),
        summary_artifact_ref=_manifest_attr(manifest_status, "summary_artifact_ref"),
        run_index_artifact_ref=_manifest_attr(
            manifest_status, "run_index_artifact_ref"
        ),
        checkpoint_artifact_ref=_manifest_attr(
            manifest_status, "checkpoint_artifact_ref"
        ),
        requested_by=_manifest_attr(manifest_status, "requested_by"),
        execution_policy=_manifest_attr(manifest_status, "execution_policy"),
        phase=_manifest_attr(manifest_status, "phase"),
        paused=_manifest_attr(manifest_status, "paused"),
        counts=_manifest_attr(manifest_status, "counts"),
        artifacts_count=len(record.artifact_refs or []),
        created_at=record.started_at,
        actions=actions,
        debug_fields=debug_fields,
        redirect_path=f"/tasks/{record.workflow_id}?source=temporal",
        integration=(
            dict(integration_state) if isinstance(integration_state, dict) else None
        ),
        latest_run_view=True,
        continue_as_new_cause=continue_as_new_cause,
        started_at=record.started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        detail_href=f"/tasks/{record.workflow_id}",
        ui_query_model="compatibility_adapter",
        stale_state=False,
        refreshed_at=_compatibility_refreshed_at(record),
    )


def _build_action_capabilities(record) -> ExecutionActionCapabilityModel:
    raw_state = str(record.state.value).strip().lower()
    if not settings.temporal_dashboard.actions_enabled:
        return ExecutionActionCapabilityModel(
            disabled_reasons={
                action: "actions_disabled"
                for action in (
                    "setTitle",
                    "updateInputs",
                    "rerun",
                    "approve",
                    "pause",
                    "resume",
                    "cancel",
                )
            }
        )

    state_actions = {
        "initializing": {"can_set_title", "can_update_inputs", "can_cancel"},
        "planning": {"can_set_title", "can_update_inputs", "can_cancel"},
        "executing": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "awaiting_external": {"can_approve", "can_pause", "can_resume", "can_cancel"},
        "finalizing": {"can_cancel"},
        "succeeded": {"can_rerun"},
        "failed": {"can_rerun"},
        "canceled": {"can_rerun"},
    }
    enabled = state_actions.get(raw_state, set())
    capability_values = {
        "can_set_title": "canSetTitle",
        "can_update_inputs": "canUpdateInputs",
        "can_rerun": "canRerun",
        "can_approve": "canApprove",
        "can_pause": "canPause",
        "can_resume": "canResume",
        "can_cancel": "canCancel",
    }
    disabled_reasons = {
        alias: "state_not_eligible"
        for field_name, alias in capability_values.items()
        if field_name not in enabled
    }
    return ExecutionActionCapabilityModel(
        can_set_title="can_set_title" in enabled,
        can_update_inputs="can_update_inputs" in enabled,
        can_rerun="can_rerun" in enabled,
        can_approve="can_approve" in enabled,
        can_pause="can_pause" in enabled,
        can_resume="can_resume" in enabled,
        can_cancel="can_cancel" in enabled,
        disabled_reasons=disabled_reasons,
    )


def _build_debug_fields(
    *,
    record,
    temporal_status: str,
    close_status: str | None,
    waiting_reason: str | None,
    attention_required: bool,
) -> ExecutionDebugFieldsModel | None:
    if not settings.temporal_dashboard.debug_fields_enabled:
        return None
    return ExecutionDebugFieldsModel(
        workflow_id=record.workflow_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        namespace=record.namespace,
        temporal_status=temporal_status,
        raw_state=record.state.value,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )


def _coerce_artifact_ref(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, dict):
        for key in ("artifactId", "artifact_id", "id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return None


def _invalid_task_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "invalid_execution_request",
            "message": message,
        },
    )


def _coerce_string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_task_request(f"{field_name} must be a JSON array of strings.")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _invalid_task_request(
                f"{field_name} must be a JSON array of strings."
            )
        candidate = item.strip()
        if candidate:
            normalized.append(candidate)
    return normalized


def _coerce_step_count(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, list):
        raise _invalid_task_request("payload.task.steps must be a JSON array.")
    return len(value)


def _derive_task_title(task_payload: dict[str, Any]) -> str | None:
    explicit = str(task_payload.get("title") or "").strip()
    if explicit:
        return explicit
    instructions = str(task_payload.get("instructions") or "").strip()
    if not instructions:
        return None
    first_line = instructions.splitlines()[0].strip()
    if not first_line:
        return None
    return first_line[:_MAX_TASK_TITLE_LENGTH]


def _derive_task_summary(
    task_payload: dict[str, Any], input_artifact_ref: str | None
) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    if instructions:
        normalized = " ".join(instructions.split())
        if len(normalized) > _MAX_TASK_SUMMARY_LENGTH:
            preview_length = _MAX_TASK_SUMMARY_LENGTH - len(_TASK_SUMMARY_ELLIPSIS)
            return f"{normalized[:preview_length]}{_TASK_SUMMARY_ELLIPSIS}"
        return normalized
    if input_artifact_ref:
        return f"Task instructions stored in artifact {input_artifact_ref}."
    return "Execution initialized."


async def _create_execution_from_task_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
) -> ExecutionModel:
    if str(request.type).strip().lower() != "task":
        raise _invalid_task_request(
            "Only task-shaped submit requests can be mapped to Temporal executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if not task_payload:
        raise _invalid_task_request(
            "Task-shaped Temporal submit requests require payload.task."
        )

    required_capabilities = _coerce_string_list(
        payload.get("requiredCapabilities"),
        field_name="payload.requiredCapabilities",
    )
    step_count = _coerce_step_count(task_payload.get("steps"))

    repository = str(payload.get("repository") or "").strip() or None
    integration = (
        str(
            payload.get("integration")
            or (payload.get("metadata") or {}).get("integration")
            or ""
        ).strip()
        or None
    )
    input_artifact_ref = _coerce_artifact_ref(
        task_payload.get("inputArtifactRef") or payload.get("inputArtifactRef")
    )
    plan_artifact_ref = _coerce_artifact_ref(
        task_payload.get("planArtifactRef") or payload.get("planArtifactRef")
    )
    manifest_artifact_ref = _coerce_artifact_ref(
        task_payload.get("manifestArtifactRef") or payload.get("manifestArtifactRef")
    )
    runtime_payload = (
        task_payload.get("runtime")
        if isinstance(task_payload.get("runtime"), dict)
        else {}
    )
    initial_parameters = {
        "requestType": request.type,
        "repository": repository,
        "requiredCapabilities": required_capabilities,
        "priority": request.priority,
        "maxAttempts": request.max_attempts,
        "targetRuntime": payload.get("targetRuntime") or runtime_payload.get("mode"),
        "model": runtime_payload.get("model"),
        "effort": runtime_payload.get("effort"),
        "publishMode": ((task_payload.get("publish") or {}).get("mode")),
        "proposeTasks": bool(task_payload.get("proposeTasks")),
        "stepCount": step_count,
    }

    try:
        record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=user.id,
            title=_derive_task_title(task_payload),
            input_artifact_ref=input_artifact_ref,
            plan_artifact_ref=plan_artifact_ref,
            manifest_artifact_ref=manifest_artifact_ref,
            failure_policy=None,
            initial_parameters=initial_parameters,
            idempotency_key=task_payload.get("idempotencyKey")
            or payload.get("idempotencyKey"),
            repository=repository,
            integration=integration,
            summary=_derive_task_summary(task_payload, input_artifact_ref),
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


async def _create_execution_from_manifest_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
) -> ExecutionModel:
    if str(request.type).strip().lower() != "manifest":
        raise _invalid_task_request(
            "Only manifest-shaped submit requests can be mapped to Temporal manifest executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    manifest_payload = (
        payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    )
    if not manifest_payload:
        raise _invalid_task_request(
            "Manifest-shaped Temporal submit requests require payload.manifest."
        )

    name = str(manifest_payload.get("name", "inline")).strip()
    action = str(manifest_payload.get("action", "run")).strip()
    options = manifest_payload.get("options", {})
    idempotency_key = str(payload.get("idempotencyKey") or "").strip() or None

    try:
        record = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=user.id,
            title=f"Manifest: {name}",
            summary=f"Manifest execution for {name} ({action})",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "manifestName": name,
                "action": action,
                "options": options,
                "systemPayload": {"manifest": manifest_payload},
            },
            idempotency_key=idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


async def _get_owned_execution(
    *,
    service: TemporalExecutionService,
    workflow_id: str,
    user: User,
):
    try:
        record = await service.describe_execution(workflow_id)
    except TemporalExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": str(exc),
            },
        ) from exc

    if _is_execution_admin(user):
        return record

    record_owner_type = _enum_value(getattr(record, "owner_type", None))
    if record_owner_type is None:
        record_owner_type = _normalize_owner_type(
            record, dict(getattr(record, "search_attributes", None) or {})
        )
    if record_owner_type != "user" or record.owner_id != _owner_id(user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": f"Workflow execution {workflow_id} was not found",
            },
        )

    return record


@router.post("", response_model=ExecutionModel, status_code=status.HTTP_201_CREATED)
async def create_execution(
    payload: dict[str, Any] = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    try:
        if "type" in payload and "payload" in payload:
            request = CreateJobRequest.model_validate(payload)
            return await _create_execution_from_task_request(
                request=request,
                service=service,
                user=user,
            )

        request = CreateExecutionRequest.model_validate(payload)
        record = await service.create_execution(
            workflow_type=request.workflow_type,
            owner_id=user.id,
            owner_type="user",
            title=request.title,
            input_artifact_ref=request.input_artifact_ref,
            plan_artifact_ref=request.plan_artifact_ref,
            manifest_artifact_ref=request.manifest_artifact_ref,
            failure_policy=request.failure_policy,
            initial_parameters=request.initial_parameters,
            idempotency_key=request.idempotency_key,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    *,
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    repo: Optional[str] = Query(None, alias="repo"),
    integration: Optional[str] = Query(None, alias="integration"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ExecutionListResponse:
    if _is_execution_admin(user):
        effective_owner_type = owner_type
        effective_owner = owner_id
    else:
        normalized_owner_type = str(owner_type or "").strip().lower()
        if owner_type is not None and owner_type != "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list non-user executions.",
                },
            )
        if owner_id is not None and owner_id != _owner_id(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another user.",
                },
            )
        effective_owner = _owner_id(user)
        effective_owner_type = "user" if normalized_owner_type == "user" else None

    try:
        result = await service.list_executions(
            workflow_type=workflow_type,
            state=state,
            entry=entry,
            owner_type=effective_owner_type,
            owner_id=effective_owner,
            repo=repo,
            integration=integration,
            page_size=page_size,
            next_page_token=next_page_token,
        )
        
        from moonmind.workflows.temporal.client import get_temporal_client, fetch_workflow_execution
        from api_service.core.sync import sync_execution_projection
        
        if settings.temporal.temporal_authoritative_read_enabled and result.items:
            try:
                client = await get_temporal_client(settings.temporal.address, settings.temporal.namespace)
                updated_items = []
                for item in result.items:
                    try:
                        desc = await fetch_workflow_execution(client, item.workflow_id)
                        updated_item = await sync_execution_projection(session, desc)
                        updated_items.append(updated_item)
                    except Exception:
                        updated_items.append(item)
                await session.commit()
                result.items = updated_items
            except Exception:
                pass
                
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_execution_query",
                "message": str(exc),
            },
        ) from exc

    return ExecutionListResponse(
        items=[
            _serialize_execution(item, include_artifact_refs=False)
            for item in result.items
        ],
        next_page_token=result.next_page_token,
        count=result.count,
        count_mode="exact",
        degraded_count=False,
        refreshed_at=max(
            (_compatibility_refreshed_at(item) for item in result.items),
            default=None,
        ),
    )


@router.get("/{workflow_id}", response_model=ExecutionModel)
async def describe_execution(
    workflow_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ExecutionModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    
    from moonmind.workflows.temporal.client import get_temporal_client, fetch_workflow_execution
    from api_service.core.sync import sync_execution_projection
    
    try:
        if settings.temporal.temporal_authoritative_read_enabled:
            client = await get_temporal_client(settings.temporal.address, settings.temporal.namespace)
            desc = await fetch_workflow_execution(client, canonical_workflow_id)
            await sync_execution_projection(session, desc)
            await session.commit()
    except Exception:
        pass

    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/update",
    response_model=UpdateExecutionResponse,
    response_model_exclude_none=True,
)
async def update_execution(
    workflow_id: str,
    payload: UpdateExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> UpdateExecutionResponse:
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )

    try:
        update_result = await service.update_execution(
            workflow_id=workflow_id,
            update_name=payload.update_name,
            input_artifact_ref=payload.input_artifact_ref,
            plan_artifact_ref=payload.plan_artifact_ref,
            parameters_patch=payload.parameters_patch,
            title=payload.title,
            new_manifest_artifact_ref=payload.new_manifest_artifact_ref,
            mode=payload.mode,
            max_concurrency=payload.max_concurrency,
            node_ids=payload.node_ids,
            idempotency_key=payload.idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_update_request",
                "message": str(exc),
            },
        ) from exc

    refreshed_record = await service.describe_execution(record.workflow_id)
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )

    return UpdateExecutionResponse(
        **update_result,
        execution=_serialize_execution(refreshed_record),
        refresh=ExecutionRefreshEnvelope(
            patched_execution=True,
            list_stale=True,
            refetch_suggested=True,
            refreshed_at=_compatibility_refreshed_at(refreshed_record),
        ),
    )


@router.get(
    "/{workflow_id}/manifest-status",
    response_model=ManifestStatusSnapshotModel,
)
async def describe_manifest_status(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestStatusSnapshotModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.describe_manifest_status(workflow_id)
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_manifest_status_request",
                "message": str(exc),
            },
        ) from exc


@router.get(
    "/{workflow_id}/manifest-nodes",
    response_model=ManifestNodePageModel,
)
async def list_manifest_node_page(
    workflow_id: str,
    state: Optional[str] = Query(None, alias="state"),
    cursor: Optional[str] = Query(None, alias="cursor"),
    limit: int = Query(50, alias="limit", ge=1, le=200),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestNodePageModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.list_manifest_nodes(
            workflow_id,
            state=state,
            cursor=cursor,
            limit=limit,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_manifest_nodes_request",
                "message": str(exc),
            },
        ) from exc


@router.post(
    "/{workflow_id}/integration",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def configure_integration_monitoring(
    workflow_id: str,
    payload: ConfigureIntegrationMonitoringRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.configure_integration_monitoring(
            workflow_id=workflow_id,
            integration_name=payload.integration_name,
            correlation_id=payload.correlation_id,
            external_operation_id=payload.external_operation_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            callback_supported=payload.callback_supported,
            callback_correlation_key=payload.callback_correlation_key,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_integration_monitoring_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/integration/poll",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_integration_poll(
    workflow_id: str,
    payload: PollIntegrationRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.record_integration_poll(
            workflow_id=workflow_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            observed_at=payload.observed_at,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
            completed_wait_cycles=payload.completed_wait_cycles,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_integration_poll_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/signal",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def signal_execution(
    workflow_id: str,
    payload: SignalExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.signal_execution(
            workflow_id=workflow_id,
            signal_name=payload.signal_name,
            payload=payload.payload,
            payload_artifact_ref=payload.payload_artifact_ref,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "signal_rejected",
                "message": str(exc),
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/cancel",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_execution(
    workflow_id: str,
    response: Response,
    payload: CancelExecutionRequest | None = None,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    request = payload or CancelExecutionRequest()
    record = await service.cancel_execution(
        workflow_id=workflow_id,
        reason=request.reason,
        graceful=request.graceful,
    )
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


__all__ = ["router"]
