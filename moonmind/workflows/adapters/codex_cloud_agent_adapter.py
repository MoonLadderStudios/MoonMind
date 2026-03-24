"""External-agent adapter implementation backed by the Codex Cloud provider."""

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
from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)
from moonmind.workflows.adapters.codex_cloud_client import (
    CodexCloudClient,
    CodexCloudClientError,
    normalize_codex_cloud_status,
)

CodexCloudClientFactory = Callable[[], CodexCloudClient]

_CODEX_CLOUD_TO_AGENT_RUN_STATUS: dict[str, str] = {
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "canceled": "canceled",
    "unknown": "awaiting_callback",
}


def _to_agent_status(raw_status: str | None) -> str:
    normalized = normalize_codex_cloud_status(raw_status)
    return _CODEX_CLOUD_TO_AGENT_RUN_STATUS.get(normalized, "awaiting_callback")


_CODEX_CLOUD_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="codex_cloud",
    supportsCallbacks=False,
    supportsCancel=True,
    supportsResultFetch=True,
    defaultPollHintSeconds=15,
)


class CodexCloudAgentAdapter(BaseExternalAgentAdapter):
    """Normalize Codex Cloud provider interactions into canonical agent contracts.

    Extends ``BaseExternalAgentAdapter`` to inherit shared validation,
    idempotency caching, and correlation metadata injection.
    """

    def __init__(self, *, client_factory: CodexCloudClientFactory) -> None:
        super().__init__(accepted_agent_ids=frozenset({"codex_cloud"}))
        self._client_factory = client_factory
        self.__client: CodexCloudClient | None = None

    @property
    def _client(self) -> CodexCloudClient:
        if self.__client is None:
            self.__client = self._client_factory()
        return self.__client

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _CODEX_CLOUD_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        response = await self._client.create_task(
            title=title,
            description=description,
            metadata=metadata,
        )

        task_id = str(response.get("taskId") or response.get("id") or "")
        provider_status = str(response.get("status") or "").strip() or "unknown"

        return self.build_handle(
            run_id=task_id,
            agent_id="codex_cloud",
            status=_to_agent_status(provider_status),
            provider_status=provider_status,
            normalized_status=normalize_codex_cloud_status(provider_status),
            external_url=str(response.get("url") or "").strip() or None,
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        response = await self._client.get_task(run_id)
        provider_status = str(response.get("status") or "").strip() or "unknown"
        normalized_status = normalize_codex_cloud_status(provider_status)
        return self.build_status(
            run_id=run_id,
            agent_id="codex_cloud",
            status=_to_agent_status(provider_status),
            provider_status=provider_status,
            normalized_status=normalized_status,
            external_url=str(response.get("url") or "").strip() or None,
        )

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        response = await self._client.get_task(run_id)
        provider_status = str(response.get("status") or "").strip() or "unknown"
        normalized_status = normalize_codex_cloud_status(provider_status)
        return self.build_result(
            run_id=run_id,
            provider_status=provider_status,
            normalized_status=normalized_status,
            provider_name="Codex Cloud",
            external_url=str(response.get("url") or "").strip() or None,
        )

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        try:
            response = await self._client.cancel_task(run_id)
        except CodexCloudClientError as exc:
            if exc.status_code in {404, 405, 501}:
                return AgentRunStatus(
                    runId=run_id,
                    agentKind="external",
                    agentId="codex_cloud",
                    status="intervention_requested",
                    metadata={
                        "cancelAccepted": False,
                        "unsupported": True,
                    },
                )
            return AgentRunStatus(
                runId=run_id,
                agentKind="external",
                agentId="codex_cloud",
                status="intervention_requested",
                metadata={"cancelAccepted": False},
            )

        provider_status = str(response.get("status") or "").strip() or "canceled"
        return self.build_status(
            run_id=run_id,
            agent_id="codex_cloud",
            status=_to_agent_status(provider_status),
            provider_status=provider_status,
            normalized_status=normalize_codex_cloud_status(provider_status),
            external_url=str(response.get("url") or "").strip() or None,
            extra_metadata={"cancelAccepted": True},
        )


__all__ = ["CodexCloudAgentAdapter"]
