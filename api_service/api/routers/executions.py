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
    CreateExecutionRequest,
    ExecutionListResponse,
    ExecutionModel,
    ExecutionRefreshEnvelope,
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
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _serialize_execution(
    record, *, include_artifact_refs: bool = True
) -> ExecutionModel:
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

    memo = dict(record.memo or {})
    search_attributes = dict(record.search_attributes or {})
    state_value = record.state.value

    return ExecutionModel(
        namespace=record.namespace,
        task_id=record.workflow_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        workflow_type=record.workflow_type.value,
        entry=record.entry,
        owner_type=record.owner_type,
        owner_id=record.owner_id,
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

    if record.owner_type != "user" or record.owner_id != _owner_id(user):
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
        ui_query_model="compatibility_adapter",
        stale_state=False,
        degraded_count=False,
        refreshed_at=_utc_now(),
    )


@router.get("/{workflow_id}", response_model=ExecutionModel)
async def describe_execution(
    workflow_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    canonical_workflow_id, _ = _canonicalize_execution_identifier(workflow_id)
    _mark_execution_alias_usage(
        response,
        raw_identifier=workflow_id,
        canonical_identifier=canonical_workflow_id,
    )
    record = await _get_owned_execution(
        service=service,
        workflow_id=canonical_workflow_id,
        user=user,
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
    canonical_workflow_id, _ = _canonicalize_execution_identifier(workflow_id)
    _mark_execution_alias_usage(
        response,
        raw_identifier=workflow_id,
        canonical_identifier=canonical_workflow_id,
    )
    await _get_owned_execution(
        service=service,
        workflow_id=canonical_workflow_id,
        user=user,
    )

    try:
        update_result = await service.update_execution(
            workflow_id=canonical_workflow_id,
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

    record = await service.describe_execution(canonical_workflow_id)
    accepted = bool(update_result.get("accepted", False))
    return UpdateExecutionResponse(
        **update_result,
        execution=_serialize_execution(record),
        refresh=ExecutionRefreshEnvelope(
            ui_query_model="compatibility_adapter",
            patched_execution=accepted,
            list_stale=accepted,
            refetch_suggested=accepted,
            refreshed_at=_utc_now(),
        ),
    )


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
    canonical_workflow_id, _ = _canonicalize_execution_identifier(workflow_id)
    _mark_execution_alias_usage(
        response,
        raw_identifier=workflow_id,
        canonical_identifier=canonical_workflow_id,
    )
    await _get_owned_execution(
        service=service,
        workflow_id=canonical_workflow_id,
        user=user,
    )

    try:
        record = await service.signal_execution(
            workflow_id=canonical_workflow_id,
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
    response: Response,
    payload: CancelExecutionRequest | None = None,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    canonical_workflow_id, _ = _canonicalize_execution_identifier(workflow_id)
    _mark_execution_alias_usage(
        response,
        raw_identifier=workflow_id,
        canonical_identifier=canonical_workflow_id,
    )
    await _get_owned_execution(
        service=service,
        workflow_id=canonical_workflow_id,
        user=user,
    )

    request = payload or CancelExecutionRequest()
    record = await service.cancel_execution(
        workflow_id=canonical_workflow_id,
        reason=request.reason,
        graceful=request.graceful,
    )
    return _serialize_execution(record)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["router"]
