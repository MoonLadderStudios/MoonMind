from __future__ import annotations

from dataclasses import dataclass

import pytest

from moonmind.mcp.remediation_tool_registry import (
    RemediationToolExecutionContext,
    RemediationToolRegistry,
)

pytestmark = pytest.mark.asyncio


@dataclass
class _Page:
    evidence_class: str
    status: str = "available"
    items: tuple[dict, ...] = ()
    next_cursor: int | None = None
    degraded_reason: str | None = None


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def read_execution_and_step_details(self, **kwargs):
        self.calls.append(kwargs)
        return _Page("execution_and_steps")


async def test_registry_exposes_and_dispatches_authenticated_bounded_read() -> None:
    registry = RemediationToolRegistry()
    names = {tool.name for tool in registry.list_tools()}
    assert {
        "remediation.read_execution_steps",
        "remediation.read_checkpoint_recovery",
        "remediation.read_bridge_events",
        "remediation.read_capture_resources",
        "remediation.read_target_logs",
        "remediation.follow_target_logs",
        "remediation.read_cleanup_janitor",
        "remediation.read_branch_publication",
        "remediation.read_policy_approvals",
        "remediation.read_target_artifact",
        "remediation.execute_action",
    } <= names

    service = _Service()
    result = await registry.call_tool(
        tool="remediation.read_execution_steps",
        arguments={
            "remediationWorkflowId": "remediation-1",
            "limit": 7,
            "includeContent": True,
            "maxContentBytes": 1024,
        },
        context=RemediationToolExecutionContext(
            service=service,  # type: ignore[arg-type]
            principal="user:owner",
        ),
    )

    assert result["evidence_class"] == "execution_and_steps"
    assert service.calls == [
        {
            "remediation_workflow_id": "remediation-1",
            "cursor": 0,
            "limit": 7,
            "include_content": True,
            "max_content_bytes": 1024,
            "principal": "user:owner",
        }
    ]
