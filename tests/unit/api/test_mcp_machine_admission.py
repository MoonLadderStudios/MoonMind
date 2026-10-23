"""General MCP surface: operator admission or a scoped machine capability.

Uses the production caller dependency (no auth override) with synthetic
credentials so the real validators decide every case.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api_service.auth_providers as providers
from api_service.api.routers import mcp_tools
from moonmind.mcp.jira_tool_registry import JiraToolRegistry
from moonmind.mcp.skills_on_demand_registry import SkillsOnDemandToolRegistry
from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
)
from moonmind.security.mcp_tool_capabilities import mint_mcp_tool_capability
from moonmind.statuses.workflow import MoonMindWorkflowState

pytestmark = pytest.mark.asyncio

_SECRET = "test-mcp-tool-capability-secret"
_WORKFLOW_ID = "mm:workflow-1"
# A managed runtime on the Compose network: neither loopback nor publication.
_MACHINE_PEER = ("172.18.0.7", 40000)
_MACHINE_BASE = "http://api:5000"


class _FakeJiraService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def get_issue(self, request: Any) -> dict[str, Any]:
        self.calls.append(request)
        return {"issueKey": request.issue_key}


def _capability(**overrides: Any) -> str:
    claims: dict[str, Any] = {
        "secret": _SECRET,
        "purpose": "jira",
        "workflow_id": _WORKFLOW_ID,
        "agent_run_id": "step-exec-1",
        "step_id": "step-exec-1",
        "session_id": "host-lease-1",
        "runtime_id": "codex_cli",
        "source_kind": "omnigent",
        "lifetime_seconds": 300,
    }
    claims.update(overrides)
    return mint_mcp_tool_capability(**claims)


def _machine_headers(token: str) -> dict[str, str]:
    return {"X-MoonMind-Mcp-Tools": "v1", "Authorization": f"Bearer {token}"}


@pytest.fixture
def mcp_app(monkeypatch: pytest.MonkeyPatch, provider_env_keys):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    for key in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(mcp_tools.settings.security, "JWT_SECRET_KEY", _SECRET)
    jira = _FakeJiraService()
    monkeypatch.setattr(mcp_tools, "_jira_service", jira)
    monkeypatch.setattr(
        mcp_tools,
        "_jira_registry",
        JiraToolRegistry(enabled_actions=frozenset({"get_issue"})),
    )
    monkeypatch.setattr(
        mcp_tools,
        "_skills_on_demand_registry",
        SkillsOnDemandToolRegistry(expose_commands=False),
    )
    from api_service.services import single_user_conversion

    monkeypatch.setattr(
        single_user_conversion,
        "completed_guarded_upgrade",
        AsyncMock(return_value=object()),
    )
    record = SimpleNamespace(state=MoonMindWorkflowState.EXECUTING)
    session = SimpleNamespace(get=AsyncMock(return_value=record), record=record)
    app = FastAPI()
    app.include_router(mcp_tools.router, prefix="/api")
    app.dependency_overrides[mcp_tools.get_async_session] = lambda: session
    return SimpleNamespace(app=app, jira=jira, session=session)


def _client(app: FastAPI, peer=_MACHINE_PEER, base=_MACHINE_BASE) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=peer), base_url=base)


@pytest.mark.parametrize("mode", ["disabled", "oidc"])
async def test_managed_jira_caller_succeeds_without_a_person(
    mcp_app, monkeypatch, mode
):
    monkeypatch.setenv("AUTH_PROVIDER", str(mode or ""))
    operator = AsyncMock(side_effect=AssertionError("no operator resolution"))
    monkeypatch.setattr(mcp_tools, "get_instance_access", operator)
    async with _client(mcp_app.app) as client:
        listed = await client.get(
            "/api/mcp/tools", headers=_machine_headers(_capability())
        )
        called = await client.post(
            "/api/mcp/tools/call",
            headers=_machine_headers(_capability()),
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["tools"]}
    assert names and all(name.startswith("jira.") for name in names)
    assert called.status_code == 200, called.text
    assert called.json()["result"] == {"issueKey": "ENG-1"}
    mcp_app.session.get.assert_awaited()
    assert mcp_app.session.get.await_args.args[1] == _WORKFLOW_ID
    operator.assert_not_awaited()


async def test_streamable_transport_scopes_machine_caller(mcp_app):
    headers = {**_machine_headers(_capability()), "Accept": "application/json"}
    async with _client(mcp_app.app) as client:
        listed = await client.post(
            "/api/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        denied = await client.post(
            "/api/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "moonmind.skills.list", "arguments": {}},
            },
        )
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        "jira.get_issue"
    }
    result = denied.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "mcp_tool_capability_scope_mismatch"


@pytest.mark.parametrize(
    "tool", ["container.submit", "moonmind.skills.list", "remediation.get_context"]
)
async def test_wrong_purpose_tool_is_rejected(mcp_app, tool):
    async with _client(mcp_app.app) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            headers=_machine_headers(_capability()),
            json={"tool": tool, "arguments": {}},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "mcp_tool_capability_scope_mismatch"
    assert mcp_app.jira.calls == []


def _container_capability() -> str:
    return mint_container_job_session_capability(
        secret=_SECRET,
        owner=OwnerIdentity(principalId="run-1", principalType="service"),
        agent_run_id="run-1",
        workflow_id=_WORKFLOW_ID,
        session_id="s",
        runtime_id="codex_cli",
        lifetime_seconds=300,
    )


def _fanout_capability() -> str:
    return mint_execution_fanout_capability(
        secret=_SECRET,
        parent_workflow_id=_WORKFLOW_ID,
        agent_run_id="run-1",
        session_id="s",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=300,
    )


@pytest.mark.parametrize(
    ("token", "code"),
    [
        (
            lambda: _capability(now=int(time.time()) - 600),
            "invalid_mcp_tool_capability",
        ),
        (lambda: _capability(secret="other-secret"), "invalid_mcp_tool_capability"),
        (_container_capability, "invalid_mcp_tool_capability"),
        (_fanout_capability, "invalid_mcp_tool_capability"),
        (lambda: "garbage", "invalid_mcp_tool_capability"),
    ],
    ids=[
        "expired",
        "wrong-secret",
        "container-capability",
        "fanout-capability",
        "garbage",
    ],
)
async def test_invalid_capability_is_rejected_without_fallback(
    mcp_app, monkeypatch, token, code
):
    operator = AsyncMock(side_effect=AssertionError("no operator fallback"))
    monkeypatch.setattr(mcp_tools, "get_instance_access", operator)
    # A loopback peer could otherwise be an admitted operator.
    async with _client(
        mcp_app.app, peer=("127.0.0.1", 1), base="http://localhost:5000"
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            headers=_machine_headers(token()),
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == code
    operator.assert_not_awaited()
    assert mcp_app.jira.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"X-MoonMind-Mcp-Tools": "v1"},
        {"X-MoonMind-Mcp-Tools": "v2", "Authorization": "Bearer x"},
    ],
    ids=["missing-bearer", "unsupported-version"],
)
async def test_malformed_machine_headers_are_rejected(mcp_app, headers):
    async with _client(
        mcp_app.app, peer=("127.0.0.1", 1), base="http://localhost:5000"
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            headers=headers,
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert response.status_code == 401
    assert mcp_app.jira.calls == []


@pytest.mark.parametrize("record", [None, "completed", "canceled", "failed"])
async def test_stale_or_revoked_workflow_binding_is_rejected(mcp_app, record):
    mcp_app.session.get.return_value = (
        None if record is None else SimpleNamespace(state=MoonMindWorkflowState(record))
    )
    async with _client(mcp_app.app) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            headers=_machine_headers(_capability()),
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "stale_mcp_tool_capability"
    assert mcp_app.jira.calls == []


async def test_machine_peer_without_capability_is_not_an_operator(mcp_app):
    async with _client(mcp_app.app) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert response.status_code == 401
    assert mcp_app.jira.calls == []


@pytest.mark.parametrize(
    "credential",
    [
        {"Authorization": "Bearer garbage"},
        {"X-MoonMind-Execution-Fanout": "v1", "Authorization": "Bearer x"},
        {"X-MoonMind-Worker-Token": "x"},
    ],
)
async def test_presented_runtime_credential_cannot_use_operator_path(
    mcp_app, credential
):
    async with _client(
        mcp_app.app, peer=("127.0.0.1", 1), base="http://localhost:5000"
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            headers=credential,
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
    assert response.status_code == 401
    assert mcp_app.jira.calls == []


async def test_local_operator_dispatches_without_a_synthetic_user(mcp_app, monkeypatch):
    captured: dict[str, Any] = {}

    async def _dispatch(payload, owner, session, *, capability=None):
        captured["owner"] = owner
        return {"jobId": "container-job:" + "1" * 32}

    monkeypatch.setattr(mcp_tools, "_dispatch_container_job_tool", _dispatch)
    monkeypatch.setattr(
        mcp_tools._container_job_registry, "has_tool", lambda name: True
    )
    async with _client(
        mcp_app.app, peer=("127.0.0.1", 1), base="http://localhost:5000"
    ) as client:
        jira = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )
        container = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "container.status", "arguments": {}},
        )
    assert jira.status_code == 200
    assert container.status_code == 200
    assert captured["owner"] == OwnerIdentity(
        principalId=providers.instance_operator_principal(), principalType="user"
    )
