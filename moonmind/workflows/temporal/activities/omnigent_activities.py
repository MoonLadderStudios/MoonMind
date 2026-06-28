"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


@activity.defn(name="integration.omnigent.execute")
async def omnigent_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    """Run one Omnigent streaming execution."""

    from moonmind.omnigent.execute import run_omnigent_execution

    return await run_omnigent_execution(request)


__all__ = [
    "omnigent_execute_activity",
]
