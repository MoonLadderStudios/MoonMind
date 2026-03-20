"""External-agent adapter implementation backed by the Jules provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    ProviderCapabilityDescriptor,
)
from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
    JulesSendMessageRequest,
    JulesTaskResponse,
    SourceContext,
    normalize_jules_status,
)
from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

JulesClientFactory = Callable[[], JulesClient]

_JULES_TO_AGENT_RUN_STATUS: dict[str, str] = {
    "queued": "queued",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "canceled": "cancelled",
    "unknown": "awaiting_callback",
}


def _to_agent_status(raw_status: str | None) -> str:
    normalized = normalize_jules_status(raw_status)
    return _JULES_TO_AGENT_RUN_STATUS[normalized]


def _normalize_jules_task(response: JulesTaskResponse) -> str:
    """Determine normalized status, treating PR creation as success."""
    normalized = normalize_jules_status(response.status)
    if normalized == "running" and response.pull_request_url:
        return "succeeded"
    return normalized


_JULES_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="jules",
    supportsCallbacks=False,
    supportsCancel=True,
    supportsResultFetch=True,
    defaultPollHintSeconds=15,
)


class JulesAgentAdapter(BaseExternalAgentAdapter):
    """Normalize Jules provider interactions into canonical agent contracts.

    Extends ``BaseExternalAgentAdapter`` to inherit shared validation,
    idempotency caching, and correlation metadata injection.  Only
    provider-specific transport calls remain in this subclass.
    """

    def __init__(self, *, client_factory: JulesClientFactory) -> None:
        super().__init__(accepted_agent_ids=frozenset({"jules", "jules_api"}))
        self._client_factory = client_factory
        self.__client: JulesClient | None = None

    @property
    def _client(self) -> JulesClient:
        if self.__client is None:
            self.__client = self._client_factory()
        return self.__client

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _JULES_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        source_context: SourceContext | None = None
        if request.workspace_spec:
            repo = request.workspace_spec.get("repository") or request.workspace_spec.get("repo")
            if repo:
                branch = str(
                    request.workspace_spec.get("startingBranch")
                    or request.workspace_spec.get("branch")
                    or "main"
                ).strip() or "main"
                source_context = SourceContext.from_repo(repo, branch=branch)

        automation_mode = None
        if request.parameters:
            automation_mode = request.parameters.get("automationMode")
            if not automation_mode and request.parameters.get("publishMode") in (
                "pr",
                "branch",
            ):
                automation_mode = "AUTO_CREATE_PR"

        response = await self._client.create_task(
            JulesCreateTaskRequest(
                title=title,
                description=description,
                metadata=metadata,
                source_context=source_context,
                automation_mode=automation_mode,
            )
        )
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = _normalize_jules_task(response)
        return self.build_handle(
            run_id=response.task_id,
            agent_id="jules",
            status=_JULES_TO_AGENT_RUN_STATUS[normalized_status],
            provider_status=provider_status,
            normalized_status=normalized_status,
            external_url=str(response.url or "").strip() or None,
        )

    async def send_message(self, *, run_id: str, prompt: str) -> AgentRunStatus:
        """Send a follow-up prompt to an existing Jules session.

        Used for multi-step workflows: resumes the session with new
        instructions instead of creating a new session.  Returns a
        running status since the session is now actively processing.
        """
        await self._client.send_message(
            JulesSendMessageRequest(sessionId=run_id, prompt=prompt)
        )
        return self.build_status(
            run_id=run_id,
            agent_id="jules",
            status="running",
            provider_status="IN_PROGRESS",
            normalized_status="running",
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        response = await self._get_task(run_id)
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = _normalize_jules_task(response)
        return self.build_status(
            run_id=response.task_id,
            agent_id="jules",
            status=_JULES_TO_AGENT_RUN_STATUS[normalized_status],
            provider_status=provider_status,
            normalized_status=normalized_status,
            external_url=str(response.url or "").strip() or None,
        )

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        response = await self._get_task(run_id)
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = _normalize_jules_task(response)
        return self.build_result(
            run_id=run_id,
            provider_status=provider_status,
            normalized_status=normalized_status,
            provider_name="Jules",
            external_url=str(response.url or "").strip() or None,
        )

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        try:
            response = await self._client.resolve_task(
                JulesResolveTaskRequest(
                    taskId=run_id,
                    resolutionNotes="Canceled by MoonMind.",
                    status="canceled",
                )
            )
        except JulesClientError:
            return AgentRunStatus(
                runId=run_id,
                agentKind="external",
                agentId="jules",
                status="intervention_requested",
                metadata={"cancelAccepted": False},
            )
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = _normalize_jules_task(response)
        return self.build_status(
            run_id=response.task_id,
            agent_id="jules",
            status=_JULES_TO_AGENT_RUN_STATUS[normalized_status],
            provider_status=provider_status,
            normalized_status=normalized_status,
            external_url=str(response.url or "").strip() or None,
            extra_metadata={"cancelAccepted": True},
        )

    async def _get_task(self, run_id: str) -> JulesTaskResponse:
        return await self._client.get_task(JulesGetTaskRequest(taskId=run_id))


__all__ = ["JulesAgentAdapter"]
