"""Temporal execution lifecycle API router."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import TemporalExecutionCloseStatus, User
from moonmind.config.settings import settings
from moonmind.schemas.agent_queue_models import CreateJobRequest
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    CreateExecutionRequest,
    ExecutionActionCapabilityModel,
    ExecutionDebugFieldsModel,
    ExecutionListResponse,
    ExecutionModel,
    SignalExecutionRequest,
    UpdateExecutionRequest,
    UpdateExecutionResponse,
)
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

router = APIRouter(prefix="/api/executions", tags=["executions"])

_MAX_TASK_TITLE_LENGTH = 120
_MAX_TASK_SUMMARY_LENGTH = 180
_TASK_SUMMARY_ELLIPSIS = "..."


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _owner_id(user: User | None) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None else None


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _serialize_execution(record) -> ExecutionModel:
    dashboard_status_map = {
        "initializing": "queued",
        "planning": "queued",
        "executing": "running",
        "awaiting_external": "awaiting_action",
        "finalizing": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "canceled": "cancelled",
    }
    temporal_status = "running"
    close_status = record.close_status.value if record.close_status else None
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

    raw_state = record.state.value
    waiting_reason = None
    if raw_state == "awaiting_external":
        waiting_reason = (
            str(dict(record.memo or {}).get("summary") or "").strip() or None
        )
    attention_required = raw_state == "awaiting_external"
    actions = _build_action_capabilities(record)
    debug_fields = _build_debug_fields(
        record=record,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )

    return ExecutionModel(
        namespace=record.namespace,
        source="temporal",
        task_id=record.workflow_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        workflow_type=record.workflow_type.value,
        dashboard_status=dashboard_status_map.get(raw_state, "queued"),
        state=record.state.value,
        raw_state=raw_state,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
        search_attributes=dict(record.search_attributes or {}),
        memo=dict(record.memo or {}),
        artifact_refs=list(record.artifact_refs or []),
        actions=actions,
        debug_fields=debug_fields,
        redirect_path=f"/tasks/{record.workflow_id}?source=temporal",
        started_at=record.started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
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

    if record.owner_id != _owner_id(user):
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
    state: Optional[str] = Query(None, alias="state"),
    entry: Optional[str] = Query(None, alias="entry"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    owner_id: Optional[UUID] = Query(None, alias="ownerId"),
    repo: Optional[str] = Query(None, alias="repo"),
    integration: Optional[str] = Query(None, alias="integration"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionListResponse:
    if _is_execution_admin(user):
        effective_owner = str(owner_id) if owner_id else None
        effective_owner_type = owner_type
    else:
        if owner_id is not None and str(owner_id) != _owner_id(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another user.",
                },
            )
        if owner_type not in {None, "", "user"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another owner type.",
                },
            )
        effective_owner = _owner_id(user)
        effective_owner_type = "user" if owner_type == "user" else None

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
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_pagination_token",
                "message": str(exc),
            },
        ) from exc

    return ExecutionListResponse(
        items=[_serialize_execution(item) for item in result.items],
        next_page_token=result.next_page_token,
        count=result.count,
        count_mode="exact",
    )


@router.get("/{workflow_id}", response_model=ExecutionModel)
async def describe_execution(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )
    return _serialize_execution(record)


@router.post("/{workflow_id}/update", response_model=UpdateExecutionResponse)
async def update_execution(
    workflow_id: str,
    payload: UpdateExecutionRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> UpdateExecutionResponse:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        response = await service.update_execution(
            workflow_id=workflow_id,
            update_name=payload.update_name,
            input_artifact_ref=payload.input_artifact_ref,
            plan_artifact_ref=payload.plan_artifact_ref,
            parameters_patch=payload.parameters_patch,
            title=payload.title,
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

    return UpdateExecutionResponse.model_validate(response)


@router.post(
    "/{workflow_id}/signal",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def signal_execution(
    workflow_id: str,
    payload: SignalExecutionRequest,
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

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/cancel",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_execution(
    workflow_id: str,
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
    return _serialize_execution(record)


__all__ = ["router"]
