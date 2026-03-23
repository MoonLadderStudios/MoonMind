"""Standalone worker authentication helpers.

Extracted from the deleted agent_queue router so downstream consumers
(e.g. manifests router) can continue to resolve worker identity without
depending on the legacy queue service.
"""

from __future__ import annotations

import dataclasses
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from api_service.auth_providers import get_current_user_optional
from api_service.db.models import User
from moonmind.config.settings import settings


@dataclasses.dataclass
class _WorkerRequestAuth:
    """Resolved worker auth context used by mutation endpoints."""

    auth_source: str
    worker_id: Optional[str]
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    token_id: Optional[UUID] = None


async def _require_worker_auth(
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> _WorkerRequestAuth:
    """Resolve worker auth from authenticated OIDC principal.

    The legacy worker-token path (backed by AgentQueueService) has been removed.
    Only OIDC authentication is supported going forward.
    """

    if worker_token:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "worker_token_deprecated",
                "message": "Worker token authentication has been removed. Use OIDC.",
            },
        )

    if (
        settings.oidc.AUTH_PROVIDER != "disabled"
        and getattr(user, "id", None) is not None
    ):
        return _WorkerRequestAuth(
            auth_source="oidc",
            worker_id=None,
            allowed_repositories=(),
            allowed_job_types=(),
            capabilities=(),
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "auth_required",
            "message": "Valid worker or OIDC credentials are required",
        },
    )


__all__ = ["_WorkerRequestAuth", "_require_worker_auth"]
