"""Cumulative allowance at production dispatch boundaries for issue #4986."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.schemas import agent_runtime_models as models
from moonmind.workflows.temporal.workflows.omnigent_session import (
    MoonMindOmnigentSessionWorkflow,
)

START = datetime(2026, 10, 31, tzinfo=UTC)


def context(**updates):
    return models.ExecutionBudgetContext(
        budget=models.resolve_execution_budget(
            timeout_policy={
                "timeout_seconds": 10,
                "max_timeout_seconds": 30,
                "progress_stall_seconds": 5,
            }
        ),
        logicalStepId="logical-step",
        attemptId="attempt-1",
        admittedAt=START,
        **updates,
    )


def test_automatic_successor_preserves_consumption_and_progress():
    first = context().observe_progress(START + timedelta(seconds=8))
    second = first.successor(now=START + timedelta(seconds=9), attempt_id="attempt-2")
    assert second.logical_step_id == first.logical_step_id
    assert second.attempt_id == "attempt-2"
    assert second.recovery_count == 1
    assert second.remaining_seconds(START + timedelta(seconds=10)) == 3
    assert second.remaining_seconds(START + timedelta(seconds=13)) == 0
    assert second.budget == first.budget


def test_capacity_and_pause_exclude_only_the_recorded_wait():
    first = context().suspend(START + timedelta(seconds=3))
    assert first.elapsed_seconds(START + timedelta(seconds=103)) == 3
    resumed = first.resume(START + timedelta(seconds=103))
    assert resumed.remaining_seconds(START + timedelta(seconds=105)) == 5
    assert resumed.remaining_seconds(START + timedelta(seconds=110)) == 0


def test_finishing_is_one_bounded_allowance_and_cannot_authorize_a_successor():
    first = context().begin_finishing(START + timedelta(seconds=10))
    again = first.begin_finishing(START + timedelta(seconds=25))
    assert again.finishing_deadline == first.finishing_deadline
    assert first.remaining_seconds(START + timedelta(seconds=11)) == 0
    with pytest.raises(models.ExecutionBudgetExhausted):
        first.activity_timeouts(
            now=START + timedelta(seconds=11),
            start_to_close_seconds=60,
            schedule_to_close_seconds=180,
        )
    with pytest.raises(models.ExecutionBudgetExhausted):
        first.successor(now=START + timedelta(seconds=11), attempt_id="attempt-2")
    assert first.activity_timeouts(
        now=first.finishing_deadline - timedelta(seconds=2),
        start_to_close_seconds=60,
        schedule_to_close_seconds=180,
        finishing=True,
    ) == {
        "start_to_close_timeout": timedelta(seconds=2),
        "schedule_to_close_timeout": timedelta(seconds=2),
    }


def test_serialized_state_does_not_refresh_a_worker_or_activity_retry():
    first = context().observe_progress(START + timedelta(seconds=8))
    restored = models.ExecutionBudgetContext.model_validate_json(
        first.model_dump_json(by_alias=True)
    )
    assert restored.activity_timeouts(
        now=START + timedelta(seconds=12),
        start_to_close_seconds=60,
        schedule_to_close_seconds=180,
    )["schedule_to_close_timeout"] == timedelta(seconds=1)


def test_failed_progress_observation_shares_its_deadline_with_finishing():
    first = context()
    observation = first.begin_finishing(START + timedelta(seconds=10))
    finished = first.begin_finishing(
        START + timedelta(seconds=15), deadline=observation.finishing_deadline
    )
    assert finished.finishing_deadline == observation.finishing_deadline
    assert finished.elapsed_seconds(START + timedelta(seconds=20)) == 15
    assert finished.observe_progress(START + timedelta(seconds=20)) == finished
    assert (
        finished.begin_finishing(
            START + timedelta(seconds=20), deadline=START + timedelta(seconds=1000)
        )
        == finished
    )
    # A caller-supplied deadline can only shorten the fixed finishing allowance.
    assert (
        first.begin_finishing(
            START + timedelta(seconds=10), deadline=START + timedelta(seconds=1000)
        )
        == observation
    )


def test_profile_bridge_uses_admitted_remaining_time_without_retry_reserve():
    from moonmind.omnigent import execute

    request = models.AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow",
        idempotencyKey="key",
        timeoutPolicy={"timeout_seconds": 10},
        executionBudget=context(),
    )
    with patch.object(execute, "datetime", create=True, wraps=datetime) as clock:
        clock.now.return_value = START + timedelta(seconds=8)
        assert execute._resolved_marked_turn_timeout_seconds(request) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "activity_name",
    ["omnigent.submit_turn", "omnigent.observe_snapshot", "omnigent.read_event_batch"],
)
async def test_session_dispatch_uses_remaining_allowance(activity_name):
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execution_budget = context()
    execute = AsyncMock(return_value={})
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=START + timedelta(seconds=9),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.patched",
            return_value=True,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.execute_activity",
            execute,
        ),
    ):
        await supervisor._execute_activity(activity_name, {})
    assert execute.await_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=1
    )
    assert execute.await_args.kwargs["start_to_close_timeout"] == timedelta(seconds=1)


@pytest.mark.asyncio
async def test_terminal_saving_does_not_refresh_finishing_allowance():
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execution_budget = context().begin_finishing(
        START + timedelta(seconds=10)
    )
    deadline = supervisor._execution_budget.finishing_deadline
    execute = AsyncMock(return_value={})
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=deadline - timedelta(seconds=1),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.patched",
            return_value=True,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.execute_activity",
            execute,
        ),
    ):
        await supervisor._execute_activity("omnigent.harvest_evidence", {})
    assert execute.await_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=1
    )
    execute.reset_mock()
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=deadline,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.execute_activity",
            execute,
        ),
    ):
        with pytest.raises(models.ExecutionBudgetExhausted):
            await supervisor._execute_activity("omnigent.harvest_evidence", {})
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_finishing_cannot_submit_task_work():
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execution_budget = context().begin_finishing(
        START + timedelta(seconds=9)
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=START + timedelta(seconds=10),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.patched",
            return_value=True,
        ),
    ):
        with pytest.raises(models.ExecutionBudgetExhausted):
            await supervisor._execute_activity("omnigent.submit_turn", {})


@pytest.mark.asyncio
async def test_session_capacity_wait_excludes_only_its_own_interval():
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execution_budget = context()
    now = [START + timedelta(seconds=2)]

    async def capacity(*_args, **_kwargs):
        now[0] += timedelta(seconds=100)
        return {}

    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            side_effect=lambda: now[0],
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.execute_activity",
            side_effect=capacity,
        ),
    ):
        await supervisor._execute_activity("omnigent.ensure_provider_profile_lease", {})
    assert supervisor._execution_budget.remaining_seconds(now[0]) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_load", [False, True])
@pytest.mark.parametrize(
    "elapsed,progress_source,expected_remaining",
    [
        (10, "snapshot", 5),
        (10, "batch", 5),
        (10, None, 0),
        (30, "snapshot", 0),
        (10, "failed_snapshot", 0),
    ],
)
async def test_session_final_observation_evaluates_progress_before_finishing(
    initial_load, elapsed, progress_source, expected_remaining
):
    from moonmind.omnigent.reconciler import DurableSessionState
    from moonmind.schemas.omnigent_session_models import OmnigentSessionWorkflowInput

    owner = MoonMindOmnigentSessionWorkflow()
    owner._initialize(
        OmnigentSessionWorkflowInput(
            sessionId="session",
            compiledExecutionIntentRef="art_intent",
            compiledExecutionIntentDigest="sha256:" + "a" * 64,
            workflowId="workflow",
            stepExecutionId="step",
            agentRunId="agent",
            initialTurnAttemptId="turn",
            executionBudget=context(),
        )
    )
    calls = []
    now = START + timedelta(seconds=elapsed)
    observation_deadline = now + timedelta(seconds=60)

    async def execute(name, payload, **kwargs):
        nonlocal now
        calls.append((name, payload, kwargs))
        if name == "omnigent.observe_snapshot" and progress_source == "failed_snapshot":
            now += timedelta(seconds=5)
            raise TimeoutError("snapshot unavailable")
        return {
            "progressObserved": name == {
                "snapshot": "omnigent.observe_snapshot",
                "batch": "omnigent.read_event_batch",
            }.get(progress_source),
            "readStatus": "available",
        }

    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            side_effect=lambda: now,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.execute_activity",
            side_effect=execute,
        ),
    ):
        if initial_load:
            await owner._execute_activity("omnigent.load_reconciliation_inputs", {})
        durable = DurableSessionState(
            sessionId="session",
            revision=1,
            ownerToken="owner",
            fencingGeneration=1,
        )
        if progress_source == "failed_snapshot":
            with pytest.raises(TimeoutError, match="snapshot unavailable"):
                await owner._observe_after_wait(durable)
        else:
            assert await owner._observe_after_wait(durable)
    assert owner._execution_budget.remaining_seconds(now) == expected_remaining
    if expected_remaining:
        assert owner._execution_budget.finishing_deadline is None
    else:
        assert owner._execution_budget.finishing_deadline == observation_deadline
    assert all(
        now + kwargs["schedule_to_close_timeout"] <= now + timedelta(seconds=60)
        for _, _, kwargs in calls
    )


@pytest.mark.asyncio
async def test_agent_run_dispatch_cannot_extend_an_override():
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    owner = MoonMindAgentRun()
    owner._execution_budget = context()
    execute = AsyncMock(return_value={})
    route_kwargs = {
        "start_to_close_timeout": timedelta(seconds=300),
        "schedule_to_close_timeout": timedelta(seconds=600),
    }
    with (
        patch.object(owner, "_execute_kwargs_for_route", return_value=route_kwargs),
        patch(
            "moonmind.workflows.temporal.workflows.agent_run.workflow.now",
            return_value=START + timedelta(seconds=9),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.agent_run.execute_typed_activity",
            execute,
        ),
    ):
        await owner._execute_routed_activity(
            "agent_runtime.fetch_result",
            {},
            schedule_to_close_timeout=timedelta(seconds=1000),
        )
    assert execute.await_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=1
    )


def test_compact_intent_handoff_carries_admitted_budget():
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    request = models.AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow",
        idempotencyKey="key",
        executionBudget=context(),
    )
    payload = MoonMindAgentRun._omnigent_resolve_intent_payload(
        request,
        workflow_id="workflow",
        step_execution_id="step",
        agent_run_id="agent-run",
        admitted_feature_generation="omnigent-session-v1",
        compact_plan_authority=False,
        compact_workspace_checkpoint_authority=False,
    )
    assert payload["executionBudget"] == request.execution_budget.model_dump(
        mode="json", by_alias=True
    )


def test_session_continue_as_new_keeps_finishing_deadline_and_attempt_identity():
    from moonmind.schemas.omnigent_session_models import OmnigentSessionWorkflowInput

    first = context().begin_finishing(START + timedelta(seconds=10))
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(
        OmnigentSessionWorkflowInput(
            sessionId="session",
            compiledExecutionIntentRef="art_intent",
            compiledExecutionIntentDigest="sha256:" + "a" * 64,
            workflowId="workflow",
            stepExecutionId="step",
            agentRunId="agent-run",
            initialTurnAttemptId="turn",
            executionBudget=first,
        )
    )
    restored = MoonMindOmnigentSessionWorkflow()
    restored._initialize(supervisor._build_continue_as_new_input())
    assert restored._execution_budget == first


@pytest.mark.asyncio
async def test_terminal_report_timeout_keeps_confirmed_result():
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    owner = MoonMindAgentRun()
    owner._execution_budget = context().begin_finishing(START + timedelta(seconds=10))
    now = [owner._execution_budget.finishing_deadline - timedelta(seconds=1)]
    original = models.AgentRunResult(
        summary="confirmed publication", publicationState="published"
    )
    request = models.AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        correlationId="workflow",
        idempotencyKey="key",
    )

    async def fail(*_args, **_kwargs):
        now[0] += timedelta(seconds=1)
        raise models.ExecutionBudgetExhausted("terminal reporting deadline")

    with (
        patch.object(
            owner,
            "_enrich_result_metadata",
            side_effect=lambda **kwargs: kwargs["result"],
        ),
        patch.object(owner, "_execute_routed_activity", side_effect=fail),
        patch(
            "moonmind.workflows.temporal.workflows.agent_run.workflow.now",
            side_effect=lambda: now[0],
        ),
        patch(
            "moonmind.workflows.temporal.workflows.agent_run.workflow.patched",
            return_value=True,
        ),
    ):
        result = await owner._publish_terminal_result(request=request, result=original)
    assert result.summary == original.summary
    assert result.publication_state == "published"
    assert result.metadata["terminalSaveIncomplete"] is True
