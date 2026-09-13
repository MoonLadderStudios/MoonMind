"""Standalone worker authentication helpers.

MoonLadderStudios/MoonMind#4126 (parent #4116, K4 machine/runtime
authority): worker-only mutations resolve a scoped machine credential at
this owner. The retired ``X-MoonMind-Worker-Token`` path stays a ``410``
tombstone per ``docs/Security/AuthenticationContracts.md`` §8; the
supported machine path is the workflow-scoped execution-fanout capability
(``X-MoonMind-Execution-Fanout: v1`` + ``Authorization: Bearer``) verified
against the deployment signing key with exact parent/run/session/runtime
scope and expiry. An ordinary browser (OIDC/session) principal alone never
satisfies a worker-only mutation.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from api_service.auth_providers import get_current_user_optional
from api_service.db.models import User
from moonmind.security.auth_modes_4120 import is_disabled_local_mode

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class _WorkerRequestAuth:
    """Resolved worker auth context used by mutation endpoints."""

    auth_source: str
    worker_id: Optional[str]
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    token_id: Optional[UUID] = None
    # Exact machine scope carried by a verified execution-fanout capability.
    # Browser (oidc/disabled-local) contexts leave these empty; machine
    # contexts populate all of them so consumers stay bounded to the correct
    # workflow/run/session/host without trusting ambient claims.
    parent_workflow_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    session_id: Optional[str] = None
    runtime_id: Optional[str] = None
    expires_at: Optional[int] = None


def _resolve_worker_fanout_capability(
    *, marker: Optional[str], authorization: Optional[str]
):
    """Verify the scoped machine bearer, or return None for a non-machine call."""
    from api_service.api.execution_fanout import resolve_execution_fanout_capability

    return resolve_execution_fanout_capability(
        marker=marker, authorization=authorization
    )


async def _require_worker_auth(
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    fanout_marker: Optional[str] = Header(None, alias="X-MoonMind-Execution-Fanout"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> _WorkerRequestAuth:
    """Resolve worker auth from a scoped machine credential.

    Precedence (fail-closed, no substitution):

    1. A presented legacy worker token is always the ``410``
       ``worker_token_deprecated`` tombstone, even when other credentials
       are also present.
    2. A presented execution-fanout marker selects the machine path: the
       bearer is verified (signature, audience/version, exact scope,
       expiry) and returned with its bounded claims. Missing/invalid/
       expired/stale machine bearers are ``401``; there is no fallback to
       browser identity after a machine-credential failure.
    3. With no machine credential, ``disabled`` local mode resolves the
       persisted local principal (loopback/trusted-ingress only).
    4. Any other authenticated browser principal is insufficient for a
       worker-only mutation (``403 worker_authorization_required``);
       missing all authority is ``401 auth_required``.
    """

    if worker_token:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "worker_token_deprecated",
                "message": "Worker token authentication has been removed. Use OIDC.",
            },
        )

    if isinstance(fanout_marker, str) and fanout_marker.strip():
        capability = _resolve_worker_fanout_capability(
            marker=fanout_marker, authorization=authorization
        )
        if capability is None:
            # No marker content after normalization is handled inside the
            # resolver (unsupported marker -> 401); a None return here only
            # happens for an empty marker, which is not machine authority.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "auth_required",
                    "message": "Valid worker or OIDC credentials are required",
                },
            )
        logger.info(
            "auth_event reason=worker_fanout_accepted parent=%s run=%s",
            capability.parent_workflow_id,
            capability.agent_run_id,
        )
        return _WorkerRequestAuth(
            auth_source="execution_fanout",
            worker_id=None,
            allowed_repositories=(),
            allowed_job_types=(),
            capabilities=("execution.fanout",),
            parent_workflow_id=capability.parent_workflow_id,
            agent_run_id=capability.agent_run_id,
            session_id=capability.session_id,
            runtime_id=capability.runtime_id,
            expires_at=capability.expires_at,
        )

    if is_disabled_local_mode() and getattr(user, "id", None) is not None:
        return _WorkerRequestAuth(
            auth_source="disabled_local",
            worker_id=None,
            allowed_repositories=(),
            allowed_job_types=(),
            capabilities=(),
        )

    if getattr(user, "id", None) is not None:
        # Ordinary browser login is authenticated but not authorized for
        # worker-only mutations (inventory E9/K4). Callers needing machine
        # authority must present the scoped fanout bearer above.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_authorization_required",
                "message": (
                    "Worker-only operations require a workflow-scoped "
                    "machine credential."
                ),
            },
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "auth_required",
            "message": "Valid worker or OIDC credentials are required",
        },
    )

__all__ = ["_WorkerRequestAuth", "_require_worker_auth"]
