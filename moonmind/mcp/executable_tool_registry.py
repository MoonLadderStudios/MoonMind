"""Discovery metadata for Temporal executable tools shown in Mission Control."""

from __future__ import annotations

from typing import Any

from moonmind.integrations.pentest.models import PENTEST_TOOL_NAME, PENTEST_TOOL_VERSION
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
            "description": "Approved target URL, host, CIDR, FQDN, or application.",
        },
        "scope_artifact_ref": {
            "type": "string",
            "description": "ArtifactRef for the approved pentest scope document.",
        },
        "objective": {
            "type": "string",
            "description": "Bounded pentest objective for the approved target.",
        },
        "operation_mode": {
            "type": "string",
            "enum": ["recon_only", "validate_hypothesis", "full_authorized"],
        },
        "runner_profile_id": {
            "type": "string",
            "enum": ["pentestgpt-safe", "pentestgpt-vpn-lab"],
            "default": "pentestgpt-safe",
        },
        "execution_profile_ref": {
            "type": "string",
            "description": "Exact PentestGPT Provider Profile to use.",
        },
        "provider_selector": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "provider_id": {"type": "string"},
                "tags_any": {"type": "array", "items": {"type": "string"}},
                "tags_all": {"type": "array", "items": {"type": "string"}},
            },
        },
        "time_budget_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 480,
            "default": 60,
        },
        "repo_dir": {"type": "string"},
        "evidence_level": {
            "type": "string",
            "enum": ["minimal", "standard", "full"],
            "default": "standard",
        },
        "network_attachment_ref": {
            "type": "string",
            "description": (
                "Optional approved network attachment ref required by VPN/lab "
                "runner profiles."
            ),
        },
    },
    "additionalProperties": False,
}


class ExecutableToolDiscoveryRegistry:
    """Read-only catalog for task-submission executable tools.

    These tools are selected in Mission Control and executed by the Temporal task
    submit path. They are intentionally not immediate `/mcp/tools/call` tools.
    """

    def __init__(self) -> None:
        self._tools = {
            PENTEST_TOOL_NAME: ToolMetadata(
                name=PENTEST_TOOL_NAME,
                description=(
                    "Run an authorized PentestGPT workload against an approved "
                    "target scope and publish normalized findings plus evidence "
                    "artifacts."
                ),
                input_schema={
                    **_PENTEST_RUN_INPUT_SCHEMA,
                    "x-moonmind-tool-version": PENTEST_TOOL_VERSION,
                    "x-moonmind-invocation": "temporal_task_submission",
                },
            )
        }

    def list_tools(self) -> list[ToolMetadata]:
        """Return discoverable Temporal executable tools."""

        return [self._tools[name] for name in sorted(self._tools)]

    def has_tool(self, name: str) -> bool:
        """Return whether this registry owns a discoverable executable tool."""

        return name in self._tools


__all__ = ["ExecutableToolDiscoveryRegistry"]
