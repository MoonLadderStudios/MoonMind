"""OpenClaw external agent: capability registration and request translation.

Execution uses ``integration.openclaw.execute`` (streaming). This adapter is
registered for runtime gate validation and ``ProviderCapabilityDescriptor``.
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

_OPENCLAW_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="openclaw",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=False,
    defaultPollHintSeconds=15,
    execution_style="streaming_gateway",
)

_SYSTEM_PROMPT = (
    "You are an autonomous OpenClaw agent executing a bounded task delegated "
    "by MoonMind. Follow instructions precisely."
)

def build_openclaw_chat_messages(request: AgentExecutionRequest) -> list[dict[str, Any]]:
    """Map an execution request to OpenAI-style chat messages."""

    params = dict(request.parameters or {})
    title = str(params.get("title") or "MoonMind Agent Task").strip()
    description = str(params.get("description") or "").strip()
    if not description and request.instruction_ref:
        description = request.instruction_ref
    if not description and request.input_refs:
        description = "Input artifact refs: " + ", ".join(request.input_refs)
    if not description:
        description = f"Delegated run {request.correlation_id}"

    workspace_blob = json.dumps(request.workspace_spec or {}, indent=2, default=str)
    user_parts = [
        f"Task title: {title}",
        f"Task instructions:\n{description}",
        f"Workspace spec (JSON):\n{workspace_blob}",
    ]
    if request.input_refs:
        user_parts.append("Input refs: " + ", ".join(request.input_refs))

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

def openclaw_success_result(*, full_text: str, request: AgentExecutionRequest) -> AgentRunResult:
    """Build a successful ``AgentRunResult`` from aggregated stream text."""

    summary = full_text.strip()
    if len(summary) > 4096:
        summary = summary[:4093] + "..."
    return AgentRunResult(
        outputRefs=[],
        summary=summary or "(empty OpenClaw response)",
        metadata={
            "normalizedStatus": "completed",
            "providerName": "openclaw",
            "correlationId": request.correlation_id,
        },
    )

class OpenClawExternalAdapter(BaseExternalAgentAdapter):
    """Registry entry for OpenClaw; poll-based hooks are not used."""

    def __init__(self) -> None:
        super().__init__(accepted_agent_ids=frozenset({"openclaw"}))

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _OPENCLAW_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        raise RuntimeError(
            "OpenClaw executes via integration.openclaw.execute only; "
            "start activity is not registered for this provider."
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError("OpenClaw uses streaming execution; status polling is unused.")

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        raise RuntimeError("OpenClaw uses streaming execution; fetch_result is unused.")

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError("OpenClaw cancels via activity cancellation on execute.")

__all__ = [
    "OpenClawExternalAdapter",
    "build_openclaw_chat_messages",
    "openclaw_success_result",
]
