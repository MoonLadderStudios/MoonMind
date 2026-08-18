"""Authenticate workflow-scoped execution fan-out requests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, status

from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    ExecutionFanoutCapability,
    ExecutionFanoutCapabilityError,
    verify_execution_fanout_capability,
)
from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
    ExecutionPrincipal,
)

EXECUTION_FANOUT_HEADER = "X-MoonMind-Execution-Fanout"
EXECUTION_FANOUT_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ExecutionRequestAuthority:
    """Resolved user and optional workflow-scoped fan-out authority."""

    user: Any
    principal: ExecutionPrincipal | None = None
    capability: ExecutionFanoutCapability | None = None


def _bearer_token(authorization: str | None) -> str:
    scheme, separator, token = str(authorization or "").strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "execution_fanout_capability_required",
                "message": "A workflow-scoped execution fan-out bearer is required.",
            },
        )
    return token.strip()


def resolve_execution_fanout_capability(
    *,
    marker: str | None,
    authorization: str | None,
) -> ExecutionFanoutCapability | None:
    """Verify fan-out headers, or return ``None`` for an ordinary user call."""

    normalized_marker = str(marker or "").strip()
    if not normalized_marker:
        return None
    if normalized_marker != EXECUTION_FANOUT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "unsupported_execution_fanout_capability",
                "message": "The requested execution fan-out format is not recognized.",
            },
        )
    try:
        return verify_execution_fanout_capability(
            _bearer_token(authorization),
            secret=str(settings.security.JWT_SECRET_KEY or ""),
        )
    except ExecutionFanoutCapabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_execution_fanout_capability",
                "message": str(exc),
            },
        ) from exc


def _owner_type(record: Any) -> str:
    raw = getattr(record, "owner_type", None)
    value = getattr(raw, "value", raw)
    return str(value or "").strip().lower()


async def resolve_execution_request_authority(
    *,
    user: Any | None,
    service: Any,
    capability: ExecutionFanoutCapability | None,
) -> ExecutionRequestAuthority:
    """Bind a verified capability to its authoritative parent owner."""

    if capability is None:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "authentication_required",
                    "message": "Authentication is required.",
                },
            )
        return ExecutionRequestAuthority(user=user)

    try:
        parent = await service.describe_execution(capability.parent_workflow_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "execution_fanout_parent_unavailable",
                "message": "The capability parent execution is unavailable.",
            },
        ) from exc
    owner_id = getattr(parent, "owner_id", None)
    if _owner_type(parent) != "user" or not str(owner_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "execution_fanout_parent_unsupported",
                "message": "The capability parent has no user execution owner.",
            },
        )
    scoped_user = SimpleNamespace(
        id=owner_id,
        email=None,
        is_active=True,
        is_superuser=False,
        roles=[],
    )
    principal = ExecutionPrincipal(
        user_id=str(owner_id),
        is_superuser=False,
        workflow_id=capability.parent_workflow_id,
        agent_run_id=capability.agent_run_id,
        scopes=frozenset({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}),
    )
    return ExecutionRequestAuthority(
        user=scoped_user,
        principal=principal,
        capability=capability,
    )


async def enforce_fanout_child_visibility(
    *,
    service: Any,
    workflow_id: str,
    authority: ExecutionRequestAuthority,
) -> None:
    """Hide executions that are not children created by this capability parent."""

    capability = authority.capability
    if capability is None:
        return
    try:
        record = await service.describe_execution(workflow_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": f"Workflow execution {workflow_id} was not found",
            },
        ) from exc
    parameters = dict(getattr(record, "parameters", None) or {})
    parent_workflow_id = str(parameters.get("parentWorkflowId") or "").strip()
    record_owner_id = str(getattr(record, "owner_id", None) or "").strip()
    if parent_workflow_id != capability.parent_workflow_id or record_owner_id != str(
        authority.user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": f"Workflow execution {workflow_id} was not found",
            },
        )


__all__ = [
    "EXECUTION_FANOUT_HEADER",
    "EXECUTION_FANOUT_VERSION",
    "ExecutionRequestAuthority",
    "enforce_fanout_child_visibility",
    "resolve_execution_fanout_capability",
    "resolve_execution_request_authority",
]
