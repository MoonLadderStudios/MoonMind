"""Omnigent external agent capability registration.

Execution uses ``integration.omnigent.execute`` (streaming). This adapter is
registered for runtime gate validation and ``ProviderCapabilityDescriptor``.
"""

from __future__ import annotations

from typing import Any

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    ProviderCapabilityDescriptor,
)
from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)

_OMNIGENT_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="omnigent",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=False,
    defaultPollHintSeconds=15,
    executionStyle="streaming_gateway",
)


class OmnigentExternalAdapter(BaseExternalAgentAdapter):
    """Registry entry for Omnigent; poll-based hooks are not used in v1."""

    def __init__(self) -> None:
        super().__init__(accepted_agent_ids=frozenset({"omnigent"}))

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _OMNIGENT_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        raise RuntimeError(
            "Omnigent executes via integration.omnigent.execute only; "
            "start activity is not registered for this provider."
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError("Omnigent uses streaming execution; status polling is unused.")

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        raise RuntimeError("Omnigent uses streaming execution; fetch_result is unused.")

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError("Omnigent cancels via activity cancellation on execute.")


__all__ = [
    "OmnigentExternalAdapter",
]
