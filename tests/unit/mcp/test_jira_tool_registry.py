"""Unit tests for Jira MCP tool registry dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.mcp.jira_tool_registry import JiraToolExecutionContext, JiraToolRegistry
from moonmind.mcp.tool_registry import ToolArgumentsValidationError, ToolNotFoundError

pytestmark = [pytest.mark.asyncio]


class _FakeJiraService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def get_issue(self, request: Any) -> dict[str, Any]:
        self.calls.append(("get_issue", request))
        return {"issueKey": request.issue_key}

    async def add_comment(self, request: Any) -> dict[str, Any]:
        self.calls.append(("add_comment", request))
        return {"commented": True, "issueKey": request.issue_key}

    async def transition_issue(self, request: Any) -> dict[str, Any]:
        self.calls.append(("transition_issue", request))
        return {"transitioned": True, "issueKey": request.issue_key}

    async def list_create_issue_types(self, request: Any) -> dict[str, Any]:
        self.calls.append(("list_create_issue_types", request))
        return {"projectKey": request.project_key, "issueTypes": []}


def _build_context(service: _FakeJiraService | None = None) -> JiraToolExecutionContext:
    return JiraToolExecutionContext(service=service or _FakeJiraService())  # type: ignore[arg-type]


async def test_list_tools_filters_by_enabled_actions() -> None:
    registry = JiraToolRegistry(enabled_actions={"get_issue", "transition_issue"})

    names = [tool.name for tool in registry.list_tools()]

    assert names == ["jira.get_issue", "jira.transition_issue"]


@pytest.mark.parametrize(
    ("tool", "arguments", "expected_call"),
    [
        ("jira.add_comment", {"issueKey": "ENG-1", "body": "ready"}, "add_comment"),
        (
            "jira.list_create_issue_types",
            {"projectKey": "ENG"},
            "list_create_issue_types",
        ),
        (
            "jira.transition_issue",
            {"issueKey": "ENG-1", "transitionId": "31"},
            "transition_issue",
        ),
    ],
)
async def test_call_tool_dispatches_expected_method(
    tool: str,
    arguments: dict[str, Any],
    expected_call: str,
) -> None:
    service = _FakeJiraService()
    registry = JiraToolRegistry(
        enabled_actions={"add_comment", "list_create_issue_types", "transition_issue"}
    )

    result = await registry.call_tool(
        tool=tool,
        arguments=arguments,
        context=_build_context(service),
    )

    assert result
    assert service.calls[0][0] == expected_call


async def test_call_tool_rejects_invalid_arguments() -> None:
    registry = JiraToolRegistry(enabled_actions={"get_issue"})

    with pytest.raises(ToolArgumentsValidationError):
        await registry.call_tool(
            tool="jira.get_issue",
            arguments={},
            context=_build_context(),
        )


async def test_call_unknown_tool_raises() -> None:
    registry = JiraToolRegistry(enabled_actions={"get_issue"})

    with pytest.raises(ToolNotFoundError):
        await registry.call_tool(
            tool="jira.create_issue",
            arguments={},
            context=_build_context(),
        )
