"""Tests for Temporal executable-tool discovery metadata."""

from __future__ import annotations

from moonmind.config import settings
from moonmind.integrations.pentest.models import PENTEST_TOOL_NAME
from moonmind.mcp.executable_tool_registry import ExecutableToolDiscoveryRegistry


def test_executable_tool_registry_hides_pentest_when_disabled() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=False)

    assert registry.list_tools() == []
    assert registry.has_tool(PENTEST_TOOL_NAME) is False


def test_executable_tool_registry_hides_pentest_by_default() -> None:
    registry = ExecutableToolDiscoveryRegistry()

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


def test_pentest_discovery_hides_vpn_lab_profile_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings.pentest, "allow_vpn_lab_profile", False)
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema
    profiles = schema["properties"]["runner_profile_id"]["enum"]

    assert settings.pentest.vpn_lab_profile_id not in profiles
    assert "pentestgpt-safe" in profiles


def test_pentest_discovery_exposes_vpn_lab_profile_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings.pentest, "allow_vpn_lab_profile", True)
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema
    profiles = schema["properties"]["runner_profile_id"]["enum"]

    assert settings.pentest.vpn_lab_profile_id in profiles


def test_pentest_discovery_does_not_expose_raw_control_inputs() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema
    property_names = set(schema["properties"])

    forbidden = {
        "command",
        "shell",
        "docker_args",
        "image",
        "mounts",
        "host_mounts",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "PENTESTGPT_AUTH_MODE",
        "LANGFUSE_ENABLED",
        "terminal",
        "attach",
    }
    assert property_names.isdisjoint(forbidden)
    assert schema["additionalProperties"] is False


def test_pentest_discovery_hides_full_authorized_mode_by_default() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    modes = registry.list_tools()[0].input_schema["properties"]["operation_mode"][
        "enum"
    ]

    assert "full_authorized" not in modes


def test_pentest_discovery_exposes_full_authorized_when_allowlisted(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.pentest,
        "allowed_operation_modes",
        ("recon_only", "validate_hypothesis", "full_authorized"),
    )
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)

    assert "full_authorized" in registry.list_tools()[0].input_schema[
        "properties"
    ]["operation_mode"]["enum"]


def test_pentest_discovery_narrows_evidence_and_time_budget_from_settings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.pentest, "allowed_evidence_levels", ("minimal",))
    monkeypatch.setattr(settings.pentest, "default_evidence_level", "minimal")
    monkeypatch.setattr(settings.pentest, "max_time_budget_minutes", 45)
    monkeypatch.setattr(settings.pentest, "default_time_budget_minutes", 30)
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema

    assert schema["properties"]["evidence_level"]["enum"] == ["minimal"]
    assert schema["properties"]["time_budget_minutes"]["maximum"] == 45
    assert schema["properties"]["time_budget_minutes"]["default"] == 30
