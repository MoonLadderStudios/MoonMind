"""Exercise grace-expiry recovery through the actual managed-run boundary."""

import asyncio
import gzip
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.temporal.workflows import agent_run
from tests.integration.services.temporal.workflows import test_agent_run as harness

pytestmark = pytest.mark.asyncio

PATCH = "agent-run-managed-no-progress-expiry-recovery-v1"
FETCH_BUDGET_PATCH = "agent-run-managed-result-fetch-budget-v1"


def _clock(monkeypatch, *, enabled=True, fetch_budget_enabled=True):
    now = [datetime(2026, 10, 23, tzinfo=UTC)]
    monkeypatch.setattr(agent_run.workflow, "now", lambda: now[0])
    monkeypatch.setattr(
        agent_run.workflow,
        "patched",
        lambda name: (
            fetch_budget_enabled
            if name == FETCH_BUDGET_PATCH
            else enabled or name != PATCH
        ),
    )

    async def wait(_predicate, *, timeout):
        now[0] += timeout
        raise TimeoutError

    monkeypatch.setattr(agent_run.workflow, "wait_condition", wait)
    return now


def _request():
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        correlationId="corr",
        idempotencyKey="idem",
    )


def _status(status):
    return AgentRunStatus(
        runId="run", agentKind="managed", agentId="claude_code", status=status
    )


@pytest.mark.parametrize("status", ["completed", "running"])
async def test_grace_expiry_checks_completion_before_quota_recovery(
    monkeypatch, status
):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    result = AgentRunResult(summary="provider effect retained")
    run._poll_managed_status = AsyncMock(return_value=_status(status))
    run._fetch_managed_result = AsyncMock(return_value=result)
    run._cancel_managed_no_progress_and_fetch_cooldown_result = AsyncMock(
        return_value=result
    )

    actual = await run._reconcile_managed_no_progress(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        resiliency_policy={"noProgressGraceSeconds": 5},
        poll_interval=10,
        initial_status=_status("running"),
        overall_start=now[0],
        timeout_seconds=60,
    )

    assert actual is result
    run._poll_managed_status.assert_awaited_once()
    if status == "completed":
        run._fetch_managed_result.assert_awaited_once()
        run._cancel_managed_no_progress_and_fetch_cooldown_result.assert_not_awaited()
    else:
        run._fetch_managed_result.assert_not_awaited()
        run._cancel_managed_no_progress_and_fetch_cooldown_result.assert_awaited_once()


@pytest.mark.parametrize("status_elapsed,remaining", [(54, 1), (55, 0)])
async def test_expiry_terminal_fetch_stays_within_original_budget(
    monkeypatch, status_elapsed, remaining
):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def status(**_kwargs):
        now[0] += timedelta(seconds=status_elapsed)
        return _status("completed")

    run._poll_managed_status = AsyncMock(side_effect=status)
    run._execute_routed_activity = AsyncMock(return_value={"summary": "retained"})
    actual = await run._reconcile_managed_no_progress(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        resiliency_policy={"noProgressGraceSeconds": 5},
        poll_interval=10,
        initial_status=_status("running"),
        overall_start=now[0],
        timeout_seconds=60,
    )
    if remaining:
        assert actual.summary == "retained"
        kwargs = run._execute_routed_activity.await_args.kwargs
        assert kwargs["start_to_close_timeout"].total_seconds() == remaining
        assert kwargs["schedule_to_close_timeout"].total_seconds() == remaining
    else:
        assert actual is None
        run._execute_routed_activity.assert_not_awaited()


async def test_cancel_terminal_fetch_consumes_status_poll_elapsed_time(monkeypatch):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def status(**_kwargs):
        now[0] += timedelta(seconds=49)
        return _status("completed")

    run._poll_managed_status = AsyncMock(side_effect=status)
    run._execute_routed_activity = AsyncMock(return_value={"summary": "retained"})
    actual = await run._cancel_managed_no_progress_and_fetch_cooldown_result(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        poll_interval=10,
        remaining_overall=60,
    )
    assert actual.summary == "retained"
    kwargs = run._execute_routed_activity.await_args_list[-1].kwargs
    assert kwargs["start_to_close_timeout"].total_seconds() == 1
    assert kwargs["schedule_to_close_timeout"].total_seconds() == 1


@pytest.mark.parametrize("status_elapsed", [54, 55])
async def test_recorded_expiry_fetch_keeps_original_activity_timeouts(
    monkeypatch, status_elapsed
):
    now = _clock(monkeypatch, fetch_budget_enabled=False)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def status(**_kwargs):
        now[0] += timedelta(seconds=status_elapsed)
        return _status("completed")

    run._poll_managed_status = AsyncMock(side_effect=status)
    run._execute_routed_activity = AsyncMock(return_value={"summary": "retained"})
    actual = await run._reconcile_managed_no_progress(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        resiliency_policy={"noProgressGraceSeconds": 5},
        poll_interval=10,
        initial_status=_status("running"),
        overall_start=now[0],
        timeout_seconds=60,
    )

    assert actual.summary == "retained"
    kwargs = run._execute_routed_activity.await_args.kwargs
    assert "start_to_close_timeout" not in kwargs
    assert "schedule_to_close_timeout" not in kwargs


@pytest.mark.parametrize("status_elapsed", [49, 50])
async def test_recorded_cancel_fetch_keeps_original_activity_timeouts(
    monkeypatch, status_elapsed
):
    now = _clock(monkeypatch, fetch_budget_enabled=False)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def status(**_kwargs):
        now[0] += timedelta(seconds=status_elapsed)
        return _status("completed")

    run._poll_managed_status = AsyncMock(side_effect=status)
    run._execute_routed_activity = AsyncMock(return_value={"summary": "retained"})
    actual = await run._cancel_managed_no_progress_and_fetch_cooldown_result(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        poll_interval=10,
        remaining_overall=60,
    )

    assert actual.summary == "retained"
    kwargs = run._execute_routed_activity.await_args_list[-1].kwargs
    assert "start_to_close_timeout" not in kwargs
    assert "schedule_to_close_timeout" not in kwargs


@pytest.mark.parametrize("status_elapsed,remaining", [(4, 1), (5, 0)])
@pytest.mark.parametrize("fetch_budget_enabled", [False, True])
async def test_terminal_status_inside_grace_uses_remaining_fetch_budget(
    monkeypatch, status_elapsed, remaining, fetch_budget_enabled
):
    now = _clock(monkeypatch, fetch_budget_enabled=fetch_budget_enabled)
    overall_start = now[0] - timedelta(seconds=45)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def status(**kwargs):
        # The normal grace poll starts with five seconds left. Completion fits
        # that Activity window; consuming its result must not start a new one.
        assert kwargs["remaining_budget_seconds"] == 5
        now[0] += timedelta(seconds=status_elapsed)
        return _status("completed")

    run._poll_managed_status = AsyncMock(side_effect=status)
    run._execute_routed_activity = AsyncMock(return_value={"summary": "retained"})
    actual = await run._reconcile_managed_no_progress(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        resiliency_policy={"noProgressGraceSeconds": 30},
        poll_interval=10,
        initial_status=_status("running"),
        overall_start=overall_start,
        timeout_seconds=60,
    )

    if fetch_budget_enabled and not remaining:
        assert actual is None
        run._execute_routed_activity.assert_not_awaited()
    else:
        assert actual.summary == "retained"
        kwargs = run._execute_routed_activity.await_args.kwargs
        if fetch_budget_enabled:
            assert kwargs["start_to_close_timeout"].total_seconds() == remaining
            assert kwargs["schedule_to_close_timeout"].total_seconds() == remaining
        else:
            assert "start_to_close_timeout" not in kwargs
            assert "schedule_to_close_timeout" not in kwargs


@pytest.mark.parametrize(
    "budget,elapsed,timeout,translated",
    [
        (5, 5, True, True),
        (5, 4, True, False),
        (None, 5, True, False),
        (5, 5, False, False),
    ],
)
async def test_fetch_timeout_only_becomes_budget_exhaustion_at_its_deadline(
    monkeypatch, caplog, budget, elapsed, timeout, translated
):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"
    monkeypatch.setattr(run, "_get_logger", lambda: logging.getLogger(__name__))
    error = ActivityError(
        "Original result-fetch failure",
        scheduled_event_id=10,
        started_event_id=11,
        identity="test-worker",
        activity_type="agent_runtime.fetch_result",
        activity_id="fetch-1",
        retry_state=None,
    )
    error.__cause__ = (
        TemporalTimeoutError(
            "Result fetch exceeded its Activity timeout",
            type=TimeoutType.SCHEDULE_TO_CLOSE,
            last_heartbeat_details=[],
        )
        if timeout
        else ApplicationError("Original provider read failure")
    )

    async def fail(*_args, **_kwargs):
        now[0] += timedelta(seconds=elapsed)
        raise error

    run._execute_routed_activity = AsyncMock(side_effect=fail)
    expected = asyncio.TimeoutError if translated else ActivityError
    with pytest.raises(expected) as caught:
        await run._fetch_managed_result(
            request=_request(),
            adapter=object(),
            uses_codex_session_adapter=False,
            use_managed_status_activity=True,
            remaining_budget_seconds=budget,
        )
    if translated:
        assert caught.value.__cause__ is error
        assert "Original result-fetch failure" in caplog.text
    else:
        assert caught.value is error


async def test_actual_fetch_activity_timeout_publishes_a_budget_terminal_result(
    monkeypatch,
):
    monkeypatch.setattr(harness, "_managed_launch_requests", [])
    monkeypatch.setattr(harness, "_managed_status_mode", "silent_then_completed")
    monkeypatch.setattr(harness, "_managed_status_poll_count", 0)
    fetch_started = asyncio.Event()
    published = []

    def policy(request, **_options):
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 1,
            "noProgressGraceSeconds": 5,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_managed_grace_reconciliation",
        }

    monkeypatch.setattr(
        agent_run.MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(policy),
    )

    @activity.defn(name="agent_runtime.fetch_result")
    async def pending_result(_request: dict) -> dict:
        fetch_started.set()
        await asyncio.sleep(3600)
        return {}

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def publish(result: AgentRunResult) -> AgentRunResult:
        published.append(result)
        return result

    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="fetch-budget-timeout-workflow",
            workflows=[
                agent_run.MoonMindAgentRun,
                harness.MockProviderProfileManager,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            env.client,
            task_queue="mm.activity.artifacts",
            activities=[
                harness.mock_provider_profile_list,
                harness.mock_provider_profile_ensure_manager,
                harness.mock_provider_profile_reset_manager,
                harness.mock_provider_profile_manager_state,
            ],
        ),
        Worker(
            env.client,
            task_queue="mm.activity.agent_runtime",
            activities=[
                harness.mock_agent_runtime_build_launch_context,
                harness.mock_agent_runtime_launch,
                harness.mock_agent_runtime_status,
                pending_result,
                publish,
            ],
        ),
        Worker(
            env.client,
            task_queue=agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "agent_runtime.cancel"
            ).task_queue,
            activities=[harness.mock_cancel],
        ),
    ):
        await env.client.start_workflow(
            harness.MockProviderProfileManager.run,
            {"runtime_id": "claude_code"},
            id="provider-profile-manager:claude_code",
            task_queue="fetch-budget-timeout-workflow",
        )
        handle = await env.client.start_workflow(
            agent_run.MoonMindAgentRun.run,
            _request().model_copy(
                update={
                    "execution_profile_ref": "default-managed",
                    "timeout_policy": {
                        "timeout_seconds": 27,
                        "max_timeout_seconds": 27,
                        "progress_stall_seconds": 27,
                    },
                }
            ),
            id="test-managed-fetch-budget-timeout",
            task_queue="fetch-budget-timeout-workflow",
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert (await handle.describe()).status.name == "COMPLETED"

    assert fetch_started.is_set()
    assert len(harness._managed_launch_requests) == 1
    assert result.failure_class == "execution_error"
    assert result.metadata["reason"] == "timed_out"
    assert "timed out" in result.summary
    assert published[-1].summary == result.summary


@pytest.mark.parametrize("enabled,budget", [(False, 60), (True, 5)])
async def test_retained_path_and_exhausted_budget_emit_no_new_effects(
    monkeypatch, enabled, budget
):
    now = _clock(monkeypatch, enabled=enabled)
    run = agent_run.MoonMindAgentRun()
    run._poll_managed_status = AsyncMock()
    run._cancel_managed_no_progress_and_fetch_cooldown_result = AsyncMock()

    actual = await run._reconcile_managed_no_progress(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        resiliency_policy={"noProgressGraceSeconds": 5},
        poll_interval=10,
        initial_status=_status("running"),
        overall_start=now[0],
        timeout_seconds=budget,
    )

    assert actual is None
    run._poll_managed_status.assert_not_awaited()
    run._cancel_managed_no_progress_and_fetch_cooldown_result.assert_not_awaited()


@pytest.mark.parametrize("terminal", ["failed", "completed"])
async def test_cancel_reconciliation_retains_terminal_result_at_its_deadline(
    monkeypatch, terminal
):
    _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"
    run._execute_routed_activity = AsyncMock()
    run._poll_managed_status = AsyncMock(
        side_effect=[_status("canceled"), _status(terminal)]
    )
    quota = (
        AgentRunResult(
            summary="quota", failureClass="integration_error", providerErrorCode="429"
        )
        if terminal == "failed"
        else AgentRunResult(summary="completed")
    )
    run._fetch_managed_result = AsyncMock(
        side_effect=[AgentRunResult(summary="canceled"), quota]
    )

    actual = await run._cancel_managed_no_progress_and_fetch_cooldown_result(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        poll_interval=10,
        remaining_overall=60,
    )

    assert actual is quota
    assert run._poll_managed_status.await_count == 2
    assert run._fetch_managed_result.await_count == 2
    run._execute_routed_activity.assert_awaited_once()


async def test_cancel_cleanup_consumes_the_original_remaining_budget(monkeypatch):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run.run_id = "run"

    async def cancel(*_args, **kwargs):
        assert kwargs["start_to_close_timeout"].total_seconds() == 5
        assert kwargs["schedule_to_close_timeout"].total_seconds() == 5
        now[0] += timedelta(seconds=3)

    run._execute_routed_activity = AsyncMock(side_effect=cancel)
    run._poll_managed_status = AsyncMock()
    actual = await run._cancel_managed_no_progress_and_fetch_cooldown_result(
        request=_request(),
        adapter=object(),
        uses_codex_session_adapter=False,
        use_managed_status_activity=True,
        poll_interval=10,
        remaining_overall=5,
    )
    assert actual is None
    run._poll_managed_status.assert_not_awaited()


async def test_cancellation_during_grace_starts_no_recovery(monkeypatch):
    now = _clock(monkeypatch)
    run = agent_run.MoonMindAgentRun()
    run._poll_managed_status = AsyncMock()
    run._cancel_managed_no_progress_and_fetch_cooldown_result = AsyncMock()

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_run.workflow, "wait_condition", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await run._reconcile_managed_no_progress(
            request=_request(),
            adapter=object(),
            uses_codex_session_adapter=False,
            use_managed_status_activity=True,
            resiliency_policy={"noProgressGraceSeconds": 5},
            poll_interval=10,
            initial_status=_status("running"),
            overall_start=now[0],
            timeout_seconds=60,
        )
    run._poll_managed_status.assert_not_awaited()
    run._cancel_managed_no_progress_and_fetch_cooldown_result.assert_not_awaited()


@pytest.mark.parametrize(
    "fixture_name,workflow_id",
    [
        (
            "grace-expired-before-recovery.json.gz",
            "test-parent-managed-no-progress-grace:child",
        ),
        (
            "shipped-expiry-fetch-after-worker-outage.json.gz",
            "test-parent-managed-result-fetch-shipped",
        ),
    ],
)
async def test_recorded_no_progress_histories_replay(
    monkeypatch, fixture_name, workflow_id
):
    # Immutable real-Temporal captures: ee53a90 predates expiry recovery;
    # 0995369 (the identical AgentRun blob at 4907a13) records that patch and
    # fetch_result after a worker outage exhausts the original budget. Never
    # regenerate from the candidate: the old commands are replay evidence.
    fixture = (
        Path(__file__).resolve().parents[4]
        / "fixtures/temporal/managed_no_progress"
        / fixture_name
    )
    history = WorkflowHistory.from_json(
        workflow_id,
        gzip.decompress(fixture.read_bytes()).decode(),
    )

    def policy(request, **_options):
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 1,
            "noProgressGraceSeconds": 5,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_managed_grace_reconciliation",
        }

    monkeypatch.setattr(
        agent_run.MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(policy),
    )
    await Replayer(
        workflows=[agent_run.MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
