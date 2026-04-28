"""System operation endpoints for Settings -> Operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import WorkerPauseSnapshotResponse
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
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


def _require_operator(user: User) -> None:
    if settings.oidc.AUTH_PROVIDER == "disabled":
        return
    if bool(getattr(user, "is_superuser", False)):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "worker_operation_forbidden",
            "message": "Only operators can invoke worker operations.",
            "failureClass": "authorization_failure",
        },
    )


def _validation_error(exc: SystemOperationValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/worker-pause", response_model=WorkerPauseSnapshotResponse)
async def get_worker_pause_snapshot(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> WorkerPauseSnapshotResponse:
    return await SystemOperationsService(session).snapshot()


@router.post("/worker-pause", response_model=WorkerPauseSnapshotResponse)
async def submit_worker_pause_operation(
    payload: WorkerOperationCommand,
    session: AsyncSession = Depends(get_async_session),
    temporal_service: TemporalExecutionService = Depends(_get_temporal_execution_service),
    user: User = Depends(get_current_user()),
) -> WorkerPauseSnapshotResponse:
    _require_operator(user)
    service = SystemOperationsService(session, temporal_service=temporal_service)
    try:
        return await service.submit(payload, actor_user_id=getattr(user, "id", None))
    except SystemOperationValidationError as exc:
        raise _validation_error(exc) from exc
    except SystemOperationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
