"""Unit tests for Codex Cloud Temporal activities."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)

pytestmark = [pytest.mark.asyncio]


def _mock_env():
    """Return env vars that satisfy the Codex Cloud runtime gate."""
    return {
        "CODEX_CLOUD_ENABLED": "true",
        "CODEX_CLOUD_API_URL": "https://codex.test",
        "CODEX_CLOUD_API_KEY": "test-key-codex",
    }


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="codex_cloud",
        executionProfileRef="profile:codex-default",
        correlationId="corr-cc-1",
        idempotencyKey="idem-cc-1",
        parameters={
            "title": "Test Codex Cloud task",
            "description": "Run tests via Codex Cloud",
            "metadata": {"origin": "unit-test"},
        },
    )


# ---------------------------------------------------------------------------
# Activity tests
# ---------------------------------------------------------------------------


async def test_codex_cloud_start_activity_calls_adapter():
    """integration.codex_cloud.start should build adapter and call start()."""

    mock_handle = AgentRunHandle(
        runId="cc-run-1",
        agentKind="external",
        agentId="codex_cloud",
        status="queued",
        startedAt="2026-03-17T12:00:00Z",
        pollHintSeconds=15,
    )
    mock_adapter = AsyncMock()
    mock_adapter.start.return_value = mock_handle

    with (
        patch.dict("os.environ", _mock_env(), clear=False),
        patch(
            "moonmind.workflows.temporal.activities.codex_cloud_activities._build_adapter",
            return_value=mock_adapter,
        ),
    ):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import (
            codex_cloud_start_activity,
        )

        result = await codex_cloud_start_activity(_request())

    assert result.run_id == "cc-run-1"
    mock_adapter.start.assert_awaited_once()


async def test_codex_cloud_status_activity_calls_adapter():
    """integration.codex_cloud.status should build adapter and call status()."""

    mock_status = AgentRunStatus(
        runId="cc-run-1",
        agentKind="external",
        agentId="codex_cloud",
        status="running",
    )
    mock_adapter = AsyncMock()
    mock_adapter.status.return_value = mock_status

    with (
        patch.dict("os.environ", _mock_env(), clear=False),
        patch(
            "moonmind.workflows.temporal.activities.codex_cloud_activities._build_adapter",
            return_value=mock_adapter,
        ),
    ):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import (
            codex_cloud_status_activity,
        )

        result = await codex_cloud_status_activity("cc-run-1")

    assert result.run_id == "cc-run-1"
    assert result.status == "running"
    mock_adapter.status.assert_awaited_once_with("cc-run-1")


async def test_codex_cloud_fetch_result_activity_calls_adapter():
    """integration.codex_cloud.fetch_result should build adapter and call fetch_result()."""

    mock_result = AgentRunResult(summary="Codex Cloud completed")
    mock_adapter = AsyncMock()
    mock_adapter.fetch_result.return_value = mock_result

    with (
        patch.dict("os.environ", _mock_env(), clear=False),
        patch(
            "moonmind.workflows.temporal.activities.codex_cloud_activities._build_adapter",
            return_value=mock_adapter,
        ),
    ):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import (
            codex_cloud_fetch_result_activity,
        )

        result = await codex_cloud_fetch_result_activity("cc-run-1")

    assert result.summary == "Codex Cloud completed"
    mock_adapter.fetch_result.assert_awaited_once_with("cc-run-1")


async def test_codex_cloud_cancel_activity_calls_adapter():
    """integration.codex_cloud.cancel should build adapter and call cancel()."""

    mock_status = AgentRunStatus(
        runId="cc-run-1",
        agentKind="external",
        agentId="codex_cloud",
        status="cancelled",
        metadata={"cancelAccepted": True},
    )
    mock_adapter = AsyncMock()
    mock_adapter.cancel.return_value = mock_status

    with (
        patch.dict("os.environ", _mock_env(), clear=False),
        patch(
            "moonmind.workflows.temporal.activities.codex_cloud_activities._build_adapter",
            return_value=mock_adapter,
        ),
    ):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import (
            codex_cloud_cancel_activity,
        )

        result = await codex_cloud_cancel_activity("cc-run-1")

    assert result.metadata.get("cancelAccepted") is True
    mock_adapter.cancel.assert_awaited_once_with("cc-run-1")


# ---------------------------------------------------------------------------
# Gate test
# ---------------------------------------------------------------------------


async def test_build_adapter_raises_when_disabled():
    """_build_adapter should raise RuntimeError when Codex Cloud is not enabled."""

    with patch.dict("os.environ", {"CODEX_CLOUD_ENABLED": "false"}, clear=False):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import (
            _build_adapter,
        )

        with pytest.raises(RuntimeError, match="CODEX_CLOUD_ENABLED"):
            _build_adapter()
