"""REST router for recurring workflow schedules."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    RecurringWorkflowScopeType,
    User,
)
from api_service.services.recurring_workflows_service import (
    RecurringScheduleRuntimeSummary,
    RecurringWorkflowAuthorizationError,
    RecurringWorkflowNotFoundError,
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)

router = APIRouter(prefix="/api/recurring-workflows", tags=["recurring-workflows"])
logger = logging.getLogger(__name__)

class RecurringWorkflowActionPermissionsModel(BaseModel):
    """Action availability for a recurring schedule visible to the caller."""

    model_config = ConfigDict(populate_by_name=True)

    can_edit: bool = Field(..., alias="canEdit")
    can_run_now: bool = Field(..., alias="canRunNow")
    can_delete: bool = Field(False, alias="canDelete")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )

class RecurringWorkflowDefinitionModel(BaseModel):
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
    temporal_schedule_id: Optional[str] = Field(None, alias="temporalScheduleId")
    owner_user_id: Optional[UUID] = Field(None, alias="ownerUserId")
    scope_type: str = Field(..., alias="scopeType")
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    target: dict[str, Any] = Field(default_factory=dict, alias="target")
    policy: dict[str, Any] = Field(default_factory=dict, alias="policy")
    permissions: RecurringWorkflowActionPermissionsModel = Field(
        ..., alias="permissions"
    )
    actions: RecurringWorkflowActionPermissionsModel = Field(..., alias="actions")
    version: int = Field(..., alias="version")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

class RecurringWorkflowDefinitionListResponse(BaseModel):
    """List response for recurring definitions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[RecurringWorkflowDefinitionModel] = Field(
        default_factory=list, alias="items"
    )

class RecurringWorkflowRunModel(BaseModel):
    """Serialized recurring run history record."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    definition_id: UUID = Field(..., alias="definitionId")
    scheduled_for: datetime = Field(..., alias="scheduledFor")
    trigger: str = Field(..., alias="trigger")
    outcome: str = Field(..., alias="outcome")
    dispatch_attempts: int = Field(..., alias="dispatchAttempts")
    dispatch_after: Optional[datetime] = Field(None, alias="dispatchAfter")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    temporal_workflow_id: Optional[str] = Field(None, alias="temporalWorkflowId")
    temporal_run_id: Optional[str] = Field(None, alias="temporalRunId")
    message: Optional[str] = Field(None, alias="message")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

class RecurringWorkflowRunListResponse(BaseModel):
    """List response for recurring run history."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[RecurringWorkflowRunModel] = Field(default_factory=list, alias="items")

class CreateRecurringWorkflowRequest(BaseModel):
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

class UpdateRecurringWorkflowRequest(BaseModel):
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

from moonmind.workflows.temporal.client import TemporalClientAdapter

async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> RecurringWorkflowsService:
    return RecurringWorkflowsService(session, temporal_client_adapter=TemporalClientAdapter())

def _serialize_definition(
    definition: RecurringWorkflowDefinition,
    runtime_summary: RecurringScheduleRuntimeSummary | None = None,
    action_permissions: RecurringWorkflowActionPermissionsModel | None = None,
) -> RecurringWorkflowDefinitionModel:
    permissions = action_permissions or RecurringWorkflowActionPermissionsModel(
        can_edit=True,
        can_run_now=True,
        can_delete=True,
    )
    return RecurringWorkflowDefinitionModel(
        id=definition.id,
        name=definition.name,
        description=definition.description,
        enabled=definition.enabled,
        schedule_type=definition.schedule_type.value,
        cron=definition.cron,
        timezone=definition.timezone,
        next_run_at=(
            runtime_summary.next_run_at
            if runtime_summary is not None
            else definition.next_run_at
        ),
        last_scheduled_for=(
            runtime_summary.last_scheduled_for
            if runtime_summary is not None
            else definition.last_scheduled_for
        ),
        last_dispatch_status=(
            runtime_summary.last_dispatch_status
            if runtime_summary is not None
            else definition.last_dispatch_status
        ),
        last_dispatch_error=(
            runtime_summary.last_dispatch_error
            if runtime_summary is not None
            else definition.last_dispatch_error
        ),
        temporal_schedule_id=definition.temporal_schedule_id,
        owner_user_id=definition.owner_user_id,
        scope_type=definition.scope_type.value,
        scope_ref=definition.scope_ref,
        target=dict(definition.target or {}),
        policy=dict(definition.policy or {}),
        permissions=permissions,
        actions=permissions,
        version=int(definition.version or 1),
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )

def _action_permissions_for_definition(
    definition: RecurringWorkflowDefinition,
    *,
    user: User,
) -> RecurringWorkflowActionPermissionsModel:
    user_id = getattr(user, "id", None)
    is_operator = bool(getattr(user, "is_superuser", False))
    if definition.scope_type is RecurringWorkflowScopeType.GLOBAL:
        can_manage = is_operator
        manage_reason = "Operator privileges are required to manage global schedules."
    elif definition.scope_type is RecurringWorkflowScopeType.PERSONAL:
        can_manage = isinstance(user_id, UUID) and definition.owner_user_id == user_id
        manage_reason = "Only the schedule owner can manage this schedule."
    else:
        can_manage = is_operator
        manage_reason = "Operator privileges are required to manage this schedule."

    disabled_reasons: dict[str, str] = {}
    if not can_manage:
        disabled_reasons["canEdit"] = manage_reason
        disabled_reasons["canRunNow"] = manage_reason
        disabled_reasons["canDelete"] = manage_reason

    return RecurringWorkflowActionPermissionsModel(
        can_edit=can_manage,
        can_run_now=can_manage,
        can_delete=can_manage,
        disabled_reasons=disabled_reasons,
    )

def _serialize_run(
    run: RecurringWorkflowRun,
    *,
    started_at: datetime | None = None,
) -> RecurringWorkflowRunModel:
    return RecurringWorkflowRunModel(
        id=run.id,
        definition_id=run.definition_id,
        scheduled_for=run.scheduled_for,
        trigger=run.trigger.value,
        outcome=run.outcome.value,
        dispatch_attempts=run.dispatch_attempts,
        dispatch_after=run.dispatch_after,
        started_at=started_at,
        temporal_workflow_id=run.temporal_workflow_id,
        temporal_run_id=run.temporal_run_id,
        message=run.message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )

def _require_operator_for_global_scope(
    *,
    scope: RecurringWorkflowScopeType,
    user: User,
) -> None:
    if scope is RecurringWorkflowScopeType.GLOBAL and not bool(
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
        "Recurring workflow endpoint failed",
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
    if isinstance(exc, RecurringWorkflowNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "recurring_workflow_not_found",
                "message": str(exc),
            },
        )
    if isinstance(exc, RecurringWorkflowAuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "recurring_workflow_forbidden",
                "message": str(exc),
            },
        )
    if isinstance(exc, RecurringWorkflowValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_recurring_workflow",
                "message": str(exc),
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "recurring_workflow_internal_error",
            "message": "Unexpected recurring workflow error.",
        },
    )

@router.get("", response_model=RecurringWorkflowDefinitionListResponse)
async def list_recurring_workflows(
    *,
    scope: Literal["personal", "global"] = Query("personal"),
    limit: int = Query(200, ge=1, le=500),
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowDefinitionListResponse:
    requested_scope = RecurringWorkflowScopeType(scope)
    _require_operator_for_global_scope(scope=requested_scope, user=user)
    user_id = getattr(user, "id", None)

    definitions = await service.list_definitions(
        scope=scope,
        user_id=user_id if isinstance(user_id, UUID) else None,
        limit=limit,
    )
    runtime_summaries = await service.runtime_summaries_for_definitions(definitions)
    return RecurringWorkflowDefinitionListResponse(
        items=[
            _serialize_definition(
                item,
                runtime_summary=runtime_summaries.get(item.id),
                action_permissions=_action_permissions_for_definition(
                    item,
                    user=user,
                ),
            )
            for item in definitions
        ]
    )

@router.post(
    "",
    response_model=RecurringWorkflowDefinitionModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurring_workflow(
    payload: CreateRecurringWorkflowRequest,
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowDefinitionModel:
    scope = RecurringWorkflowScopeType(payload.scope_type)
    _require_operator_for_global_scope(scope=scope, user=user)

    user_id = getattr(user, "id", None)
    owner_user_id = user_id if scope is RecurringWorkflowScopeType.PERSONAL else None
    if scope is RecurringWorkflowScopeType.PERSONAL and not isinstance(owner_user_id, UUID):
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
            action="create_recurring_workflow",
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
    return _serialize_definition(
        definition,
        action_permissions=_action_permissions_for_definition(definition, user=user),
    )

@router.get("/{definition_id}", response_model=RecurringWorkflowDefinitionModel)
async def get_recurring_workflow(
    definition_id: UUID,
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowDefinitionModel:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        runtime_summary = await service.runtime_summary_for_definition(definition)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="get_recurring_workflow",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        raise _map_error(exc) from exc
    return _serialize_definition(
        definition,
        runtime_summary=runtime_summary,
        action_permissions=_action_permissions_for_definition(definition, user=user),
    )

@router.patch("/{definition_id}", response_model=RecurringWorkflowDefinitionModel)
async def update_recurring_workflow(
    definition_id: UUID,
    payload: UpdateRecurringWorkflowRequest,
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowDefinitionModel:
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
            action="update_recurring_workflow",
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
    return _serialize_definition(
        updated,
        action_permissions=_action_permissions_for_definition(updated, user=user),
    )

@router.post(
    "/{definition_id}/run",
    response_model=RecurringWorkflowRunModel,
    status_code=status.HTTP_201_CREATED,
)
async def run_recurring_workflow_now(
    definition_id: UUID,
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowRunModel:
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
            action="run_recurring_workflow_now",
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

@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recurring_workflow(
    definition_id: UUID,
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> Response:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        await service.delete_definition(definition)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="delete_recurring_workflow",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        _audit_schedule_action(
            action="recurring_schedule.delete",
            outcome="failure",
            user_id=user_id if isinstance(user_id, UUID) else None,
            definition_id=definition_id,
        )
        raise _map_error(exc) from exc
    _audit_schedule_action(
        action="recurring_schedule.delete",
        outcome="success",
        user_id=user_id if isinstance(user_id, UUID) else None,
        definition_id=definition.id,
        scope=definition.scope_type.value,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/{definition_id}/runs", response_model=RecurringWorkflowRunListResponse)
async def list_recurring_workflow_runs(
    definition_id: UUID,
    *,
    limit: int = Query(200, ge=1, le=500),
    service: RecurringWorkflowsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecurringWorkflowRunListResponse:
    user_id = getattr(user, "id", None)
    try:
        definition = await service.require_authorized_definition(
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            can_manage_global=bool(getattr(user, "is_superuser", False)),
        )
        runs = await service.list_runs(definition_id=definition.id, limit=limit)
        started_at_by_workflow_id = await service.started_at_by_workflow_id(
            item.temporal_workflow_id for item in runs if item.temporal_workflow_id
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        _log_route_exception(
            action="list_recurring_workflow_runs",
            definition_id=definition_id,
            user_id=user_id if isinstance(user_id, UUID) else None,
            exc=exc,
        )
        raise _map_error(exc) from exc

    return RecurringWorkflowRunListResponse(
        items=[
            _serialize_run(
                item,
                started_at=started_at_by_workflow_id.get(
                    str(item.temporal_workflow_id or "")
                ),
            )
            for item in runs
        ]
    )
