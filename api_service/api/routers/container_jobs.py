"""Authenticated owner-scoped HTTP surface for container jobs (MoonMind#3259).

These handlers perform no long-running Docker work and never wait for terminal
completion: submit starts a durable Temporal job and returns immediately, while
status/logs/artifacts/cancel are bounded owner-scoped operations over the same
``ContainerJobService`` the MCP transport calls. Docker authority stays in the
trusted worker.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.container_jobs import ContainerJobService
from moonmind.config.settings import settings
from moonmind.mcp.container_job_tool_registry import classify_container_job_error
from moonmind.schemas.container_job_models import (
    MAX_ARTIFACT_PAGE_ENTRIES,
    MAX_LOG_PAGE_ENTRIES,
    ContainerJobAccepted,
    ContainerJobArtifactPage,
    ContainerJobCancelRequest,
    ContainerJobCancelResult,
    ContainerJobLogPage,
    ContainerJobLogQuery,
    ContainerJobStatus,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.workflows import get_temporal_artifact_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/container-jobs", tags=["container-jobs"])


def container_jobs_ready() -> bool:
    """Return whether the authenticated container-job surface is enabled/ready."""

    return bool(settings.feature_flags.container_jobs_enabled)


def _require_ready() -> None:
    if not container_jobs_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backend_unavailable",
                "message": (
                    "The container-job service is not enabled on this "
                    "deployment."
                ),
            },
        )


def _owner(user: User) -> OwnerIdentity:
    return OwnerIdentity(principalId=str(user.id), principalType="user")


def _build_service(session: AsyncSession) -> ContainerJobService:
    return ContainerJobService(
        session, artifacts=get_temporal_artifact_service(session)
    )


def _raise_from(exc: Exception) -> HTTPException:
    normalized = classify_container_job_error(exc)
    return HTTPException(
        status_code=normalized.http_status,
        detail={"code": normalized.code, "message": normalized.message},
    )


def _audit(request: Request, user: User, action: str, job_id: str | None) -> None:
    logger.info(
        "container_job_http action=%s transport=http principal=user:%s "
        "request_id=%s job_id=%s",
        action,
        getattr(user, "id", None),
        request.headers.get("x-request-id"),
        job_id,
    )


@router.post("", response_model=ContainerJobAccepted, response_model_by_alias=True)
async def submit_container_job(
    payload: Annotated[Any, Body()],
    request: Request,
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobAccepted:
    """Create-or-replay a durable container job and start its Temporal workflow."""

    try:
        validated = ContainerJobSubmitRequest.model_validate(payload)
        _require_ready()
        service = _build_service(session)
        accepted = await service.submit(owner=_owner(user), request=validated)
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit(request, user, "submit", accepted.job_id)
    return accepted


@router.get(
    "/{job_id}", response_model=ContainerJobStatus, response_model_by_alias=True
)
async def get_container_job_status(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobStatus:
    _require_ready()
    service = _build_service(session)
    try:
        snapshot = await service.status(owner=_owner(user), job_id=job_id)
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit(request, user, "status", job_id)
    return snapshot


@router.get(
    "/{job_id}/logs", response_model=ContainerJobLogPage, response_model_by_alias=True
)
async def get_container_job_logs(
    job_id: str,
    request: Request,
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(100, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobLogPage:
    _require_ready()
    service = _build_service(session)
    try:
        page = await service.logs(
            owner=_owner(user),
            job_id=job_id,
            query=ContainerJobLogQuery(cursor=cursor, limit=limit),
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit(request, user, "logs", job_id)
    return page


@router.get(
    "/{job_id}/artifacts",
    response_model=ContainerJobArtifactPage,
    response_model_by_alias=True,
)
async def get_container_job_artifacts(
    job_id: str,
    request: Request,
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(MAX_ARTIFACT_PAGE_ENTRIES, ge=1, le=MAX_ARTIFACT_PAGE_ENTRIES),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobArtifactPage:
    _require_ready()
    service = _build_service(session)
    try:
        page = await service.artifacts(
            owner=_owner(user), job_id=job_id, cursor=cursor, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit(request, user, "artifacts", job_id)
    return page


@router.post(
    "/{job_id}/cancel",
    response_model=ContainerJobCancelResult,
    response_model_by_alias=True,
)
async def cancel_container_job(
    job_id: str,
    payload: ContainerJobCancelRequest,
    request: Request,
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobCancelResult:
    _require_ready()
    service = _build_service(session)
    try:
        result = await service.cancel(
            owner=_owner(user), job_id=job_id, request=payload
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit(request, user, "cancel", job_id)
    return result


__all__ = ["router", "container_jobs_ready"]
