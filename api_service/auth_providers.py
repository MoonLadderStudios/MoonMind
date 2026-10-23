"""Shared instance admission and provider credential composition."""

import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.operator_admission import require_operator
from moonmind.auth import AuthProviderManager, EnvAuthProvider, ProfileAuthProvider
from moonmind.security.operator_admission import OperatorAdmission

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


async def get_instance_access_optional(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> OperatorAdmission | None:
    """Defer marked fan-out to its owning capability verifier, granting nothing."""
    if request.headers.get("x-moonmind-execution-fanout", "").strip() == "v1":
        return None
    return await get_instance_access(request, session)


async def get_auth_manager(
    db: AsyncSession = Depends(get_async_session),
) -> AuthProviderManager:
    return AuthProviderManager(ProfileAuthProvider(db), EnvAuthProvider())
