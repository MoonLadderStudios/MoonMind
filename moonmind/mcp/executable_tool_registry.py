"""Discovery metadata for Temporal executable tools shown in the dashboard."""

from __future__ import annotations

from moonmind.mcp.tool_registry import ToolMetadata


class ExecutableToolDiscoveryRegistry:
    """Read-only catalog for task-submission executable tools."""

    def __init__(self) -> None:
        from moonmind.workflows.skills.tool_definitions import (
            default_registry_tool_payload,
        )

        # Discovery supplies the same server-owned contract used by workflow
        # compilation. Direct MCP calls still require ordinary task submission.
        self._tools = {}
        for name in ("security.assess", "security.plan"):
            payload = default_registry_tool_payload(name=name)
            self._tools[name] = ToolMetadata(
                name=payload["name"],
                description=payload["description"],
                inputSchema=payload["inputs"]["schema"],
            )

    def list_tools(self) -> list[ToolMetadata]:
        """Return discoverable Temporal executable tools."""

        return [self._tools[name] for name in sorted(self._tools)]

    def has_tool(self, name: str) -> bool:
        """Return whether this registry owns a discoverable executable tool."""

        return name in self._tools


__all__ = ["ExecutableToolDiscoveryRegistry"]
