"""Tests for Temporal executable-tool discovery metadata."""

from __future__ import annotations

import pytest

from moonmind.config.settings import settings
from moonmind.integrations.pentest.models import (
    PENTEST_TOOL_NAME,
    PENTEST_VPN_LAB_PROFILE_ID,
)
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


def test_discovery_hides_vpn_lab_profile_by_default() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema
    runner_profiles = schema["properties"]["runner_profile_id"]["enum"]

    assert PENTEST_VPN_LAB_PROFILE_ID not in runner_profiles
    assert runner_profiles == ["pentestgpt-safe"]


def test_discovery_exposes_vpn_lab_profile_only_when_allowed(monkeypatch) -> None:
    monkeypatch.setattr(settings.pentest, "allow_vpn_lab_profile", True)
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    runner_profiles = registry.list_tools()[0].input_schema["properties"][
        "runner_profile_id"
    ]["enum"]

    assert PENTEST_VPN_LAB_PROFILE_ID in runner_profiles


def test_discovery_hides_full_authorized_mode_by_default() -> None:
    # Default allowed_operation_modes does not include full_authorized.
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    modes = registry.list_tools()[0].input_schema["properties"]["operation_mode"][
        "enum"
    ]

    assert "full_authorized" not in modes


def test_discovery_exposes_full_authorized_when_allowlisted(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.pentest,
        "allowed_operation_modes",
        ("recon_only", "validate_hypothesis", "full_authorized"),
    )
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    modes = registry.list_tools()[0].input_schema["properties"]["operation_mode"][
        "enum"
    ]

    assert "full_authorized" in modes


def test_discovery_narrows_evidence_levels_and_time_budget_from_settings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.pentest, "allowed_evidence_levels", ("minimal",))
    monkeypatch.setattr(settings.pentest, "max_time_budget_minutes", 90)
    monkeypatch.setattr(settings.pentest, "default_time_budget_minutes", 30)
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    props = registry.list_tools()[0].input_schema["properties"]

    assert props["evidence_level"]["enum"] == ["minimal"]
    assert props["time_budget_minutes"]["maximum"] == 90
    assert props["time_budget_minutes"]["default"] == 30


def test_discovery_input_schema_forbids_unsafe_fields() -> None:
    registry = ExecutableToolDiscoveryRegistry(pentest_enabled=True)
    schema = registry.list_tools()[0].input_schema

    # No raw shell/docker/image/host-mount/credential controls are exposed.
    assert schema["additionalProperties"] is False
    forbidden = {
        "command",
        "shell",
        "docker_args",
        "image",
        "host_mounts",
        "mounts",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "PENTESTGPT_AUTH_MODE",
        "LANGFUSE_ENABLED",
        "terminal",
    }
    assert forbidden.isdisjoint(schema["properties"].keys())
