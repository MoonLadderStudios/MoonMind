"""Temporal activities for Omnigent streaming execution."""

from __future__ import annotations

from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


@activity.defn(name="integration.omnigent.execute")
async def omnigent_execute_activity(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    """Run one Omnigent streaming execution."""

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.execute import run_omnigent_execution

    return await run_omnigent_execution(
        request,
        artifact_gateway=LocalOmnigentArtifactGateway(),
        run_store=OmnigentBridgeSessionStore(async_session_maker),
    )


__all__ = [
    "omnigent_execute_activity",
]
