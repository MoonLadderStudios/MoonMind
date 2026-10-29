"""Shared instance admission and provider credential composition."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from time import monotonic

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.operator_admission import require_operator
from moonmind.auth import AuthProviderManager, EnvAuthProvider, ProfileAuthProvider
from moonmind.security.operator_admission import (
    STREAM_REVALIDATION_SECONDS,
    OperatorAdmission,
)

logger = logging.getLogger(__name__)

_OPERATOR_ADMISSION = object()


def get_operator_recovery_principal(request: Request) -> str | None:
    """Only a server-admitted operator can use protected artifact recovery."""
    if getattr(request.state, "_operator_admission", None) is _OPERATOR_ADMISSION:
        return "operator"
    return None


def instance_operator_principal() -> str:
    """Retained artifact/quota scope; never a person or an admission credential."""
    return "00000000-0000-0000-0000-000000000000"


async def get_instance_access(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> OperatorAdmission:
    """Admit transport only after the guarded data conversion published."""
    if "authorization" in request.headers or any(
        name in request.headers
        for name in (
            "x-moonmind-execution-fanout",
            "x-moonmind-mcp-tools",
            "x-moonmind-worker-token",
            "x-moonmind-integration-token",
        )
    ):
        raise HTTPException(
            status_code=401, detail={"code": "auth_invalid"}
        )
    admission = await require_operator(request)
    request.state.operator_admitted_at = monotonic()
    from api_service.services.single_user_conversion import completed_guarded_upgrade

    try:
        completed = await completed_guarded_upgrade(session)
    except Exception as exc:
        logger.warning("Instance admission completion unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="unavailable") from exc
    if completed is None:
        raise HTTPException(status_code=503, detail="conversion_required")
    request.state._operator_admission = _OPERATOR_ADMISSION
    return admission


async def bounded_operator_stream(
    request: Request,
    caller: OperatorAdmission,
    stream: AsyncGenerator[str | bytes, None],
) -> AsyncGenerator[str | bytes, None]:
    """Expire cached admission even while a live source is idle or busy."""
    async with aclosing(stream):
        if not isinstance(caller, OperatorAdmission):
            async for chunk in stream:
                yield chunk
            return
        admitted_at = getattr(request.state, "operator_admitted_at", None)
        while admitted_at is not None:
            remaining = STREAM_REVALIDATION_SECONDS - (monotonic() - admitted_at)
            if remaining <= 0:
                return
            deadline = asyncio.timeout(remaining)
            try:
                async with deadline:
                    chunk = await anext(stream)
            except StopAsyncIteration:
                return
            except TimeoutError:
                if not deadline.expired():
                    raise
                return
            if monotonic() - admitted_at >= STREAM_REVALIDATION_SECONDS:
                return
            yield chunk


async def get_instance_access_optional(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> OperatorAdmission | None:
    """Defer marked fan-out to its owning capability verifier, granting nothing."""
    if request.headers.get("x-moonmind-execution-fanout", "").strip() == "v1":
        return None
    return await get_instance_access(request, session)


async def get_artifact_access(
    request: Request,
    admission: OperatorAdmission | None = Depends(get_instance_access_optional),
):
    """Bind artifact reads to verified HTTP admission, retaining recovery proof."""
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    if not isinstance(admission, OperatorAdmission):
        raise HTTPException(status_code=401, detail={"code": "auth_required"})
    return ArtifactAccessContext.for_operator(
        admission,
        recovery=get_operator_recovery_principal(request) == "operator",
    )


async def get_auth_manager(
    db: AsyncSession = Depends(get_async_session),
) -> AuthProviderManager:
    return AuthProviderManager(ProfileAuthProvider(db), EnvAuthProvider())
