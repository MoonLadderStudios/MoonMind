"""Temporal execution lifecycle API router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
    User,
)
from moonmind.config.settings import settings
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    ConfigureIntegrationMonitoringRequest,
    CreateExecutionRequest,
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
)

router = APIRouter(prefix="/api/executions", tags=["executions"])


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _owner_id(user: User | None) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None else None


def _dashboard_status(state: str) -> str:
    mapping = {
        "initializing": "queued",
        "planning": "queued",
        "executing": "running",
        "awaiting_external": "awaiting_action",
        "finalizing": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "canceled": "cancelled",
    }
    return mapping.get(state, "queued")


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
    if updated_at.tzinfo is not None:
        return updated_at
    return updated_at.replace(tzinfo=UTC)


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
    owner_type_value = _enum_value(record.owner_type) or "system"
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

    return ExecutionModel(
        namespace=record.namespace,
        task_id=record.workflow_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        workflow_type=workflow_type_value,
        entry=record.entry,
        owner_type=owner_type_value,
        owner_id=record.owner_id or "system",
        state=state_value,
        temporal_status=temporal_status,
        close_status=close_status,
        title=str(memo.get("title") or ""),
        summary=str(memo.get("summary") or ""),
        waiting_reason=record.waiting_reason,
        attention_required=bool(record.attention_required),
        dashboard_status=_dashboard_status(state_value),
        search_attributes=search_attributes,
        memo=memo,
        artifact_refs=list(record.artifact_refs or []) if include_artifact_refs else [],
        integration=(
            dict(integration_state) if isinstance(integration_state, dict) else None
        ),
        latest_run_view=True,
        continue_as_new_cause=continue_as_new_cause,
        started_at=record.started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        ui_query_model="compatibility_adapter",
        stale_state=False,
        refreshed_at=_compatibility_refreshed_at(record),
    )


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

    if record.owner_type.value != "user" or record.owner_id != _owner_id(user):
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
    payload: CreateExecutionRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    try:
        record = await service.create_execution(
            workflow_type=payload.workflow_type,
            owner_id=user.id,
            owner_type="user",
            title=payload.title,
            input_artifact_ref=payload.input_artifact_ref,
            plan_artifact_ref=payload.plan_artifact_ref,
            manifest_artifact_ref=payload.manifest_artifact_ref,
            failure_policy=payload.failure_policy,
            initial_parameters=payload.initial_parameters,
            idempotency_key=payload.idempotency_key,
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


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    *,
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionListResponse:
    if _is_execution_admin(user):
        effective_owner_type = owner_type
        effective_owner = owner_id
    else:
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
        effective_owner_type = "user"
        effective_owner = _owner_id(user)

    try:
        result = await service.list_executions(
            workflow_type=workflow_type,
            owner_type=effective_owner_type,
            state=state,
            owner_id=effective_owner,
            entry=entry,
            page_size=page_size,
            next_page_token=next_page_token,
        )
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
        degraded_count=0,
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
) -> ExecutionModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
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


@router.post("/{workflow_id}/update", response_model=UpdateExecutionResponse)
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
