from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api_service.api.execution_fanout import (
    enforce_fanout_child_visibility,
    resolve_execution_fanout_capability,
    resolve_execution_request_authority,
)
from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
)
from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
)


class _Service:
    def __init__(self, records: dict[str, Any]) -> None:
        self.records = records

    async def describe_execution(self, workflow_id: str, **_kwargs: Any) -> Any:
        if workflow_id not in self.records:
            raise LookupError(workflow_id)
        return self.records[workflow_id]


def _token() -> str:
    return mint_execution_fanout_capability(
        secret=str(settings.security.JWT_SECRET_KEY),
        parent_workflow_id="mm:parent",
        agent_run_id="agent-run-1",
        step_id="step-1",
        session_id="session-1",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=60,
    )


@pytest.mark.asyncio
async def test_fanout_authority_uses_parent_owner_and_scoped_principal() -> None:
    parent = SimpleNamespace(owner_id="user-1", owner_type="user")
    capability = resolve_execution_fanout_capability(
        marker="v1", authorization=f"Bearer {_token()}"
    )

    authority = await resolve_execution_request_authority(
        user=None,
        service=_Service({"mm:parent": parent}),
        capability=capability,
    )

    assert authority.user.id == "user-1"
    assert authority.user.is_superuser is False
    assert authority.principal is not None
    assert authority.principal.workflow_id == "mm:parent"
    assert authority.principal.agent_run_id == "agent-run-1"
    assert authority.principal.scopes == frozenset(
        {SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}
    )


@pytest.mark.asyncio
async def test_fanout_describe_hides_non_child_execution() -> None:
    parent = SimpleNamespace(owner_id="user-1", owner_type="user")
    unrelated = SimpleNamespace(
        owner_id="user-1",
        owner_type="user",
        parameters={"parentWorkflowId": "mm:someone-else"},
    )
    capability = resolve_execution_fanout_capability(
        marker="v1", authorization=f"Bearer {_token()}"
    )
    service = _Service({"mm:parent": parent, "mm:unrelated": unrelated})
    authority = await resolve_execution_request_authority(
        user=None,
        service=service,
        capability=capability,
    )

    with pytest.raises(HTTPException) as exc:
        await enforce_fanout_child_visibility(
            service=service,
            workflow_id="mm:unrelated",
            authority=authority,
        )

    assert exc.value.status_code == 404


def test_fanout_marker_requires_execution_scoped_bearer() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_execution_fanout_capability(
            marker="v1", authorization="Bearer not-a-capability"
        )

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "invalid_execution_fanout_capability"
