"""Temporal activities for Jules external agent integration.

Registered on the ``mm.activity.integrations`` task queue to satisfy
spec 066 / FR-008.
"""

from __future__ import annotations

from temporalio import activity

from moonmind.jules.runtime import build_runtime_gate_state, JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.jules_client import JulesClient


def _build_adapter() -> JulesAgentAdapter:
    """Build a gated JulesAgentAdapter using env-based configuration.

    Raises ``RuntimeError`` if the Jules runtime gate is unsatisfied.
    """

    import os

    gate = build_runtime_gate_state()
    if not gate.enabled:
        raise RuntimeError(
            f"{JULES_RUNTIME_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    jules_url = os.environ.get("JULES_API_URL", "").strip() or "https://jules.googleapis.com/v1alpha"
    jules_key = os.environ.get("JULES_API_KEY", "").strip()
    client = JulesClient(base_url=jules_url, api_key=jules_key)
    return JulesAgentAdapter(client_factory=lambda: client)


@activity.defn(name="integration.jules.start")
async def jules_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
    """Start a Jules-backed run via the canonical adapter contract."""

    adapter = _build_adapter()
    return await adapter.start(request)


@activity.defn(name="integration.jules.status")
async def jules_status_activity(run_id: str) -> AgentRunStatus:
    """Poll current status for one Jules task."""

    adapter = _build_adapter()
    return await adapter.status(run_id)


@activity.defn(name="integration.jules.fetch_result")
async def jules_fetch_result_activity(run_id: str) -> AgentRunResult:
    """Fetch terminal result for one completed Jules task."""

    adapter = _build_adapter()
    return await adapter.fetch_result(run_id)


@activity.defn(name="integration.jules.cancel")
async def jules_cancel_activity(run_id: str) -> AgentRunStatus:
    """Attempt best-effort cancellation for one Jules task."""

    adapter = _build_adapter()
    return await adapter.cancel(run_id)


__all__ = [
    "jules_cancel_activity",
    "jules_fetch_result_activity",
    "jules_start_activity",
    "jules_status_activity",
]
