"""External-agent adapter implementation backed by the Codex Cloud provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
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
    "succeeded": "completed",
    "failed": "failed",
    "canceled": "cancelled",
    "unknown": "awaiting_callback",
}


def _to_agent_status(raw_status: str | None) -> str:
    normalized = normalize_codex_cloud_status(raw_status)
    return _CODEX_CLOUD_TO_AGENT_RUN_STATUS.get(normalized, "awaiting_callback")


def _extract_parameters_metadata(
    parameters: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    payload = dict(parameters or {})
    title = str(payload.get("title") or "MoonMind Agent Task").strip()
    description = str(payload.get("description") or "").strip()
    metadata = payload.get("metadata")
    if metadata is None:
        metadata_payload: dict[str, Any] = {}
    elif isinstance(metadata, Mapping):
        metadata_payload = dict(metadata)
    else:
        raise ValueError("parameters.metadata must be an object")
    return title, description, metadata_payload


class CodexCloudAgentAdapter:
    """Normalize Codex Cloud provider interactions into canonical agent contracts.

    Follows the same pattern as ``JulesAgentAdapter``:
    - Client lifecycle: single client created at construction, reused for pooling.
    - In-memory idempotency guard per activity attempt.
    """

    def __init__(self, *, client_factory: CodexCloudClientFactory) -> None:
        self._client_factory = client_factory
        self.__client: CodexCloudClient | None = None
        self._starts_by_idempotency: dict[str, AgentRunHandle] = {}

    @property
    def _client(self) -> CodexCloudClient:
        if self.__client is None:
            self.__client = self._client_factory()
        return self.__client

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        if request.agent_kind != "external":
            raise ValueError("CodexCloudAgentAdapter only supports external agent_kind")
        if str(request.agent_id).strip().lower() != "codex_cloud":
            raise ValueError("CodexCloudAgentAdapter only supports agent_id=codex_cloud")

        cached = self._starts_by_idempotency.get(request.idempotency_key)
        if cached is not None:
            return cached

        title, description, metadata = _extract_parameters_metadata(request.parameters)
        if not description and request.instruction_ref:
            description = request.instruction_ref
        if not description and request.input_refs:
            description = f"MoonMind artifact refs: {', '.join(request.input_refs)}"
        if not description:
            description = f"MoonMind delegated run {request.correlation_id}"

        moonmind_meta = metadata.setdefault("moonmind", {})
        if isinstance(moonmind_meta, Mapping):
            moonmind_payload = dict(moonmind_meta)
            moonmind_payload.setdefault("correlationId", request.correlation_id)
            moonmind_payload.setdefault("idempotencyKey", request.idempotency_key)
            metadata["moonmind"] = moonmind_payload

        response = await self._client.create_task(
            title=title,
            description=description,
            metadata=metadata,
        )

        task_id = str(response.get("taskId") or response.get("id") or "")
        provider_status = str(response.get("status") or "").strip() or "unknown"

        handle = AgentRunHandle(
            runId=task_id,
            agentKind="external",
            agentId="codex_cloud",
            status=_to_agent_status(provider_status),
            startedAt=datetime.now(tz=UTC),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalize_codex_cloud_status(provider_status),
                "externalUrl": str(response.get("url") or "").strip() or None,
            },
        )
        self._starts_by_idempotency[request.idempotency_key] = handle
        return handle

    async def status(self, run_id: str) -> AgentRunStatus:
        response = await self._client.get_task(run_id)
        provider_status = str(response.get("status") or "").strip() or "unknown"
        normalized_status = normalize_codex_cloud_status(provider_status)
        return AgentRunStatus(
            runId=run_id,
            agentKind="external",
            agentId="codex_cloud",
            status=_to_agent_status(provider_status),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalized_status,
                "externalUrl": str(response.get("url") or "").strip() or None,
            },
        )

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        response = await self._client.get_task(run_id)
        provider_status = str(response.get("status") or "").strip() or "unknown"
        normalized_status = normalize_codex_cloud_status(provider_status)
        failure_class = None
        if normalized_status == "failed":
            failure_class = "integration_error"
        elif normalized_status == "canceled":
            failure_class = "execution_error"

        summary = f"Codex Cloud task {run_id} ended with provider status '{provider_status}'."
        return AgentRunResult(
            outputRefs=[],
            summary=summary,
            failureClass=failure_class,
            providerErrorCode=provider_status if failure_class else None,
            metadata={
                "normalizedStatus": normalized_status,
                "externalUrl": str(response.get("url") or "").strip() or None,
            },
        )

    async def cancel(self, run_id: str) -> AgentRunStatus:
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
        return AgentRunStatus(
            runId=run_id,
            agentKind="external",
            agentId="codex_cloud",
            status=_to_agent_status(provider_status),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalize_codex_cloud_status(provider_status),
                "externalUrl": str(response.get("url") or "").strip() or None,
                "cancelAccepted": True,
            },
        )


__all__ = ["CodexCloudAgentAdapter"]
