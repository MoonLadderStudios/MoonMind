"""Unit tests for Omnigent external adapter registration metadata."""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import OmnigentExternalAdapter


def _request(agent_id: str = "omnigent") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId=agent_id,
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


def test_omnigent_external_adapter_capability_is_streaming_gateway() -> None:
    adapter = OmnigentExternalAdapter()
    cap = adapter.provider_capability

    assert cap.provider_name == "omnigent"
    assert cap.execution_style == "streaming_gateway"
    assert cap.supports_callbacks is False
    assert cap.supports_cancel is False
    assert cap.supports_result_fetch is False


@pytest.mark.asyncio
async def test_omnigent_adapter_accepts_only_canonical_agent_id() -> None:
    adapter = OmnigentExternalAdapter()

    with pytest.raises(RuntimeError, match="integration.omnigent.execute"):
        await adapter.start(_request("omnigent"))

    for alias in (
        "omnigent_session",
        "omnigent_claude",
        "omnigent_codex",
        "omnigent_polly",
    ):
        with pytest.raises(ValueError, match="only supports agent_id"):
            await adapter.start(_request(alias))


@pytest.mark.asyncio
async def test_omnigent_unused_polling_hooks_fail_loudly() -> None:
    adapter = OmnigentExternalAdapter()

    with pytest.raises(RuntimeError, match="status polling is unused"):
        await adapter.do_status("run-1")
    with pytest.raises(RuntimeError, match="fetch_result is unused"):
        await adapter.do_fetch_result("run-1")
    with pytest.raises(RuntimeError, match="activity cancellation"):
        await adapter.do_cancel("run-1")
