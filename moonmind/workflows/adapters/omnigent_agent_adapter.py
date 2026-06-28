"""Omnigent external agent capability registration for MM-991.

Execution uses ``integration.omnigent.execute`` as a terminal-only streaming
gateway activity. Polling lifecycle hooks are intentionally unavailable in v1.
"""

from __future__ import annotations

import json
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


def omnigent_parameters(request: AgentExecutionRequest) -> dict[str, Any]:
    params = request.parameters or {}
    value = params.get("omnigent")
    return dict(value) if isinstance(value, dict) else {}


def build_omnigent_first_message(request: AgentExecutionRequest) -> dict[str, Any]:
    params = request.parameters or {}
    omni = omnigent_parameters(request)
    prompt = omni.get("prompt")
    prompt = prompt if isinstance(prompt, dict) else {}

    text = str(prompt.get("text") or "").strip()
    if not text:
        text = str(prompt.get("instructionRef") or "").strip()
    if not text and request.instruction_ref:
        text = request.instruction_ref
    if not text:
        text = str(params.get("description") or "").strip()
    if not text:
        title = str(params.get("title") or "MoonMind Agent Task").strip()
        workspace_blob = json.dumps(request.workspace_spec or {}, indent=2, default=str)
        parts = [
            f"Task title: {title}",
            f"Correlation ID: {request.correlation_id}",
            f"Workspace spec (JSON):\n{workspace_blob}",
        ]
        if request.input_refs:
            parts.append("Input refs: " + ", ".join(request.input_refs))
        text = "\n\n".join(parts)

    return {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


class OmnigentExternalAdapter(BaseExternalAgentAdapter):
    """Registry entry for Omnigent; poll-based hooks are not used."""

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
    "build_omnigent_first_message",
    "omnigent_parameters",
]
