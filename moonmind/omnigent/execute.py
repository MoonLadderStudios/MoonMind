"""Run one Omnigent streaming execution inside a Temporal activity."""

from __future__ import annotations

from moonmind.omnigent.settings import OMNIGENT_DISABLED_MESSAGE, build_omnigent_gate
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


async def run_omnigent_execution(request: AgentExecutionRequest) -> AgentRunResult:
    """Execute an Omnigent run via the streaming activity boundary."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    raise NotImplementedError(
        "integration.omnigent.execute is registered, but the Omnigent streaming "
        "transport is not implemented yet."
    )


__all__ = ["run_omnigent_execution"]
