"""Temporal activities for Codex Cloud external agent integration.

Registered on the ``mm.activity.integrations`` task queue.
Follows the same 4-activity pattern as ``jules_activities.py``.
"""

from __future__ import annotations

from temporalio import activity

from moonmind.codex_cloud.settings import (
    build_codex_cloud_gate,
    CODEX_CLOUD_DISABLED_MESSAGE,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.adapters.codex_cloud_agent_adapter import (
    CodexCloudAgentAdapter,
)
from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient


def _build_adapter() -> CodexCloudAgentAdapter:
    """Build a gated CodexCloudAgentAdapter using env-based configuration.

    Raises ``RuntimeError`` if the Codex Cloud runtime gate is unsatisfied.
    """

    import os

    gate = build_codex_cloud_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{CODEX_CLOUD_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    cloud_url = os.environ.get("CODEX_CLOUD_API_URL", "").strip()
    cloud_key = os.environ.get("CODEX_CLOUD_API_KEY", "").strip()
    client = CodexCloudClient(base_url=cloud_url, api_key=cloud_key)
    return CodexCloudAgentAdapter(client_factory=lambda: client)


@activity.defn(name="integration.codex_cloud.start")
async def codex_cloud_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
    """Start a Codex Cloud-backed run via the canonical adapter contract."""

    adapter = _build_adapter()
    return await adapter.start(request)


@activity.defn(name="integration.codex_cloud.status")
async def codex_cloud_status_activity(run_id: str) -> AgentRunStatus:
    """Poll current status for one Codex Cloud task."""

    adapter = _build_adapter()
    return await adapter.status(run_id)


@activity.defn(name="integration.codex_cloud.fetch_result")
async def codex_cloud_fetch_result_activity(run_id: str) -> AgentRunResult:
    """Fetch terminal result for one completed Codex Cloud task."""

    adapter = _build_adapter()
    return await adapter.fetch_result(run_id)


@activity.defn(name="integration.codex_cloud.cancel")
async def codex_cloud_cancel_activity(run_id: str) -> AgentRunStatus:
    """Attempt best-effort cancellation for one Codex Cloud task."""

    adapter = _build_adapter()
    return await adapter.cancel(run_id)


__all__ = [
    "codex_cloud_cancel_activity",
    "codex_cloud_fetch_result_activity",
    "codex_cloud_start_activity",
    "codex_cloud_status_activity",
]
