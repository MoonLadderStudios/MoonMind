"""Unit tests for BaseExternalAgentAdapter shared behaviour."""

from __future__ import annotations

from typing import Any

import pytest

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

pytestmark = [pytest.mark.asyncio]

_STUB_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="stub",
    supportsCallbacks=False,
    supportsCancel=True,
    supportsResultFetch=True,
    defaultPollHintSeconds=10,
)


class _StubAdapter(BaseExternalAgentAdapter):
    """Concrete stub for testing the base class."""

    def __init__(
        self,
        *,
        accepted_agent_ids: frozenset[str] = frozenset({"stub"}),
        start_run_id: str = "run-1",
    ) -> None:
        super().__init__(accepted_agent_ids=accepted_agent_ids)
        self.start_run_id = start_run_id
        self.do_start_calls: list[dict[str, Any]] = []
        self.do_status_calls: list[str] = []
        self.do_fetch_result_calls: list[str] = []
        self.do_cancel_calls: list[str] = []

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _STUB_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        self.do_start_calls.append(
            {
                "request": request,
                "title": title,
                "description": description,
                "metadata": metadata,
            }
        )
        return self.build_handle(
            run_id=self.start_run_id,
            agent_id="stub",
            status="queued",
            provider_status="pending",
            normalized_status="queued",
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        self.do_status_calls.append(run_id)
        return self.build_status(
            run_id=run_id,
            agent_id="stub",
            status="running",
            provider_status="in_progress",
            normalized_status="running",
        )

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        self.do_fetch_result_calls.append(run_id)
        return self.build_result(
            run_id=run_id,
            provider_status="completed",
            normalized_status="succeeded",
            provider_name="Stub",
        )

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        self.do_cancel_calls.append(run_id)
        return self.build_status(
            run_id=run_id,
            agent_id="stub",
            status="cancelled",
            provider_status="canceled",
            normalized_status="canceled",
            extra_metadata={"cancelAccepted": True},
        )


def _request(
    *,
    agent_id: str = "stub",
    idempotency_key: str = "idem-1",
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId=agent_id,
        executionProfileRef="profile:stub-default",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        parameters={
            "title": "Test task",
            "description": "Run tests",
            "metadata": {"origin": "unit-test"},
        },
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_start_rejects_wrong_agent_kind():
    adapter = _StubAdapter()
    req = AgentExecutionRequest(
        agentKind="managed",
        agentId="stub",
        executionProfileRef="profile:stub-default",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )
    with pytest.raises(ValueError, match="only supports external"):
        await adapter.start(req)


async def test_start_rejects_wrong_agent_id():
    adapter = _StubAdapter()
    req = _request(agent_id="other")
    with pytest.raises(ValueError, match="only supports agent_id"):
        await adapter.start(req)


# ---------------------------------------------------------------------------
# Idempotency cache
# ---------------------------------------------------------------------------


async def test_start_idempotency_cache_prevents_duplicate_calls():
    adapter = _StubAdapter()
    first = await adapter.start(_request(idempotency_key="same"))
    second = await adapter.start(_request(idempotency_key="same"))

    assert first.run_id == second.run_id
    assert len(adapter.do_start_calls) == 1


async def test_start_different_keys_create_separate_runs():
    adapter = _StubAdapter(start_run_id="run-X")
    await adapter.start(_request(idempotency_key="key-a"))
    await adapter.start(_request(idempotency_key="key-b"))

    assert len(adapter.do_start_calls) == 2


# ---------------------------------------------------------------------------
# Correlation metadata injection
# ---------------------------------------------------------------------------


async def test_start_injects_correlation_metadata():
    adapter = _StubAdapter()
    await adapter.start(_request())

    call = adapter.do_start_calls[0]
    moonmind = call["metadata"].get("moonmind", {})
    assert moonmind.get("correlationId") == "corr-1"
    assert moonmind.get("idempotencyKey") == "idem-1"


# ---------------------------------------------------------------------------
# Forwarding to provider hooks
# ---------------------------------------------------------------------------


async def test_status_delegates_to_do_status():
    adapter = _StubAdapter()
    result = await adapter.status("run-42")

    assert result.run_id == "run-42"
    assert adapter.do_status_calls == ["run-42"]


async def test_fetch_result_delegates_to_do_fetch_result():
    adapter = _StubAdapter()
    result = await adapter.fetch_result("run-42")

    assert result.summary is not None
    assert "completed" in result.summary
    assert adapter.do_fetch_result_calls == ["run-42"]


async def test_cancel_delegates_to_do_cancel():
    adapter = _StubAdapter()
    result = await adapter.cancel("run-42")

    assert result.metadata.get("cancelAccepted") is True
    assert adapter.do_cancel_calls == ["run-42"]


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


def test_build_handle_populates_canonical_fields():
    handle = BaseExternalAgentAdapter.build_handle(
        run_id="r1",
        agent_id="test",
        status="queued",
        provider_status="pending",
        normalized_status="queued",
        external_url="https://example.test",
    )
    assert handle.run_id == "r1"
    assert handle.agent_kind == "external"
    assert handle.metadata["externalUrl"] == "https://example.test"


def test_build_result_sets_failure_class_for_failed():
    result = BaseExternalAgentAdapter.build_result(
        run_id="r1",
        provider_status="error",
        normalized_status="failed",
        provider_name="Test",
    )
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "error"


def test_build_result_sets_failure_class_for_canceled():
    result = BaseExternalAgentAdapter.build_result(
        run_id="r1",
        provider_status="canceled",
        normalized_status="canceled",
        provider_name="Test",
    )
    assert result.failure_class == "execution_error"


def test_build_result_no_failure_for_succeeded():
    result = BaseExternalAgentAdapter.build_result(
        run_id="r1",
        provider_status="completed",
        normalized_status="succeeded",
        provider_name="Test",
    )
    assert result.failure_class is None
    assert result.provider_error_code is None


# ---------------------------------------------------------------------------
# Provider capability descriptor
# ---------------------------------------------------------------------------



async def test_provider_capability_returns_descriptor():
    adapter = _StubAdapter()
    cap = adapter.provider_capability
    assert isinstance(cap, ProviderCapabilityDescriptor)
    assert cap.provider_name == "stub"
    assert cap.supports_cancel is True


# ---------------------------------------------------------------------------
# Capability-aware poll_hint_seconds auto-population (DOC-REQ-010, FR-008)
# ---------------------------------------------------------------------------


async def test_start_populates_poll_hint_from_capability():
    """start() should set poll_hint_seconds from defaultPollHintSeconds when
    the provider hook returns a handle without it."""
    adapter = _StubAdapter()
    handle = await adapter.start(_request())
    assert handle.poll_hint_seconds == _STUB_CAPABILITY.default_poll_hint_seconds


async def test_start_preserves_explicit_poll_hint():
    """If do_start returns a handle with poll_hint_seconds already set,
    start() should NOT overwrite it."""

    class _ExplicitPollAdapter(_StubAdapter):
        async def do_start(self, request, title, description, metadata):
            return self.build_handle(
                run_id="run-explicit",
                agent_id="stub",
                status="queued",
                provider_status="pending",
                normalized_status="queued",
            ).model_copy(update={"poll_hint_seconds": 99})

    adapter = _ExplicitPollAdapter()
    handle = await adapter.start(_request())
    assert handle.poll_hint_seconds == 99, "explicit value must not be overwritten"


# ---------------------------------------------------------------------------
# Capability-aware cancel fallback (DOC-REQ-006, FR-006)
# ---------------------------------------------------------------------------


_NO_CANCEL_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="no_cancel_stub",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=True,
    defaultPollHintSeconds=10,
)


class _NoCancelStubAdapter(_StubAdapter):
    """Stub adapter that declares supportsCancel=False."""

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _NO_CANCEL_CAPABILITY


async def test_cancel_returns_fallback_when_unsupported():
    """cancel() should return a fallback status without calling do_cancel
    when the provider does not support cancellation."""
    adapter = _NoCancelStubAdapter()
    result = await adapter.cancel("run-99")

    assert result.status == "intervention_requested"
    assert result.metadata.get("cancelAccepted") is False
    assert result.metadata.get("unsupported") is True
    assert adapter.do_cancel_calls == [], "do_cancel must NOT be called"


async def test_cancel_delegates_when_supported():
    """cancel() should still delegate to do_cancel when the provider
    supports cancellation (default behaviour)."""
    adapter = _StubAdapter()
    result = await adapter.cancel("run-42")

    assert result.metadata.get("cancelAccepted") is True
    assert adapter.do_cancel_calls == ["run-42"]

