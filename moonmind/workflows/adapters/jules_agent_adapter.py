"""External-agent adapter implementation backed by the Jules provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
    JulesTaskResponse,
    normalize_jules_status,
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


class JulesAgentAdapter:
    """Normalize Jules provider interactions into canonical agent contracts."""

    def __init__(self, *, client_factory: JulesClientFactory) -> None:
        self._client_factory = client_factory
        self._starts_by_idempotency: dict[str, AgentRunHandle] = {}

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        if request.agent_kind != "external":
            raise ValueError("JulesAgentAdapter only supports external agent_kind")
        if str(request.agent_id).strip().lower() not in {"jules", "jules_api"}:
            raise ValueError("JulesAgentAdapter only supports agent_id=jules")

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

        client = self._client_factory()
        try:
            response = await client.create_task(
                JulesCreateTaskRequest(
                    title=title,
                    description=description,
                    metadata=metadata,
                )
            )
        finally:
            await client.aclose()

        handle = AgentRunHandle(
            runId=response.task_id,
            agentKind="external",
            agentId="jules",
            status=_to_agent_status(response.status),
            startedAt=datetime.now(tz=UTC),
            metadata={
                "providerStatus": str(response.status or "").strip() or "unknown",
                "normalizedStatus": normalize_jules_status(response.status),
                "externalUrl": str(response.url or "").strip() or None,
            },
        )
        self._starts_by_idempotency[request.idempotency_key] = handle
        return handle

    async def status(self, run_id: str) -> AgentRunStatus:
        response = await self._get_task(run_id)
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = normalize_jules_status(provider_status)
        return AgentRunStatus(
            runId=response.task_id,
            agentKind="external",
            agentId="jules",
            status=_to_agent_status(provider_status),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalized_status,
                "externalUrl": str(response.url or "").strip() or None,
            },
        )

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        response = await self._get_task(run_id)
        provider_status = str(response.status or "").strip() or "unknown"
        normalized_status = normalize_jules_status(provider_status)
        failure_class = None
        if normalized_status == "failed":
            failure_class = "integration_error"
        elif normalized_status == "canceled":
            failure_class = "execution_error"

        summary = f"Jules task {run_id} ended with provider status '{provider_status}'."
        return AgentRunResult(
            outputRefs=[],
            summary=summary,
            failureClass=failure_class,
            providerErrorCode=provider_status if failure_class else None,
            metadata={
                "normalizedStatus": normalized_status,
                "externalUrl": str(response.url or "").strip() or None,
            },
        )

    async def cancel(self, run_id: str) -> AgentRunStatus:
        client = self._client_factory()
        try:
            try:
                response = await client.resolve_task(
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
        finally:
            await client.aclose()

        provider_status = str(response.status or "").strip() or "canceled"
        return AgentRunStatus(
            runId=response.task_id,
            agentKind="external",
            agentId="jules",
            status=_to_agent_status(provider_status),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalize_jules_status(provider_status),
                "externalUrl": str(response.url or "").strip() or None,
                "cancelAccepted": True,
            },
        )

    async def _get_task(self, run_id: str) -> JulesTaskResponse:
        client = self._client_factory()
        try:
            response = await client.get_task(JulesGetTaskRequest(taskId=run_id))
        finally:
            await client.aclose()
        return response


__all__ = ["JulesAgentAdapter"]
