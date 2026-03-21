"""Unit tests for Temporal Jules activities (thin wrappers around JulesAgentAdapter)."""

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


def _exec_request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-test",
        idempotencyKey="idem-test",
        parameters={
            "title": "Test Jules Task",
            "description": "Integration test task",
        },
    )


def _mock_handle() -> AgentRunHandle:
    from datetime import UTC, datetime

    return AgentRunHandle(
        runId="task-001",
        agentKind="external",
        agentId="jules",
        status="queued",
        startedAt=datetime.now(tz=UTC),
        metadata={
            "providerStatus": "pending",
            "normalizedStatus": "queued",
        },
    )


def _mock_status(*, run_id: str = "task-001") -> AgentRunStatus:
    return AgentRunStatus(
        runId=run_id,
        agentKind="external",
        agentId="jules",
        status="running",
        metadata={
            "providerStatus": "in_progress",
            "normalizedStatus": "running",
        },
    )


def _mock_result(*, run_id: str = "task-001") -> AgentRunResult:
    return AgentRunResult(
        outputRefs=[],
        summary=f"Jules task {run_id} ended with provider status 'completed'.",
        failureClass=None,
    )


def _mock_cancel_status(*, run_id: str = "task-001") -> AgentRunStatus:
    return AgentRunStatus(
        runId=run_id,
        agentKind="external",
        agentId="jules",
        status="cancelled",
        metadata={
            "providerStatus": "canceled",
            "normalizedStatus": "canceled",
            "cancelAccepted": True,
        },
    )


class _FakeAdapter:
    """Lightweight adapter fake for testing activity wrappers."""

    def __init__(self) -> None:
        self.start = AsyncMock(return_value=_mock_handle())
        self.status = AsyncMock(return_value=_mock_status())
        self.fetch_result = AsyncMock(return_value=_mock_result())
        self.cancel = AsyncMock(return_value=_mock_cancel_status())
        self.send_message = AsyncMock(return_value=_mock_status())


@pytest.fixture
def _patch_build_adapter():
    """Patch _build_adapter to return a fake adapter."""
    fake = _FakeAdapter()
    with patch(
        "moonmind.workflows.temporal.activities.jules_activities._build_adapter",
        return_value=fake,
    ):
        yield fake


async def test_jules_start_activity_calls_adapter(_patch_build_adapter):
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_start_activity,
    )

    result = await jules_start_activity(_exec_request())
    assert result.run_id == "task-001"
    assert result.status == "queued"
    _patch_build_adapter.start.assert_awaited_once()


async def test_jules_status_activity_calls_adapter(_patch_build_adapter):
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_status_activity,
    )

    result = await jules_status_activity("task-001")
    assert result.run_id == "task-001"
    _patch_build_adapter.status.assert_awaited_once_with("task-001")


async def test_jules_fetch_result_activity_calls_adapter(_patch_build_adapter):
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_fetch_result_activity,
    )

    result = await jules_fetch_result_activity("task-001")
    assert result.summary is not None
    _patch_build_adapter.fetch_result.assert_awaited_once_with("task-001")


async def test_jules_cancel_activity_calls_adapter(_patch_build_adapter):
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_cancel_activity,
    )

    result = await jules_cancel_activity("task-001")
    assert result.metadata.get("cancelAccepted") is True
    _patch_build_adapter.cancel.assert_awaited_once_with("task-001")


async def test_build_adapter_raises_when_disabled():
    """Verify _build_adapter raises RuntimeError when Jules is explicitly disabled."""
    from moonmind.workflows.temporal.activities.jules_activities import _build_adapter

    with patch.dict(
        "os.environ",
        {"JULES_ENABLED": "false", "JULES_API_URL": "", "JULES_API_KEY": "some-key"},
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="JULES_ENABLED"):
            _build_adapter()


async def test_build_adapter_raises_when_no_api_key():
    """Verify _build_adapter raises RuntimeError when API key is missing."""
    from moonmind.workflows.temporal.activities.jules_activities import _build_adapter

    with patch.dict(
        "os.environ",
        {"JULES_API_KEY": ""},
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="JULES_API_KEY"):
            _build_adapter()


async def test_jules_send_message_activity_calls_adapter(_patch_build_adapter):
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_send_message_activity,
    )

    result = await jules_send_message_activity({
        "session_id": "session-42",
        "prompt": "Continue with step 2.",
    })
    assert result.run_id == "task-001"
    _patch_build_adapter.send_message.assert_awaited_once_with(
        run_id="session-42",
        prompt="Continue with step 2.",
    )
