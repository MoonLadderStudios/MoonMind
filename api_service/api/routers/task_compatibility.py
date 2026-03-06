"""Task compatibility router for unified `/api/tasks/*` APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.task_compatibility_models import (
    TaskCompatibilityDetail,
    TaskCompatibilityListResponse,
)
from moonmind.workflows.tasks.compatibility import TaskCompatibilityService
from moonmind.workflows.tasks.source_mapping import (
    TaskResolutionAmbiguousError,
    TaskResolutionNotFoundError,
)

router = APIRouter(prefix="/api/tasks", tags=["task-compatibility"])


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskCompatibilityService:
    return TaskCompatibilityService(session)


def _raise_task_resolution_http_error(error: Exception) -> None:
    if isinstance(error, TaskResolutionAmbiguousError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ambiguous_task_source",
                "message": str(error),
                "sources": sorted(error.sources),
            },
        ) from error
    if isinstance(error, TaskResolutionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "task_not_found",
                "message": str(error),
            },
        ) from error
    raise error


@router.get("/list", response_model=TaskCompatibilityListResponse)
async def list_compatibility_tasks(
    *,
    source: Literal["queue", "orchestrator", "temporal", "all"] | None = Query(
        None, alias="source"
    ),
    entry: Literal["run", "manifest"] | None = Query(None, alias="entry"),
    workflow_type: str | None = Query(None, alias="workflowType"),
    status_filter: Literal[
        "queued",
        "running",
        "awaiting_action",
        "succeeded",
        "failed",
        "cancelled",
    ]
    | None = Query(None, alias="status"),
    owner_type: Literal["user", "system", "service"] | None = Query(
        None, alias="ownerType"
    ),
    owner_id: str | None = Query(None, alias="ownerId"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    cursor: str | None = Query(None, alias="cursor"),
    service: TaskCompatibilityService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskCompatibilityListResponse:
    return await service.list_tasks(
        user=user,
        source=source,
        entry=entry,
        workflow_type=workflow_type,
        status_filter=status_filter,
        owner_type=owner_type,
        owner_id=owner_id,
        page_size=page_size,
        cursor=cursor,
    )


@router.get("/{task_id}", response_model=TaskCompatibilityDetail)
async def get_compatibility_task_detail(
    task_id: str,
    *,
    source_hint: Literal["queue", "orchestrator", "temporal"] | None = Query(
        None, alias="source"
    ),
    service: TaskCompatibilityService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskCompatibilityDetail:
    try:
        return await service.get_task_detail(
            task_id=task_id,
            source_hint=source_hint,
            user=user,
        )
    except (TaskResolutionAmbiguousError, TaskResolutionNotFoundError) as exc:
        _raise_task_resolution_http_error(exc)
        raise
