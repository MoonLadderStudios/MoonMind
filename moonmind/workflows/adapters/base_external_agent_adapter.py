"""Reusable base class for external-agent adapter implementations.

Extracts shared logic that repeats across provider-specific adapters
(Jules, Codex Cloud, future BYOA) into one canonical pattern.

Provider subclasses override ``do_start``, ``do_status``,
``do_fetch_result``, and ``do_cancel`` while the base handles:

- request validation (``agent_kind == "external"``, agent_id check)
- in-memory idempotency cache per Activity attempt
- MoonMind correlation metadata injection
- normalized ``AgentRunHandle`` / ``AgentRunStatus`` / ``AgentRunResult``
  metadata population
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    ProviderCapabilityDescriptor,
)


def _extract_parameters_metadata(
    parameters: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    """Parse title, description, and metadata from request parameters."""

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


class BaseExternalAgentAdapter(abc.ABC):
    """Universal base for external-agent provider adapters.

    Notes on in-memory idempotency:
        ``_starts_by_idempotency`` is intentionally an in-memory dict.  Each
        adapter instance is constructed per Temporal Activity execution.
        Temporal guarantees at-most-once delivery at the workflow level;
        the in-memory cache only guards against accidental double-submit
        within the same Activity attempt.

        Cross-attempt deduplication is the responsibility of the Temporal
        Workflow and the provider's own idempotency semantics.
    """

    def __init__(self, *, accepted_agent_ids: frozenset[str]) -> None:
        self._accepted_agent_ids = accepted_agent_ids
        self._starts_by_idempotency: dict[str, AgentRunHandle] = {}

    # ------------------------------------------------------------------
    # Provider capability descriptor
    # ------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        """Return the capability descriptor for this provider."""

    # ------------------------------------------------------------------
    # Public contract (AgentAdapter protocol)
    # ------------------------------------------------------------------

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        """Validate, deduplicate, then delegate to ``do_start``."""

        self._validate_request(request)

        cached = self._starts_by_idempotency.get(request.idempotency_key)
        if cached is not None:
            return cached

        title, description, metadata = _extract_parameters_metadata(
            request.parameters
        )
        if not description and request.instruction_ref:
            description = request.instruction_ref
        if not description and request.input_refs:
            description = f"MoonMind artifact refs: {', '.join(request.input_refs)}"
        if not description:
            description = f"MoonMind delegated run {request.correlation_id}"

        metadata = self._inject_correlation_metadata(
            metadata, request.correlation_id, request.idempotency_key
        )

        handle = await self.do_start(request, title, description, metadata)

        self._starts_by_idempotency[request.idempotency_key] = handle
        return handle

    async def status(self, run_id: str) -> AgentRunStatus:
        """Read current provider status for one run."""

        return await self.do_status(run_id)

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        """Fetch terminal result for one run."""

        return await self.do_fetch_result(run_id)

    async def cancel(self, run_id: str) -> AgentRunStatus:
        """Attempt provider cancellation for one run."""

        return await self.do_cancel(run_id)

    # ------------------------------------------------------------------
    # Provider hooks (subclasses override these)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        """Provider-specific start implementation."""

    @abc.abstractmethod
    async def do_status(self, run_id: str) -> AgentRunStatus:
        """Provider-specific status read."""

    @abc.abstractmethod
    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        """Provider-specific result fetch."""

    @abc.abstractmethod
    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        """Provider-specific cancel attempt."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _validate_request(self, request: AgentExecutionRequest) -> None:
        """Validate agent_kind and agent_id."""

        if request.agent_kind != "external":
            raise ValueError(
                f"{self.__class__.__name__} only supports external agent_kind"
            )
        if str(request.agent_id).strip().lower() not in self._accepted_agent_ids:
            raise ValueError(
                f"{self.__class__.__name__} only supports agent_id in "
                f"{sorted(self._accepted_agent_ids)}"
            )

    @staticmethod
    def _inject_correlation_metadata(
        metadata: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Inject MoonMind correlation context into provider metadata."""

        moonmind_meta = metadata.setdefault("moonmind", {})
        if isinstance(moonmind_meta, Mapping):
            moonmind_payload = dict(moonmind_meta)
            moonmind_payload.setdefault("correlationId", correlation_id)
            moonmind_payload.setdefault("idempotencyKey", idempotency_key)
            metadata["moonmind"] = moonmind_payload
        return metadata

    @staticmethod
    def build_handle(
        *,
        run_id: str,
        agent_id: str,
        status: str,
        provider_status: str,
        normalized_status: str,
        external_url: str | None = None,
    ) -> AgentRunHandle:
        """Build a standard ``AgentRunHandle`` with canonical metadata."""

        return AgentRunHandle(
            runId=run_id,
            agentKind="external",
            agentId=agent_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            metadata={
                "providerStatus": provider_status,
                "normalizedStatus": normalized_status,
                "externalUrl": external_url,
            },
        )

    @staticmethod
    def build_status(
        *,
        run_id: str,
        agent_id: str,
        status: str,
        provider_status: str,
        normalized_status: str,
        external_url: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> AgentRunStatus:
        """Build a standard ``AgentRunStatus`` with canonical metadata."""

        meta: dict[str, Any] = {
            "providerStatus": provider_status,
            "normalizedStatus": normalized_status,
            "externalUrl": external_url,
        }
        if extra_metadata:
            meta.update(extra_metadata)
        return AgentRunStatus(
            runId=run_id,
            agentKind="external",
            agentId=agent_id,
            status=status,
            metadata=meta,
        )

    @staticmethod
    def build_result(
        *,
        run_id: str,
        provider_status: str,
        normalized_status: str,
        provider_name: str,
        external_url: str | None = None,
    ) -> AgentRunResult:
        """Build a standard ``AgentRunResult`` with canonical metadata."""

        failure_class = None
        if normalized_status == "failed":
            failure_class = "integration_error"
        elif normalized_status == "canceled":
            failure_class = "execution_error"

        summary = (
            f"{provider_name} task {run_id} ended with "
            f"provider status '{provider_status}'."
        )
        return AgentRunResult(
            outputRefs=[],
            summary=summary,
            failureClass=failure_class,
            providerErrorCode=provider_status if failure_class else None,
            metadata={
                "normalizedStatus": normalized_status,
                "externalUrl": external_url,
            },
        )


__all__ = [
    "BaseExternalAgentAdapter",
    "_extract_parameters_metadata",
]
