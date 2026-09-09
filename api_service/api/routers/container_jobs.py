"""Authenticated owner-scoped HTTP surface for container jobs (MoonMind#3259).

These handlers perform no long-running Docker work and never wait for terminal
completion: submit starts a durable Temporal job and returns immediately, while
status/logs/artifacts/cancel are bounded owner-scoped operations over the same
``ContainerJobService`` the MCP transport calls. Docker authority stays in the
trusted worker.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.container_jobs import ContainerJobService
from moonmind.config.settings import settings
from moonmind.mcp.container_job_tool_registry import (
    classify_container_job_error,
    classify_gpu_request_error,
)
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

class _RedactedValidationRoute(APIRoute):
    """Keep rejected request values out of container-job error responses."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def redacted_route_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                # A refused GPU resource request keeps its stable generic class
                # so an HTTP caller classifies it exactly as an MCP caller does.
                # Only the class and a fixed message are returned; the rejected
                # value never leaves the service.
                normalized = classify_gpu_request_error(exc)
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={
                        "detail": {
                            "code": (
                                normalized.code
                                if normalized is not None
                                else "invalid_request"
                            ),
                            "message": (
                                normalized.message
                                if normalized is not None
                                else "Container-job request validation failed."
                            ),
                        }
                    },
                )

        return redacted_route_handler


router = APIRouter(
    prefix="/api/v1/container-jobs",
    tags=["container-jobs"],
    route_class=_RedactedValidationRoute,
)


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


async def _require_job_owner(
    request: Request,
    user: User | None = Depends(get_current_user_optional()),
) -> OwnerIdentity:
    """Resolve the container-job owner from browser or machine authority (#4126).

    Browser users keep working through the existing session path. Machine
    callers (managed sessions, MCP/container jobs dispatched through
    production) present the scoped container capability as a bearer token
    without browser cookies and stay bounded to the capability's owner.
    Invalid presented credentials fail as ``401 auth_invalid`` (never a
    silent fallback to another principal); conflicting user/machine owners
    fail as ``401 auth_conflict``; missing everything fails as ``401
    auth_required``. Runtime, session, and worker tokens are rejected by
    the capability verifier, never promoted to job authority.
    """
    from moonmind.security.container_job_capabilities import (
        verify_container_job_session_capability,
    )
    from moonmind.security.scoped_machine_auth_4126 import (
        ScopedWorkerAuthError,
        resolve_container_job_caller,
    )

    def _verify(token: str):
        return verify_container_job_session_capability(
            token, secret=str(settings.security.JWT_SECRET_KEY or "")
        )

    try:
        caller = await resolve_container_job_caller(
            user=user,
            authorization=request.headers.get("authorization"),
            verify_capability=_verify,
        )
    except ScopedWorkerAuthError as exc:
        code = getattr(exc, "code", None) or "auth_invalid"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": code, "message": str(exc)},
        ) from exc
    return caller.owner


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


def _audit_owner(request: Request, owner: OwnerIdentity, action: str, job_id: str | None) -> None:
    logger.info(
        "container_job_http action=%s transport=http principal=%s:%s "
        "request_id=%s job_id=%s",
        action,
        owner.principal_type,
        owner.principal_id,
        request.headers.get("x-request-id"),
        job_id,
    )


@router.post("", response_model=ContainerJobAccepted, response_model_by_alias=True)
async def submit_container_job(
    payload: ContainerJobSubmitRequest,
    request: Request,
    owner: OwnerIdentity = Depends(_require_job_owner),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobAccepted:
    """Create-or-replay a durable container job and start its Temporal workflow."""

    try:
        _require_ready()
        service = _build_service(session)
        accepted = await service.submit(owner=owner, request=payload)
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit_owner(request, owner, "submit", accepted.job_id)
    return accepted


@router.get(
    "/{job_id}", response_model=ContainerJobStatus, response_model_by_alias=True
)
async def get_container_job_status(
    job_id: str,
    request: Request,
    owner: OwnerIdentity = Depends(_require_job_owner),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobStatus:
    _require_ready()
    service = _build_service(session)
    try:
        snapshot = await service.status(owner=owner, job_id=job_id)
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit_owner(request, owner, "status", job_id)
    return snapshot


@router.get(
    "/{job_id}/logs", response_model=ContainerJobLogPage, response_model_by_alias=True
)
async def get_container_job_logs(
    job_id: str,
    request: Request,
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(100, ge=1, le=MAX_LOG_PAGE_ENTRIES),
    owner: OwnerIdentity = Depends(_require_job_owner),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobLogPage:
    _require_ready()
    service = _build_service(session)
    try:
        page = await service.logs(
            owner=owner,
            job_id=job_id,
            query=ContainerJobLogQuery(cursor=cursor, limit=limit),
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit_owner(request, owner, "logs", job_id)
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
    owner: OwnerIdentity = Depends(_require_job_owner),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobArtifactPage:
    _require_ready()
    service = _build_service(session)
    try:
        page = await service.artifacts(
            owner=owner, job_id=job_id, cursor=cursor, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit_owner(request, owner, "artifacts", job_id)
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
    owner: OwnerIdentity = Depends(_require_job_owner),
    session: AsyncSession = Depends(get_async_session),
) -> ContainerJobCancelResult:
    _require_ready()
    service = _build_service(session)
    try:
        result = await service.cancel(
            owner=owner, job_id=job_id, request=payload
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a stable problem detail
        raise _raise_from(exc) from exc
    _audit_owner(request, owner, "cancel", job_id)
    return result


__all__ = ["router", "container_jobs_ready", "_require_job_owner"]
