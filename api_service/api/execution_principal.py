"""Resolve ``ExecutionPrincipal`` for ``POST /api/executions`` callers.

Task-spawning callers identify themselves with trusted transport headers
(``X-MoonMind-Task-Workflow-Id`` / ``X-MoonMind-Agent-Run-Id`` /
``X-MoonMind-Agent-Run-Identifier``).  We refuse to honour those headers
unless the caller is an admitted instance operator and the executions service
can load the referenced workflow. Machines use the separately verified fan-out
capability principal; task identity headers never grant machine authority.
When the operator's check passes the principal is granted the
``executions:create-child`` and ``executions:inherit-runtime`` scopes that
the runtime inheritance contract requires.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Header, Request

from api_service.api.instance_access import is_instance_operator
from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
    ExecutionPrincipal,
)

logger = logging.getLogger(__name__)

TASK_WORKFLOW_HEADER = "X-MoonMind-Task-Workflow-Id"
AGENT_RUN_HEADER = "X-MoonMind-Agent-Run-Id"
AGENT_RUN_IDENTIFIER_HEADER = "X-MoonMind-Agent-Run-Identifier"


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _verify_operator_workflow(
    *,
    service: Any,
    workflow_id: str,
    user: Any,
) -> bool:
    """Verify operator admission and existence before trusting task headers."""

    if not is_instance_operator(user):
        return False
    try:
        await service.describe_execution(workflow_id)
    except Exception:
        logger.debug(
            "Failed to verify operator task workflow %s",
            workflow_id,
            exc_info=True,
        )
        return False

    return True


async def resolve_execution_principal(
    *,
    user: Any,
    service: Any,
    request: Optional[Request] = None,
    workflow_id_header: Optional[str] = None,
    run_id_header: Optional[str] = None,
    agent_run_id_header: Optional[str] = None,
) -> ExecutionPrincipal:
    """Build an ``ExecutionPrincipal`` for a request.

    ``request`` is used to read the task identity headers when explicit
    values are not provided (the executions router can supply the FastAPI
    ``Request`` directly).
    """

    workflow_id = _coerce_str(workflow_id_header)
    run_id = _coerce_str(run_id_header)
    agent_run_id = _coerce_str(agent_run_id_header)

    if request is not None:
        headers = request.headers
        if workflow_id is None:
            workflow_id = _coerce_str(headers.get(TASK_WORKFLOW_HEADER))
        if run_id is None:
            run_id = _coerce_str(headers.get(AGENT_RUN_HEADER))
        if agent_run_id is None:
            agent_run_id = _coerce_str(headers.get(AGENT_RUN_IDENTIFIER_HEADER))

    scopes: set[str] = set()
    verified_workflow_id: Optional[str] = None
    verified_run_id: Optional[str] = None
    verified_agent_run_id: Optional[str] = None

    if workflow_id:
        workflow_verified = await _verify_operator_workflow(
            service=service,
            workflow_id=workflow_id,
            user=user,
        )
        if workflow_verified:
            verified_workflow_id = workflow_id
            verified_run_id = run_id
            verified_agent_run_id = agent_run_id
            scopes.update({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME})

    return ExecutionPrincipal(
        is_instance_operator=is_instance_operator(user),
        workflow_id=verified_workflow_id,
        run_id=verified_run_id,
        agent_run_id=verified_agent_run_id,
        scopes=frozenset(scopes),
    )


async def execution_principal_dependency(
    request: Request,
    x_moonmind_task_workflow_id: Optional[str] = Header(
        default=None, alias=TASK_WORKFLOW_HEADER
    ),
    x_moonmind_agent_run_id: Optional[str] = Header(
        default=None, alias=AGENT_RUN_HEADER
    ),
    x_moonmind_agent_run_identifier: Optional[str] = Header(
        default=None, alias=AGENT_RUN_IDENTIFIER_HEADER
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
        "run_id_header": x_moonmind_agent_run_id,
        "agent_run_id_header": x_moonmind_agent_run_identifier,
    }


__all__ = [
    "AGENT_RUN_HEADER",
    "AGENT_RUN_IDENTIFIER_HEADER",
    "TASK_WORKFLOW_HEADER",
    "execution_principal_dependency",
    "resolve_execution_principal",
]
