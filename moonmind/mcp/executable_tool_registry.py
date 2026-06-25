"""Discovery metadata for Temporal executable tools shown in Mission Control."""

from __future__ import annotations

from typing import Any

from moonmind.config.settings import settings
from moonmind.integrations.pentest.models import PENTEST_TOOL_NAME
from moonmind.mcp.tool_registry import ToolMetadata


_PENTEST_RUN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "target",
        "scope_artifact_ref",
        "operation_mode",
        "runner_profile_id",
    ],
    "properties": {
        "target": {
            "type": "string",
            "title": "Target",
            "description": "Approved target URL, host, CIDR, FQDN, or application.",
        },
        "scope_artifact_ref": {
            "type": "string",
            "title": "Approved scope artifact",
            "description": "ArtifactRef for the approved pentest scope document.",
        },
        "objective": {
            "type": "string",
            "title": "Objective",
            "description": "Bounded pentest objective for the approved target.",
        },
        "operation_mode": {
            "type": "string",
            "title": "Operation mode",
            "enum": ["recon_only", "validate_hypothesis", "full_authorized"],
        },
        "runner_profile_id": {
            "type": "string",
            "title": "Runner profile",
            "enum": ["pentestgpt-claude-oauth"],
            "default": "pentestgpt-claude-oauth",
        },
        "execution_profile_ref": {
            "type": "string",
            "title": "Execution profile",
            "description": "Exact PentestGPT Provider Profile to use.",
        },
        "provider_selector": {
            "type": "object",
            "title": "Provider selector",
            "additionalProperties": False,
            "properties": {
                "provider_id": {"type": "string"},
                "tags_any": {"type": "array", "items": {"type": "string"}},
                "tags_all": {"type": "array", "items": {"type": "string"}},
            },
        },
        "time_budget_minutes": {
            "type": "integer",
            "title": "Time budget minutes",
            "minimum": 1,
            "maximum": 480,
            "default": 60,
        },
        "repo_dir": {"type": "string", "title": "Repository directory"},
        "evidence_level": {
            "type": "string",
            "title": "Evidence level",
            "enum": ["minimal", "standard", "full"],
            "default": "standard",
        },
        "network_attachment_ref": {
            "type": "string",
            "title": "Network attachment",
            "description": (
                "Optional approved network attachment ref reserved for future "
                "elevated-network runner profiles."
            ),
        },
    },
    "additionalProperties": False,
}


def _pentest_run_input_schema() -> dict[str, Any]:
    pentest_settings = settings.pentest
    runner_profiles = list(pentest_settings.allowed_runner_profiles)
    claude_runner = pentest_settings.claude_oauth_runner_profile_id
    if pentest_settings.allow_claude_oauth_profile:
        if claude_runner and claude_runner not in runner_profiles:
            runner_profiles.append(claude_runner)
    else:
        runner_profiles = [
            profile_id for profile_id in runner_profiles if profile_id != claude_runner
        ]
    operation_modes = list(pentest_settings.allowed_operation_modes)
    evidence_levels = list(pentest_settings.allowed_evidence_levels)
    schema = dict(_PENTEST_RUN_INPUT_SCHEMA)
    properties = dict(schema["properties"])
    properties["operation_mode"] = {
        **properties["operation_mode"],
        "enum": operation_modes,
        "default": pentest_settings.default_operation_mode,
    }
    properties["runner_profile_id"] = {
        **properties["runner_profile_id"],
        "enum": runner_profiles,
        "default": pentest_settings.default_runner_profile,
    }
    properties["time_budget_minutes"] = {
        **properties["time_budget_minutes"],
        "maximum": pentest_settings.max_time_budget_minutes,
        "default": pentest_settings.default_time_budget_minutes,
    }
    properties["evidence_level"] = {
        **properties["evidence_level"],
        "enum": evidence_levels,
        "default": pentest_settings.default_evidence_level,
    }
    schema["properties"] = properties
    return schema


class ExecutableToolDiscoveryRegistry:
    """Read-only catalog for task-submission executable tools.

    These tools are selected in Mission Control and executed by the Temporal task
    submit path. They are intentionally not immediate `/mcp/tools/call` tools.
    """

    def __init__(self, *, pentest_enabled: bool | None = None) -> None:
        enabled = settings.pentest.enabled if pentest_enabled is None else pentest_enabled
        self._tools = {}
        if enabled:
            self._tools[PENTEST_TOOL_NAME] = ToolMetadata(
                name=PENTEST_TOOL_NAME,
                description=(
                    "Run an authorized PentestGPT workload against an approved "
                    "target scope and publish normalized findings plus evidence "
                    "artifacts."
                ),
                input_schema={
                    **_pentest_run_input_schema(),
                    "x-moonmind-invocation": "temporal_task_submission",
                },
            )

    def list_tools(self) -> list[ToolMetadata]:
        """Return discoverable Temporal executable tools."""

        return [self._tools[name] for name in sorted(self._tools)]

    def has_tool(self, name: str) -> bool:
        """Return whether this registry owns a discoverable executable tool."""

        return name in self._tools


__all__ = ["ExecutableToolDiscoveryRegistry"]
