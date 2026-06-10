"""Router tests for MCP discovery and dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import mcp_tools as mcp_tools_router
from api_service.auth_providers import get_current_user
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.mcp.jira_tool_registry import JiraToolRegistry
from moonmind.mcp.skills_on_demand_registry import SkillsOnDemandToolRegistry
from moonmind.mcp.tool_registry import QueueToolRegistry, ResourceListResponse

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()


def _empty_async_session() -> SimpleNamespace:
    return SimpleNamespace()


class _FakeJiraService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.raise_on_get_issue: JiraToolError | None = None

    async def get_issue(self, request: Any) -> dict[str, Any]:
        self.calls.append(("get_issue", request))
        if self.raise_on_get_issue is not None:
            raise self.raise_on_get_issue
        return {"issueKey": request.issue_key, "summary": "Example"}

@pytest.fixture
def router_app(
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_tools_router.router, prefix="/api")
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=None)
    monkeypatch.setattr(mcp_tools_router, "_queue_registry", QueueToolRegistry())
    monkeypatch.setattr(
        mcp_tools_router,
        "_skills_on_demand_registry",
        SkillsOnDemandToolRegistry(expose_commands=False),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_registry", None)
    monkeypatch.setattr(mcp_tools_router, "_jira_service", None)
    monkeypatch.setattr(mcp_tools_router, "_jules_registry", None)
    monkeypatch.setattr(mcp_tools_router, "_jules_client", None)
    app.dependency_overrides[mcp_tools_router.get_async_session] = _empty_async_session
    return app


def _mcp_headers() -> dict[str, str]:
    return {"Accept": "application/json, text/event-stream"}


async def test_list_tools_includes_enabled_jira_tools(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue", "verify_connection"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", _FakeJiraService())

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/mcp/tools")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert {"jira.get_issue", "jira.verify_connection"}.issubset(names)


async def test_list_tools_includes_skills_on_demand_commands_when_enabled(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_tools_router.settings.workflow,
        "skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        mcp_tools_router,
        "_skills_on_demand_registry",
        SkillsOnDemandToolRegistry(expose_commands=True),
    )

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/mcp/tools")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert "moonmind.skills.query" in names
    assert "moonmind.skills.request" in names


async def test_call_skills_on_demand_query_denies_when_disabled(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_tools_router.settings.workflow,
        "skills_on_demand_enabled",
        False,
    )

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={
                "tool": "moonmind.skills.query",
                "arguments": {"query": "jira"},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "denied"
    assert result["code"] == "feature_disabled"
    assert result["results"] == []


async def test_streamable_http_initialize_returns_mcp_capabilities(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "unit-test", "version": "1.0"},
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert payload["result"]["protocolVersion"] == "2025-03-26"
    assert payload["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert payload["result"]["serverInfo"]["name"] == "moonmind"


async def test_streamable_http_accepts_standard_json_accept_header(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

    assert response.status_code == 200
    assert response.json()["result"] == {}


async def test_streamable_http_allows_cross_origin_requests(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={**_mcp_headers(), "Origin": "http://evil.example"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

    assert response.status_code == 200
    assert response.json()["result"] == {}


async def test_streamable_http_rejects_batched_initialize(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json=[
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                }
            ],
        )

    assert response.status_code == 400
    assert "must not be batched" in response.json()["error"]["message"]


async def test_streamable_http_tools_list_uses_callable_tools(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", _FakeJiraService())

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
        )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert "jira.get_issue" in names
    assert "security.pentest.run" not in names


async def test_streamable_http_tools_call_dispatches_to_trusted_tool(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeJiraService()
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", service)

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "jira.get_issue",
                    "arguments": {"issueKey": "MM-777"},
                },
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {
        "issueKey": "MM-777",
        "summary": "Example",
    }
    assert result["content"][0]["type"] == "text"
    assert service.calls[0][0] == "get_issue"


async def test_streamable_http_tools_call_maps_execution_failures_to_tool_result(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeJiraService()
    service.raise_on_get_issue = JiraToolError(
        "Access denied",
        code="jira_policy_denied",
        status_code=403,
        action="get_issue",
    )
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", service)

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "jira.get_issue",
                    "arguments": {"issueKey": "MM-777"},
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload
    result = payload["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "jira_policy_denied"
    assert "Access denied" in result["content"][0]["text"]


async def test_streamable_http_tools_call_preserves_string_result_text(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def dispatch_tool(payload: Any, user: Any, session: Any = None) -> str:
        del payload, user, session
        return "success"

    monkeypatch.setattr(mcp_tools_router, "_dispatch_tool_call", dispatch_tool)

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "jira.get_issue", "arguments": {}},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["content"][0]["text"] == "success"
    assert result["structuredContent"] == "success"


async def test_streamable_http_notifications_return_accepted(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": None,
                "method": "notifications/initialized",
            },
        )

    assert response.status_code == 202
    assert response.content == b""


async def test_streamable_http_get_reports_sse_not_available(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/mcp",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 405

async def test_list_tools_includes_curated_pentest_execution_tool(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/mcp/tools")

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["tools"]}
    pentest = tools["security.pentest.run"]
    assert "PentestGPT" in pentest["description"]
    assert pentest["inputSchema"]["required"] == [
        "target",
        "scope_artifact_ref",
        "operation_mode",
        "runner_profile_id",
    ]
    assert (
        pentest["inputSchema"]["x-moonmind-invocation"]
        == "temporal_task_submission"
    )

async def test_list_resources_returns_mcp_resource_catalog(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/mcp/resources")

    assert response.status_code == 200
    resources = {
        resource["name"]: resource for resource in response.json()["resources"]
    }
    assert resources["context-completion"] == {
        "uri": "moonmind://context",
        "name": "context-completion",
        "description": (
            "Chat-style context completion endpoint with optional RAG, available "
            "through POST /context."
        ),
        "mimeType": "application/json",
    }
    assert resources["tool-catalog"]["uri"] == "moonmind://mcp/tools"
    assert resources["tool-catalog"]["mimeType"] == "application/json"

async def test_resource_metadata_allows_optional_mcp_fields() -> None:
    response = ResourceListResponse(
        resources=[{"uri": "moonmind://minimal", "name": "minimal"}]
    )

    assert response.model_dump(by_alias=True) == {
        "resources": [
            {
                "uri": "moonmind://minimal",
                "name": "minimal",
                "description": None,
                "mimeType": None,
            }
        ]
    }

async def test_call_curated_execution_tool_requires_task_submission(
    router_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "security.pentest.run", "arguments": {}},
        )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["code"]
        == "execution_tool_requires_task_submission"
    )

async def test_call_tool_dispatches_to_jira_service(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeJiraService()
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", service)

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )

    assert response.status_code == 200
    assert response.json()["result"] == {"issueKey": "ENG-1", "summary": "Example"}
    assert service.calls[0][0] == "get_issue"

async def test_call_tool_maps_jira_errors_to_http_response(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeJiraService()
    service.raise_on_get_issue = JiraToolError(
        "Access denied",
        code="jira_policy_denied",
        status_code=403,
        action="get_issue",
    )
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", service)

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "jira.get_issue", "arguments": {"issueKey": "ENG-1"}},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "jira_policy_denied"

async def test_call_tool_rejects_invalid_jira_arguments(
    router_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_tools_router,
        "_jira_registry",
        JiraToolRegistry(enabled_actions={"get_issue"}),
    )
    monkeypatch.setattr(mcp_tools_router, "_jira_service", _FakeJiraService())

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/mcp/tools/call",
            json={"tool": "jira.get_issue", "arguments": {}},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_tool_arguments"
