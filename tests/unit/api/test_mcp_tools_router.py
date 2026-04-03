"""Router tests for Jira MCP discovery and dispatch."""

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
from moonmind.mcp.tool_registry import QueueToolRegistry

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()


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
    monkeypatch.setattr(mcp_tools_router, "_jules_registry", None)
    monkeypatch.setattr(mcp_tools_router, "_jules_client", None)
    return app


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
    names = sorted(tool["name"] for tool in response.json()["tools"])
    assert names == ["jira.get_issue", "jira.verify_connection"]


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
