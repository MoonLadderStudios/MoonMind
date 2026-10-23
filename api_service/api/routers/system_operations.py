"""System operation endpoints for Settings -> Operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.instance_access import access_principal
from api_service.api.schemas import WorkerPauseSnapshotResponse
from api_service.auth_providers import get_instance_access
from api_service.db.base import get_async_session
from api_service.services.system_operations import (
    SystemOperationUnavailableError,
    SystemOperationValidationError,
    SystemOperationsService,
    WorkerOperationCommand,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal import TemporalExecutionService


router = APIRouter(prefix="/api/system", tags=["system-operations"])


def _get_temporal_execution_service(
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
    )


def _validation_error(exc: SystemOperationValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/worker-pause", response_model=WorkerPauseSnapshotResponse)
async def get_worker_pause_snapshot(
    session: AsyncSession = Depends(get_async_session),
    temporal_service: TemporalExecutionService = Depends(_get_temporal_execution_service),
    user: Any = Depends(get_instance_access),
) -> WorkerPauseSnapshotResponse:
    return await SystemOperationsService(
        session, temporal_service=temporal_service
    ).snapshot()


@router.post("/worker-pause", response_model=WorkerPauseSnapshotResponse)
async def submit_worker_pause_operation(
    payload: WorkerOperationCommand,
    session: AsyncSession = Depends(get_async_session),
    temporal_service: TemporalExecutionService = Depends(_get_temporal_execution_service),
    user: Any = Depends(get_instance_access),
) -> WorkerPauseSnapshotResponse:
    service = SystemOperationsService(session, temporal_service=temporal_service)
    try:
        return await service.submit(
            payload, actor_user_id=access_principal(user) or None
        )
    except SystemOperationValidationError as exc:
        raise _validation_error(exc) from exc
    except SystemOperationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
