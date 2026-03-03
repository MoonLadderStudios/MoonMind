"""Unit tests for Jules MCP tool registry dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.mcp.jules_tool_registry import (
    JulesToolExecutionContext,
    JulesToolRegistry,
)
from moonmind.mcp.tool_registry import ToolArgumentsValidationError, ToolNotFoundError
from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
    JulesTaskResponse,
)
from moonmind.workflows.adapters.jules_client import JulesClientError

pytestmark = [pytest.mark.asyncio]


class _FakeJulesClient:
    """Fake Jules client with canned responses and call tracking."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def create_task(self, request: JulesCreateTaskRequest) -> JulesTaskResponse:
        self.calls.append(("create_task", request))
        return JulesTaskResponse(task_id="task-001", status="pending", url=None)

    async def resolve_task(self, request: JulesResolveTaskRequest) -> JulesTaskResponse:
        self.calls.append(("resolve_task", request))
        return JulesTaskResponse(task_id=request.task_id, status="completed", url=None)

    async def get_task(self, request: JulesGetTaskRequest) -> JulesTaskResponse:
        self.calls.append(("get_task", request))
        return JulesTaskResponse(task_id=request.task_id, status="pending", url=None)


class _FailingJulesClient(_FakeJulesClient):
    """Fake client that raises JulesClientError on every call."""

    async def create_task(self, request: JulesCreateTaskRequest) -> JulesTaskResponse:
        raise JulesClientError("connection refused")

    async def resolve_task(self, request: JulesResolveTaskRequest) -> JulesTaskResponse:
        raise JulesClientError("connection refused")

    async def get_task(self, request: JulesGetTaskRequest) -> JulesTaskResponse:
        raise JulesClientError("connection refused")


def _build_context(
    client: _FakeJulesClient | None = None,
) -> JulesToolExecutionContext:
    return JulesToolExecutionContext(client=client or _FakeJulesClient())  # type: ignore[arg-type]


# --- discovery tests ---


def test_list_tools_returns_three_jules_tools():
    registry = JulesToolRegistry()
    tools = registry.list_tools()
    names = [t.name for t in tools]
    assert sorted(names) == [
        "jules.create_task",
        "jules.get_task",
        "jules.resolve_task",
    ]


# --- dispatch tests ---


@pytest.mark.asyncio
async def test_call_create_task():
    fake = _FakeJulesClient()
    context = _build_context(fake)
    registry = JulesToolRegistry()

    result = await registry.call_tool(
        tool="jules.create_task",
        arguments={"title": "Fix bug", "description": "Resolve issue #42"},
        context=context,
    )
    assert result["taskId"] == "task-001"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "create_task"


@pytest.mark.asyncio
async def test_call_resolve_task():
    fake = _FakeJulesClient()
    context = _build_context(fake)
    registry = JulesToolRegistry()

    result = await registry.call_tool(
        tool="jules.resolve_task",
        arguments={
            "taskId": "task-001",
            "resolutionNotes": "Fixed",
            "status": "completed",
        },
        context=context,
    )
    assert result["status"] == "completed"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "resolve_task"


@pytest.mark.asyncio
async def test_call_get_task():
    fake = _FakeJulesClient()
    context = _build_context(fake)
    registry = JulesToolRegistry()

    result = await registry.call_tool(
        tool="jules.get_task",
        arguments={"taskId": "task-001"},
        context=context,
    )
    assert result["taskId"] == "task-001"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "get_task"


# --- error tests ---


@pytest.mark.asyncio
async def test_call_unknown_tool_raises():
    registry = JulesToolRegistry()
    context = _build_context()
    with pytest.raises(ToolNotFoundError):
        await registry.call_tool(
            tool="jules.nonexistent",
            arguments={},
            context=context,
        )


@pytest.mark.asyncio
async def test_call_with_bad_args_raises():
    registry = JulesToolRegistry()
    context = _build_context()
    with pytest.raises(ToolArgumentsValidationError):
        await registry.call_tool(
            tool="jules.create_task",
            arguments={},  # missing required fields
            context=context,
        )


@pytest.mark.asyncio
async def test_client_error_propagates_from_handler():
    failing = _FailingJulesClient()
    context = _build_context(failing)
    registry = JulesToolRegistry()

    with pytest.raises(JulesClientError):
        await registry.call_tool(
            tool="jules.create_task",
            arguments={"title": "Fail", "description": "Will error"},
            context=context,
        )
