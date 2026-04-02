"""Temporal activities for OpenClaw streaming execution."""

from __future__ import annotations

from temporalio import activity

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)


@activity.defn(name="integration.openclaw.execute")
async def openclaw_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    """Run one OpenClaw streaming execution; heartbeats carry stream progress.

    Delegates to the OpenClaw executor, which reads configuration, yields content,
    and returns canonical AgentRunResult.
    """
    from moonmind.openclaw.execute import run_openclaw_execution

    return await run_openclaw_execution(request)


__all__ = [
    "openclaw_execute_activity",
]
