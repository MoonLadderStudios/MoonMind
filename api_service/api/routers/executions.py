"""Temporal execution lifecycle API router."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import TemporalExecutionCloseStatus, User
from moonmind.config.settings import settings
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    CreateExecutionRequest,
    ExecutionListResponse,
    ExecutionModel,
    SignalExecutionRequest,
    UpdateExecutionRequest,
    UpdateExecutionResponse,
)
from moonmind.schemas.manifest_ingest_models import (
    ManifestNodePageModel,
    ManifestStatusSnapshotModel,
)
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    build_manifest_status_snapshot,
)

router = APIRouter(prefix="/api/executions", tags=["executions"])


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
    temporal_status = "running"
    close_status = record.close_status.value if record.close_status else None
    memo = dict(record.memo or {})
    search_attributes = dict(record.search_attributes or {})
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

    manifest_status = None
    if record.workflow_type.value == "MoonMind.ManifestIngest":
        manifest_status = build_manifest_status_snapshot(record)

    return ExecutionModel(
        namespace=record.namespace,
        task_id=record.workflow_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        workflow_type=record.workflow_type.value,
        state=record.state.value,
        temporal_status=temporal_status,
        close_status=close_status,
        search_attributes=search_attributes,
        memo=memo,
        artifact_refs=list(record.artifact_refs or []),
        manifest_artifact_ref=(
            manifest_status.manifest_artifact_ref if manifest_status else record.manifest_ref
        ),
        plan_artifact_ref=manifest_status.plan_artifact_ref if manifest_status else None,
        summary_artifact_ref=(
            manifest_status.summary_artifact_ref if manifest_status else None
        ),
        run_index_artifact_ref=(
            manifest_status.run_index_artifact_ref if manifest_status else None
        ),
        checkpoint_artifact_ref=(
            manifest_status.checkpoint_artifact_ref if manifest_status else None
        ),
        requested_by=manifest_status.requested_by if manifest_status else None,
        execution_policy=manifest_status.execution_policy if manifest_status else None,
        phase=manifest_status.phase if manifest_status else None,
        paused=manifest_status.paused if manifest_status else None,
        counts=manifest_status.counts if manifest_status else None,
        latest_run_view=True,
        continue_as_new_cause=continue_as_new_cause,
        started_at=record.started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
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
    payload: CreateExecutionRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    try:
        record = await service.create_execution(
            workflow_type=payload.workflow_type,
            owner_id=user.id,
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
    state: Optional[str] = Query(None, alias="state"),
    owner_id: Optional[UUID] = Query(None, alias="ownerId"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionListResponse:
    if _is_execution_admin(user):
        effective_owner = str(owner_id) if owner_id else None
    else:
        if owner_id is not None and str(owner_id) != _owner_id(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another user.",
                },
            )
        effective_owner = _owner_id(user)

    try:
        result = await service.list_executions(
            workflow_type=workflow_type,
            state=state,
            owner_id=effective_owner,
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


@router.post(
    "/{workflow_id}/update",
    response_model=UpdateExecutionResponse,
    response_model_exclude_none=True,
)
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

    return UpdateExecutionResponse.model_validate(response)


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
