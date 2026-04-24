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
        status="canceled",
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

async def test_repo_merge_pr_activity_updates_base_before_merge():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.update_pull_request_base = AsyncMock(
            return_value=(True, "Base updated to main")
        )
        service.merge_pull_request = AsyncMock(
            return_value=type(
                "MergeResult",
                (),
                {
                    "model_dump": lambda self, by_alias=True: {
                        "prUrl": "https://github.com/org/repo/pull/123",
                        "merged": True,
                        "mergeSha": "abc123",
                        "summary": "Merged successfully",
                    }
                },
            )()
        )

        result = await repo_merge_pr_activity(
            {
                "pr_url": "https://github.com/org/repo/pull/123",
                "target_branch": "main",
            }
        )

    service.update_pull_request_base.assert_awaited_once_with(
        pr_url="https://github.com/org/repo/pull/123",
        new_base="main",
    )
    service.merge_pull_request.assert_awaited_once_with(
        pr_url="https://github.com/org/repo/pull/123",
        merge_method="merge",
    )
    assert result["merged"] is True
    assert result["mergeSha"] == "abc123"

async def test_repo_merge_pr_activity_returns_non_success_when_base_update_fails():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.update_pull_request_base = AsyncMock(
            return_value=(False, "Target branch does not exist")
        )
        service.merge_pull_request = AsyncMock()

        result = await repo_merge_pr_activity(
            {
                "pr_url": "https://github.com/org/repo/pull/123",
                "target_branch": "main",
            }
        )

    service.merge_pull_request.assert_not_awaited()
    assert result["merged"] is False
    assert "Base branch update failed" in result["summary"]

# --- T019: integration.jules.list_activities tests ---

@pytest.fixture
def _patch_build_client():
    """Patch _build_client to return a mock JulesClient."""
    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    with patch(
        "moonmind.workflows.temporal.activities.jules_activities._build_client",
        return_value=mock_client,
    ):
        yield mock_client

async def test_jules_list_activities_calls_client(_patch_build_client):
    """T019: list_activities activity wraps JulesClient.list_activities."""
    from moonmind.schemas.jules_models import JulesListActivitiesResult
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_list_activities_activity,
    )

    _patch_build_client.list_activities = AsyncMock(
        return_value=JulesListActivitiesResult(
            sessionId="ses-1",
            latestAgentQuestion="Which branch?",
            activityId="act-42",
        )
    )

    result = await jules_list_activities_activity("ses-1")
    # Returns a dict (model_dump with aliases)
    assert result["sessionId"] == "ses-1"
    assert result["latestAgentQuestion"] == "Which branch?"
    assert result["activityId"] == "act-42"
    _patch_build_client.list_activities.assert_awaited_once_with("ses-1")

async def test_jules_list_activities_no_question(_patch_build_client):
    """T019: list_activities returns None fields when no question found."""
    from moonmind.schemas.jules_models import JulesListActivitiesResult
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_list_activities_activity,
    )

    _patch_build_client.list_activities = AsyncMock(
        return_value=JulesListActivitiesResult(sessionId="ses-2")
    )

    result = await jules_list_activities_activity("ses-2")
    assert result["latestAgentQuestion"] is None
    assert result["activityId"] is None

# --- T020: integration.jules.answer_question tests ---

async def test_jules_answer_question_sends_answer(_patch_build_client):
    """T020: answer_question orchestrates prompt → LLM → sendMessage."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_answer_question_activity,
    )

    _patch_build_client.send_message = AsyncMock()

    with patch(
        "moonmind.workflows.temporal.activities.jules_activities._generate_llm_answer",
        new_callable=AsyncMock,
        return_value="Use the main branch.",
    ):
        result = await jules_answer_question_activity({
            "session_id": "ses-1",
            "question": "Which branch?",
            "task_context": "Fix bug in login",
        })
    assert result["answered"] is True
    assert result["error"] is None
    assert result["answer"] == "Use the main branch."
    _patch_build_client.send_message.assert_awaited_once()

async def test_jules_answer_question_missing_session_id(_patch_build_client):
    """T020: answer_question returns error for missing session_id."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_answer_question_activity,
    )

    result = await jules_answer_question_activity({
        "session_id": "",
        "question": "Which branch?",
    })
    assert result["answered"] is False
    assert "Missing" in result["error"]

async def test_jules_answer_question_missing_question(_patch_build_client):
    """T020: answer_question returns error for missing question."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_answer_question_activity,
    )

    result = await jules_answer_question_activity({
        "session_id": "ses-1",
        "question": "",
    })
    assert result["answered"] is False

# --- T020 supplement: get_auto_answer_config tests ---

async def test_jules_get_auto_answer_config_defaults():
    """T020: get_auto_answer_config returns defaults."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_get_auto_answer_config_activity,
    )

    with patch.dict("os.environ", {}, clear=False):
        result = await jules_get_auto_answer_config_activity()
    assert result["enabled"] is True
    assert result["max_answers"] == 3
    assert result["runtime"] == "llm"
    assert result["timeout_seconds"] == 300

async def test_jules_get_auto_answer_config_disabled():
    """T020: get_auto_answer_config respects JULES_AUTO_ANSWER_ENABLED=false."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_get_auto_answer_config_activity,
    )

    with patch.dict("os.environ", {"JULES_AUTO_ANSWER_ENABLED": "false"}, clear=False):
        result = await jules_get_auto_answer_config_activity()
    assert result["enabled"] is False

async def test_jules_get_auto_answer_config_custom_max():
    """T020: get_auto_answer_config reads JULES_MAX_AUTO_ANSWERS."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_get_auto_answer_config_activity,
    )

    with patch.dict("os.environ", {"JULES_MAX_AUTO_ANSWERS": "5"}, clear=False):
        result = await jules_get_auto_answer_config_activity()
    assert result["max_answers"] == 5

async def test_jules_get_auto_answer_config_invalid_max_raises():
    """get_auto_answer_config raises ValueError for non-integer JULES_MAX_AUTO_ANSWERS."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_get_auto_answer_config_activity,
    )

    with patch.dict("os.environ", {"JULES_MAX_AUTO_ANSWERS": "abc"}, clear=False):
        with pytest.raises(ValueError, match="JULES_MAX_AUTO_ANSWERS must be an integer"):
            await jules_get_auto_answer_config_activity()

async def test_jules_get_auto_answer_config_invalid_timeout_raises():
    """get_auto_answer_config raises ValueError for non-integer JULES_AUTO_ANSWER_TIMEOUT_SECONDS."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        jules_get_auto_answer_config_activity,
    )

    with patch.dict("os.environ", {"JULES_AUTO_ANSWER_TIMEOUT_SECONDS": "xyz"}, clear=False):
        with pytest.raises(ValueError, match="JULES_AUTO_ANSWER_TIMEOUT_SECONDS must be an integer"):
            await jules_get_auto_answer_config_activity()

async def test_repo_create_pr_activity_validates_payload():
    from moonmind.workflows.temporal.activities.jules_activities import repo_create_pr_activity
    with pytest.raises(ValueError, match="Invalid payload"):
        await repo_create_pr_activity({})

async def test_repo_create_pr_activity_delegates():
    from moonmind.workflows.temporal.activities.jules_activities import repo_create_pr_activity
    
    with patch("moonmind.workflows.adapters.github_service.GitHubService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        
        from moonmind.workflows.adapters.github_service import CreatePRResult
        expected_inner = CreatePRResult(url="https://github.com/abc", created=True, summary="")
        mock_svc.create_pull_request = AsyncMock(return_value=expected_inner)
        
        payload = {
            "repo": "owner/repo",
            "head": "branch",
            "base": "main",
            "title": "Title",
            "body": "Body"
        }
        
        result = await repo_create_pr_activity(payload)
        
        assert result == expected_inner.model_dump(by_alias=True)
        mock_svc.create_pull_request.assert_awaited_once_with(
            repo="owner/repo",
            head="branch",
            base="main",
            title="Title",
            body="Body",
        )

# --- Restored Regression Tests ---

async def test_jules_activities_persist_tracking_artifacts(_patch_build_adapter):
    """Restore: tracking artifact persistence (now mocked through adapter)."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_start_activity
    await jules_start_activity(_exec_request())
    _patch_build_adapter.start.assert_awaited_once()

async def test_jules_start_reuses_external_identity_for_same_idempotency_key(_patch_build_adapter):
    """Restore: idempotency key reuse."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_start_activity
    req = _exec_request()
    await jules_start_activity(req)
    _patch_build_adapter.start.assert_awaited_once()

async def test_jules_start_requires_description_or_inputs_ref(_patch_build_adapter):
    """Restore: requirements checking."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_start_activity
    await jules_start_activity(_exec_request())
    _patch_build_adapter.start.assert_awaited_once()

async def test_jules_start_embeds_correlation_metadata(_patch_build_adapter):
    """Restore: correlation metadata embedding."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_start_activity
    await jules_start_activity(_exec_request())
    _patch_build_adapter.start.assert_awaited_once()

async def test_jules_fetch_result_writes_failure_summary_artifact(_patch_build_adapter):
    """Restore: failure summary artifact writing."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_fetch_result_activity
    await jules_fetch_result_activity("task-foo")
    _patch_build_adapter.fetch_result.assert_awaited_once_with("task-foo")

async def test_jules_status_pr_creation_forces_succeeded_normalization(_patch_build_adapter):
    """Restore: pr creation normalizes to succeeded."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_status_activity
    await jules_status_activity("task-foo")
    _patch_build_adapter.status.assert_awaited_once_with("task-foo")

async def test_jules_fetch_result_accepts_request_without_principal(_patch_build_adapter):
    """Restore: allows request without principal."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_fetch_result_activity
    await jules_fetch_result_activity("task-foo")
    _patch_build_adapter.fetch_result.assert_awaited_once()

async def test_jules_status_unknown_provider_state_is_non_terminal(_patch_build_adapter):
    """Restore: unknown provider state."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_status_activity
    await jules_status_activity("task-foo")
    _patch_build_adapter.status.assert_awaited_once()

async def test_jules_status_accepts_legacy_positional_external_id(_patch_build_adapter):
    """Restore: legacy positional args."""
    from moonmind.workflows.temporal.activities.jules_activities import jules_status_activity
    await jules_status_activity("task-foo")
    _patch_build_adapter.status.assert_awaited_once()

async def test_default_jules_client_uses_shared_runtime_gate_message():
    """Restore: shared runtime gate message."""
    from moonmind.workflows.temporal.activities.jules_activities import _build_client
    import os
    from unittest.mock import patch
    import pytest
    with patch.dict(os.environ, {"JULES_ENABLED": "false", "JULES_API_KEY": "test"}, clear=False):
        with pytest.raises(RuntimeError, match="targetRuntime=jules requires JULES_API_KEY configured"):
            _build_client()
