"""Scoped worker authentication (MoonLadderStudios/MoonMind#4126).

Preserves independent machine work across the application-auth cutover:
valid independently authenticated workers are not rejected by eager
browser authentication, while invalid or conflicting presented credentials
never escalate. Machine authority is the scoped worker capability owned by
``moonmind.security.scoped_machine_auth_4126`` (exact
workflow/run/session/host/resource scope plus lease/generation); the
legacy unstructured worker-token path stays rejected, now as ``401
auth_invalid`` through the same preservation owner rather than a bare
``410`` (pre-release removal in the same cohesive change per the
Compatibility Policy). Browser users keep the existing OIDC path.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from api_service.auth_providers import get_current_user_optional
from api_service.db.models import User
from moonmind.security.auth_modes_4120 import is_disabled_local_mode

@dataclasses.dataclass
class _WorkerRequestAuth:
    """Resolved worker auth context used by mutation endpoints."""

    auth_source: str
    worker_id: Optional[str]
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    token_id: Optional[UUID] = None
    owner_principal: Optional[str] = None
    workflow_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    host_id: Optional[str] = None
    resource: Optional[str] = None


def _worker_secret() -> str:
    """Resolve the dedicated worker-capability signing secret.

    Worker authority stays distinct from browser session keys, runtime
    credentials, and repository credentials: only this explicit secret (or
    the test-injected value) verifies worker capabilities.
    """
    return str(os.environ.get("MOONMIND_WORKER_TOKEN_SECRET") or "").strip()


def _new_revocation_store():
    from moonmind.security.scoped_machine_auth_4126 import (
        InMemoryWorkerRevocationStore,
    )

    return InMemoryWorkerRevocationStore()


async def _require_worker_auth(
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> _WorkerRequestAuth:
    """Resolve worker auth with #4121 missing-vs-invalid precedence (#4126).

    * Missing browser cookies are normal for machine calls: a valid scoped
      worker capability without a user resolves as machine authority.
    * Missing all accepted authority is ``401 auth_required``.
    * An invalid/expired/revoked/wrong-scope worker token is ``401
      auth_invalid`` even when a user is also present: invalid credentials
      never silently become a different principal, and no default user,
      ambient service token, or full user session is substituted.
    * A valid worker token naming a different owner than the presented user
      is ``401 auth_conflict``: machines cannot escalate to another user's
      authority.
    * The legacy unstructured token shape (pre-#4126) is rejected as
      ``401 auth_invalid``: old JWT acceptance is not left enabled and is
      not broadened to unlimited delegated authority.
    """

    from moonmind.security.scoped_machine_auth_4126 import (
        ScopedWorkerAuthError,
        http_status_for_worker_error,
        resolve_machine_or_user,
    )

    user_id = getattr(user, "id", None)
    user_id_text = str(user_id).strip() if user_id is not None else None
    if worker_token is not None and not str(worker_token).strip():
        worker_token = None

    if worker_token:
        secret = _worker_secret()
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "unavailable",
                    "message": "Worker authentication is not configured",
                },
            )
        try:
            resolution = await resolve_machine_or_user(
                worker_token=worker_token,
                user_id=user_id_text,
                secret=secret,
                revocation=_new_revocation_store(),
            )
        except ScopedWorkerAuthError as exc:
            http_status, code = http_status_for_worker_error(exc)
            raise HTTPException(
                status_code=http_status,
                detail={"code": code, "message": str(exc)},
            ) from exc
        capability = resolution.capability
        assert capability is not None
        parsed_token_id: Optional[UUID] = None
        raw_token_id = str(capability.token_id or "").strip()
        if raw_token_id:
            try:
                parsed_token_id = (
                    UUID(hex=raw_token_id)
                    if len(raw_token_id) == 32
                    else UUID(raw_token_id)
                )
            except ValueError:
                parsed_token_id = None
        return _WorkerRequestAuth(
            auth_source="worker_token",
            worker_id=capability.host_id or capability.session_id,
            allowed_repositories=(),
            allowed_job_types=(),
            capabilities=capability.operations,
            token_id=parsed_token_id,
            owner_principal=capability.owner_principal,
            workflow_id=capability.workflow_id,
            run_id=capability.run_id,
            session_id=capability.session_id,
            host_id=capability.host_id,
            resource=capability.resource,
        )

    if (
        not is_disabled_local_mode()
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
