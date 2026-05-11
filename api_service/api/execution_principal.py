"""Resolve ``ExecutionPrincipal`` for ``POST /api/executions`` callers.

Task-spawning callers identify themselves with trusted transport headers
(``X-MoonMind-Task-Workflow-Id`` / ``X-MoonMind-Task-Run-Id`` /
``X-MoonMind-Task-Run-Identifier``).  We refuse to honour those headers
unless the executions service can confirm that the OIDC user owns the
referenced workflow.  When that check passes the principal is granted the
``executions:create-child`` and ``executions:inherit-runtime`` scopes that
the runtime inheritance contract requires.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Header, Request

from moonmind.workflows.tasks.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
    ExecutionPrincipal,
)

logger = logging.getLogger(__name__)

TASK_WORKFLOW_HEADER = "X-MoonMind-Task-Workflow-Id"
TASK_RUN_HEADER = "X-MoonMind-Task-Run-Id"
TASK_RUN_IDENTIFIER_HEADER = "X-MoonMind-Task-Run-Identifier"


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_superuser(user: Any) -> bool:
    return bool(getattr(user, "is_superuser", False))


def _owner_id(user: Any) -> Optional[str]:
    raw = getattr(user, "id", None)
    return _coerce_str(raw)


async def _verify_task_workflow_ownership(
    *,
    service: Any,
    workflow_id: str,
    user: Any,
) -> bool:
    """Return True when ``user`` is allowed to act as the workflow principal."""

    if _is_superuser(user):
        return True
    try:
        record = await service.describe_execution(workflow_id)
    except Exception:
        logger.debug(
            "Failed to verify task workflow ownership for %s",
            workflow_id,
            exc_info=True,
        )
        return False

    record_owner_id = _coerce_str(getattr(record, "owner_id", None))
    user_id = _owner_id(user)
    if not record_owner_id or not user_id:
        return False
    return record_owner_id == user_id


async def resolve_execution_principal(
    *,
    user: Any,
    service: Any,
    request: Optional[Request] = None,
    workflow_id_header: Optional[str] = None,
    run_id_header: Optional[str] = None,
    task_run_id_header: Optional[str] = None,
) -> ExecutionPrincipal:
    """Build an ``ExecutionPrincipal`` for a request.

    ``request`` is used to read the task identity headers when explicit
    values are not provided (the executions router can supply the FastAPI
    ``Request`` directly).
    """

    workflow_id = _coerce_str(workflow_id_header)
    run_id = _coerce_str(run_id_header)
    task_run_id = _coerce_str(task_run_id_header)

    if request is not None:
        headers = request.headers
        if workflow_id is None:
            workflow_id = _coerce_str(headers.get(TASK_WORKFLOW_HEADER))
        if run_id is None:
            run_id = _coerce_str(headers.get(TASK_RUN_HEADER))
        if task_run_id is None:
            task_run_id = _coerce_str(headers.get(TASK_RUN_IDENTIFIER_HEADER))

    scopes: set[str] = set()
    verified_workflow_id: Optional[str] = None
    verified_run_id: Optional[str] = None
    verified_task_run_id: Optional[str] = None

    if workflow_id:
        ownership_ok = await _verify_task_workflow_ownership(
            service=service,
            workflow_id=workflow_id,
            user=user,
        )
        if ownership_ok:
            verified_workflow_id = workflow_id
            verified_run_id = run_id
            verified_task_run_id = task_run_id
            scopes.update({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME})

    return ExecutionPrincipal(
        user_id=_owner_id(user),
        is_superuser=_is_superuser(user),
        workflow_id=verified_workflow_id,
        run_id=verified_run_id,
        task_run_id=verified_task_run_id,
        scopes=frozenset(scopes),
    )


async def execution_principal_dependency(
    request: Request,
    x_moonmind_task_workflow_id: Optional[str] = Header(
        default=None, alias=TASK_WORKFLOW_HEADER
    ),
    x_moonmind_task_run_id: Optional[str] = Header(
        default=None, alias=TASK_RUN_HEADER
    ),
    x_moonmind_task_run_identifier: Optional[str] = Header(
        default=None, alias=TASK_RUN_IDENTIFIER_HEADER
    ),
) -> dict[str, Any]:
    """Return a *deferred* principal context for router endpoints.

    The actual ``ExecutionPrincipal`` is resolved lazily by the router so
    we can share the same ``TemporalExecutionService`` instance the
    endpoint depends on (avoiding a second service allocation).  This
    dependency only collects header values that need verification later.
    """

    return {
        "request": request,
        "workflow_id_header": x_moonmind_task_workflow_id,
        "run_id_header": x_moonmind_task_run_id,
        "task_run_id_header": x_moonmind_task_run_identifier,
    }


__all__ = [
    "TASK_RUN_HEADER",
    "TASK_RUN_IDENTIFIER_HEADER",
    "TASK_WORKFLOW_HEADER",
    "execution_principal_dependency",
    "resolve_execution_principal",
]
