"""REST router for recurring task schedules."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    RecurringTaskDefinition,
    RecurringTaskRun,
    RecurringTaskScopeType,
    User,
)
from api_service.services.recurring_tasks_service import (
    RecurringTaskAuthorizationError,
    RecurringTaskNotFoundError,
    RecurringTasksService,
    RecurringTaskValidationError,
)

router = APIRouter(prefix="/api/recurring-tasks", tags=["recurring-tasks"])
logger = logging.getLogger(__name__)


class RecurringTaskDefinitionModel(BaseModel):
    """Serialized recurring schedule definition."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    name: str = Field(..., alias="name")
    description: Optional[str] = Field(None, alias="description")
    enabled: bool = Field(..., alias="enabled")
    schedule_type: str = Field(..., alias="scheduleType")
    cron: str = Field(..., alias="cron")
    timezone: str = Field(..., alias="timezone")
    next_run_at: Optional[datetime] = Field(None, alias="nextRunAt")
    last_scheduled_for: Optional[datetime] = Field(None, alias="lastScheduledFor")
    last_dispatch_status: Optional[str] = Field(None, alias="lastDispatchStatus")
    last_dispatch_error: Optional[str] = Field(None, alias="lastDispatchError")
    owner_user_id: Optional[UUID] = Field(None, alias="ownerUserId")
    scope_type: str = Field(..., alias="scopeType")
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    target: dict[str, Any] = Field(default_factory=dict, alias="target")
    policy: dict[str, Any] = Field(default_factory=dict, alias="policy")
    version: int = Field(..., alias="version")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class RecurringTaskDefinitionListResponse(BaseModel):
    """List response for recurring definitions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[RecurringTaskDefinitionModel] = Field(
        default_factory=list, alias="items"
    )


class RecurringTaskRunModel(BaseModel):
    """Serialized recurring run history record."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    definition_id: UUID = Field(..., alias="definitionId")
    scheduled_for: datetime = Field(..., alias="scheduledFor")
    trigger: str = Field(..., alias="trigger")
    outcome: str = Field(..., alias="outcome")
    dispatch_attempts: int = Field(..., alias="dispatchAttempts")
    dispatch_after: Optional[datetime] = Field(None, alias="dispatchAfter")
    queue_job_id: Optional[UUID] = Field(None, alias="queueJobId")
    queue_job_type: Optional[str] = Field(None, alias="queueJobType")
    message: Optional[str] = Field(None, alias="message")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class RecurringTaskRunListResponse(BaseModel):
    """List response for recurring run history."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[RecurringTaskRunModel] = Field(default_factory=list, alias="items")


class CreateRecurringTaskRequest(BaseModel):
    """Request payload for creating recurring schedules."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    description: Optional[str] = Field(None, alias="description")
    enabled: bool = Field(True, alias="enabled")
    schedule_type: Literal["cron"] = Field("cron", alias="scheduleType")
    cron: str = Field(..., alias="cron")
    timezone: str = Field("UTC", alias="timezone")
    scope_type: Literal["personal", "global"] = Field("personal", alias="scopeType")
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    target: dict[str, Any] = Field(default_factory=dict, alias="target")
    policy: dict[str, Any] = Field(default_factory=dict, alias="policy")


class UpdateRecurringTaskRequest(BaseModel):
    """Request payload for updating recurring schedules."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, alias="name")
    description: Optional[str] = Field(None, alias="description")
    enabled: Optional[bool] = Field(None, alias="enabled")
    cron: Optional[str] = Field(None, alias="cron")
    timezone: Optional[str] = Field(None, alias="timezone")
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    target: Optional[dict[str, Any]] = Field(None, alias="target")
    policy: Optional[dict[str, Any]] = Field(None, alias="policy")


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> RecurringTasksService:
    return RecurringTasksService(session)


def _serialize_definition(
    definition: RecurringTaskDefinition,
) -> RecurringTaskDefinitionModel:
    return RecurringTaskDefinitionModel(
        id=definition.id,
        name=definition.name,
        description=definition.description,
        enabled=definition.enabled,
        schedule_type=definition.schedule_type.value,
        cron=definition.cron,
        timezone=definition.timezone,
        next_run_at=definition.next_run_at,
        last_scheduled_for=definition.last_scheduled_for,
        last_dispatch_status=definition.last_dispatch_status,
        last_dispatch_error=definition.last_dispatch_error,
        owner_user_id=definition.owner_user_id,
        scope_type=definition.scope_type.value,
        scope_ref=definition.scope_ref,
        target=dict(definition.target or {}),
        policy=dict(definition.policy or {}),
        version=int(definition.version or 1),
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def _serialize_run(run: RecurringTaskRun) -> RecurringTaskRunModel:
    return RecurringTaskRunModel(
        id=run.id,
        definition_id=run.definition_id,
        scheduled_for=run.scheduled_for,
        trigger=run.trigger.value,
        outcome=run.outcome.value,
        dispatch_attempts=run.dispatch_attempts,
        dispatch_after=run.dispatch_after,
        queue_job_id=run.queue_job_id,
        queue_job_type=run.queue_job_type,
        message=run.message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _require_operator_for_global_scope(
    *,
    scope: RecurringTaskScopeType,
    user: User,
) -> None:
    if scope is RecurringTaskScopeType.GLOBAL and not bool(
        getattr(user, "is_superuser", False)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "operator_role_required",
                "message": "Operator privileges are required for global schedules.",
            },
        )


def _log_route_exception(
    *, action: str, definition_id: UUID | None, user_id: UUID | None, exc: Exception
) -> None:
    logger.exception(
        "Recurring task endpoint failed",
        extra={
            "action": action,
            "definition_id": str(definition_id) if definition_id else None,
            "user_id": str(user_id) if user_id else None,
            "error_type": type(exc).__name__,
        },
    )


def _audit_schedule_action(
    *,
    action: str,
    outcome: str,
    user_id: UUID | None,
    definition_id: UUID | None = None,
    scope: str | None = None,
) -> None:
    logger.info(
        "Recurring schedule audit",
        extra={
            "action": action,
            "outcome": outcome,
            "user_id": str(user_id) if user_id else None,
            "definition_id": str(definition_id) if definition_id else None,
            "scope": scope,
        },
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RecurringTaskNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "recurring_task_not_found",
                "message": str(exc),
            },
        )
    if isinstance(exc, RecurringTaskAuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "recurring_task_forbidden",
                "message": str(exc),
            },
        )
    if isinstance(exc, RecurringTaskValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_recurring_task",
                "message": str(exc),
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "recurring_task_internal_error",
            "message": "Unexpected recurring task error.",
        },
    )


@router.get("", response_model=RecurringTaskDefinitionListResponse)
async def list_recurring_tasks(
    *,
    scope: Literal["personal", "global"] = Query("personal"),
    limit: int = Query(200, ge=1, le=500),
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskDefinitionListResponse:
    requested_scope = RecurringTaskScopeType(scope)
    _require_operator_for_global_scope(scope=requested_scope, user=user)
    user_id = getattr(user, "id", None)

    definitions = await service.list_definitions(
        scope=scope,
        user_id=user_id if isinstance(user_id, UUID) else None,
        limit=limit,
    )
    return RecurringTaskDefinitionListResponse(
        items=[_serialize_definition(item) for item in definitions]
    )


@router.post(
    "",
    response_model=RecurringTaskDefinitionModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurring_task(
    payload: CreateRecurringTaskRequest,
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskDefinitionModel:
    scope = RecurringTaskScopeType(payload.scope_type)
    _require_operator_for_global_scope(scope=scope, user=user)

    user_id = getattr(user, "id", None)
    owner_user_id = user_id if scope is RecurringTaskScopeType.PERSONAL else None
    if scope is RecurringTaskScopeType.PERSONAL and not isinstance(owner_user_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "owner_user_required",
                "message": "A persisted user id is required for personal schedules.",
            },
        )

    try:
        definition = await service.create_definition(
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            schedule_type=payload.schedule_type,
            cron=payload.cron,
            timezone=payload.timezone,
            scope_type=payload.scope_type,
            scope_ref=payload.scope_ref,
            owner_user_id=owner_user_id,
            target=payload.target,
            policy=payload.policy,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="create_recurring_task",
            definition_id=None,
            user_id=owner_user_id if isinstance(owner_user_id, UUID) else None,
            exc=exc,
        )
        _audit_schedule_action(
            action="recurring_schedule.create",
            outcome="failure",
            user_id=owner_user_id if isinstance(owner_user_id, UUID) else None,
            scope=payload.scope_type,
        )
        raise _map_error(exc) from exc

    _audit_schedule_action(
        action="recurring_schedule.create",
        outcome="success",
        user_id=owner_user_id if isinstance(owner_user_id, UUID) else None,
        definition_id=definition.id,
        scope=definition.scope_type.value,
    )
    return _serialize_definition(definition)


@router.get("/{definition_id}", response_model=RecurringTaskDefinitionModel)
async def get_recurring_task(
    definition_id: UUID,
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskDefinitionModel:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="get_recurring_task",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        raise _map_error(exc) from exc
    return _serialize_definition(definition)


@router.patch("/{definition_id}", response_model=RecurringTaskDefinitionModel)
async def update_recurring_task(
    definition_id: UUID,
    payload: UpdateRecurringTaskRequest,
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskDefinitionModel:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        updated = await service.update_definition(
            definition,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            cron=payload.cron,
            timezone=payload.timezone,
            scope_ref=payload.scope_ref,
            target=payload.target,
            policy=payload.policy,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="update_recurring_task",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        _audit_schedule_action(
            action="recurring_schedule.update",
            outcome="failure",
            user_id=user_id if isinstance(user_id, UUID) else None,
            definition_id=definition_id,
        )
        raise _map_error(exc) from exc
    _audit_schedule_action(
        action="recurring_schedule.update",
        outcome="success",
        user_id=user_id if isinstance(user_id, UUID) else None,
        definition_id=updated.id,
        scope=updated.scope_type.value,
    )
    return _serialize_definition(updated)


@router.post(
    "/{definition_id}/run",
    response_model=RecurringTaskRunModel,
    status_code=status.HTTP_201_CREATED,
)
async def run_recurring_task_now(
    definition_id: UUID,
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskRunModel:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        run = await service.create_manual_run(definition)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="run_recurring_task_now",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        _audit_schedule_action(
            action="recurring_schedule.run_now",
            outcome="failure",
            user_id=user_id if isinstance(user_id, UUID) else None,
            definition_id=definition_id,
        )
        raise _map_error(exc) from exc
    _audit_schedule_action(
        action="recurring_schedule.run_now",
        outcome="success",
        user_id=user_id if isinstance(user_id, UUID) else None,
        definition_id=definition.id,
        scope=definition.scope_type.value,
    )
    return _serialize_run(run)


@router.get("/{definition_id}/runs", response_model=RecurringTaskRunListResponse)
async def list_recurring_task_runs(
    definition_id: UUID,
    *,
    limit: int = Query(200, ge=1, le=500),
    service: RecurringTasksService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringTaskRunListResponse:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        runs = await service.list_runs(definition_id=definition.id, limit=limit)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="list_recurring_task_runs",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        raise _map_error(exc) from exc

    return RecurringTaskRunListResponse(items=[_serialize_run(item) for item in runs])
