"""Tests for Temporal executable-tool discovery metadata."""

from __future__ import annotations

from moonmind.integrations.pentest.models import PENTEST_TOOL_NAME
from moonmind.mcp.executable_tool_registry import ExecutableToolDiscoveryRegistry


def test_executable_tool_registry_hides_pentest_when_disabled() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=False)

    assert registry.list_tools() == []
    assert registry.has_tool(PENTEST_TOOL_NAME) is False


def test_executable_tool_registry_exposes_enabled_pentest_schema() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)

    tools = {tool.name: tool for tool in registry.list_tools()}
    pentest = tools[PENTEST_TOOL_NAME]
    schema = pentest.input_schema

    assert registry.has_tool(PENTEST_TOOL_NAME) is True
    assert schema["x-moonmind-invocation"] == "temporal_task_submission"
    assert schema["properties"]["runner_profile_id"]["default"] == "pentestgpt-safe"
    assert schema["properties"]["operation_mode"]["enum"] == [
        "recon_only",
        "validate_hypothesis",
    ]
