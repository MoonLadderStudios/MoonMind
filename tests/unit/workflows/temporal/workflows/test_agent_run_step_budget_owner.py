"""One execution budget per step attempt, and one owner for recovery decisions.

MoonLadderStudios/MoonMind#4986. Cooldown re-entry, pause and automatic
successors resume the attempt's execution clock with the time it already
consumed; every successor or cooldown re-entry is admitted by
``decide_attempt_recovery`` and recorded once on the result. Auth rejection,
permission denial and invalid choices are never re-attempted.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_STEP_BUDGET_OWNER_PATCH_ID,
    MANAGED_PROCESS_LOST_DURING_RECONCILIATION,
    MoonMindAgentRun,
    RunStatus,
    budget_bounded_activity_timeouts,
    decide_attempt_recovery,
)
from tests.integration.services.temporal.workflows import test_agent_run as harness

_START = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)
_BUDGET_SECONDS = 300
_FIRST_SEGMENT_SECONDS = 200
_COOLDOWN_WAIT_SECONDS = 1000


def _result(**fields) -> AgentRunResult:
    return AgentRunResult(summary="attempt result", **fields)


_RATE_LIMITED = {
    "summary": "Provider rate limit reached; retry after cooldown.",
    "failureClass": "integration_error",
    "providerErrorCode": "429",
    "retryRecommendation": "retry_after_cooldown",
    "metadata": {
        "providerFailure": {
            "providerErrorClass": "rate_limit",
            "providerErrorCode": "429",
            "retryRecommendation": "retry_after_cooldown",
            "sanitizedSummary": "Provider rate limit reached.",
        },
    },
}
_PROCESS_LOST = {
    "summary": "Managed worker restarted while the process was running.",
    "failureClass": "system_error",
    "providerErrorCode": MANAGED_PROCESS_LOST_DURING_RECONCILIATION,
}


# ---------------------------------------------------------------------------
# The recovery owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "non_retryable",
    [
        {"failureClass": "integration_error", "providerErrorCode": "401"},
        {"failureClass": "integration_error", "providerErrorCode": "403"},
        {
            "failureClass": "integration_error",
            "retryRecommendation": "expand_credential_scope",
        },
        {"failureClass": "user_error"},
    ],
    ids=["auth-rejection", "permission-denied", "credential-scope", "invalid-choice"],
)
@pytest.mark.parametrize("proposal", ["successor", "cooldown"])
def test_non_retryable_failures_are_never_reattempted(non_retryable, proposal):
    result = _result(**non_retryable)
    assert result.retry_disposition == "no_retry"

    decision = decide_attempt_recovery(
        proposed_action=proposal,
        proposed_reason="proposed",
        retry_disposition=result.retry_disposition,
        execution_state=result.execution_state,
        remaining_budget_seconds=600,
    )

    assert decision.action == "terminal_stop"
    assert decision.reason == "non_retryable_failure"


def test_uncertain_effect_is_reconciled_before_any_retry():
    decision = decide_attempt_recovery(
        proposed_action="successor",
        proposed_reason="profile_bound_single_retry",
        retry_disposition="retry",
        execution_state="delivery_unknown",
        remaining_budget_seconds=600,
    )

    assert decision.action == "terminal_stop"
    assert decision.reason == "uncertain_effect_requires_reconciliation"


@pytest.mark.parametrize("remaining", [0.0, -5.0])
@pytest.mark.parametrize("proposal", ["successor", "cooldown"])
def test_spent_budget_admits_no_new_task_work(proposal, remaining):
    decision = decide_attempt_recovery(
        proposed_action=proposal,
        proposed_reason="proposed",
        retry_disposition="wait",
        execution_state="failed",
        remaining_budget_seconds=remaining,
    )

    assert decision.action == "terminal_stop"
    assert decision.reason == "execution_budget_exhausted"


@pytest.mark.parametrize("proposal", ["successor", "cooldown"])
def test_recoverable_failure_with_budget_is_admitted(proposal):
    decision = decide_attempt_recovery(
        proposed_action=proposal,
        proposed_reason="proposed",
        retry_disposition="wait",
        execution_state="failed",
        remaining_budget_seconds=1,
    )

    assert (decision.action, decision.reason) == (proposal, "proposed")


def test_no_proposal_is_a_terminal_stop_with_its_reason():
    decision = decide_attempt_recovery(
        proposed_action=None,
        proposed_reason="provider_cooldown_retry_exhausted",
        retry_disposition="wait",
        execution_state="failed",
        remaining_budget_seconds=600,
    )

    assert (decision.action, decision.reason) == (
        "terminal_stop",
        "provider_cooldown_retry_exhausted",
    )


# ---------------------------------------------------------------------------
# One derivation of attempt Activity timeouts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "activity_type",
    ["agent_runtime.status", "agent_runtime.fetch_result", "agent_runtime.cancel"],
)
def test_attempt_activity_timeouts_never_exceed_the_remaining_budget(activity_type):
    route = agent_run_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_type)

    bounded = budget_bounded_activity_timeouts(
        activity_type, remaining_budget_seconds=12.5
    )
    roomy = budget_bounded_activity_timeouts(
        activity_type, remaining_budget_seconds=10**6
    )
    spent = budget_bounded_activity_timeouts(activity_type, remaining_budget_seconds=-3)

    assert bounded == {
        "start_to_close_timeout": timedelta(seconds=12.5),
        "schedule_to_close_timeout": timedelta(seconds=12.5),
    }
    assert roomy == {
        "start_to_close_timeout": timedelta(
            seconds=route.timeouts.start_to_close_seconds
        ),
        "schedule_to_close_timeout": timedelta(
            seconds=route.timeouts.schedule_to_close_seconds
        ),
    }
    assert spent["schedule_to_close_timeout"] == timedelta(seconds=0.001)
    assert (
        budget_bounded_activity_timeouts(activity_type, remaining_budget_seconds=None)
        == {}
    )


def test_status_override_uses_the_shared_derivation(monkeypatch):
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _id: True)

    assert (
        MoonMindAgentRun._managed_status_schedule_to_close_override(
            remaining_budget_seconds=45
        )
        == budget_bounded_activity_timeouts(
            "agent_runtime.status", remaining_budget_seconds=45
        )["schedule_to_close_timeout"]
    )


@pytest.mark.parametrize("method", ["_fetch_managed_result", "_poll_managed_status"])
def test_live_attempt_reads_require_an_explicit_allowance(method):
    parameter = inspect.signature(getattr(MoonMindAgentRun, method)).parameters[
        "remaining_budget_seconds"
    ]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Execution clock accounting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("owner_enabled,expected", [(True, 120), (False, 0)])
def test_resumed_clock_keeps_the_consumed_allowance(
    monkeypatch, owner_enabled, expected
):
    """Pause, slot and cooldown waits are excluded without resetting the budget."""

    run = MoonMindAgentRun()
    run._step_budget_owner_enabled = owner_enabled
    run._consumed_execution_seconds = 120.0
    resumed_at = _START + timedelta(hours=2)

    start = run._resume_execution_clock(resumed_at)

    assert (resumed_at - start).total_seconds() == expected


# ---------------------------------------------------------------------------
# Workflow boundary journeys (deterministic clock)
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self._now = _START

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class _Manager:
    def __init__(self) -> None:
        self.signals: list[tuple[str, object]] = []

    async def signal(self, name, payload=None):
        self.signals.append((name, payload))


class _Journey:
    """Drive ``MoonMindAgentRun.run`` through scripted runtime results."""

    def __init__(self, monkeypatch, *, owner_enabled=True) -> None:
        self.clock = _Clock()
        self.manager = _Manager()
        self.run = MoonMindAgentRun()
        self.launches: list[datetime] = []
        self.fetch_results: list[dict] = []
        self.fetch_advance_seconds = 0.0
        self.status_polls: list[dict] = []
        self.publish_kwargs: list[dict] = []
        self.slot_requests = 0
        self.first_segment_seconds = _FIRST_SEGMENT_SECONDS

        info = type(
            "WorkflowInfo",
            (),
            {
                "task_queue": "mm.workflow.user.v2",
                "namespace": "default",
                "workflow_id": "wf-agent-run-4986",
                "run_id": "run-4986",
                "search_attributes": {},
                "parent": None,
            },
        )
        logger = type(
            "Logger",
            (),
            {
                "info": lambda *a, **k: None,
                "warning": lambda *a, **k: None,
                "error": lambda *a, **k: None,
            },
        )
        workflow = agent_run_module.workflow
        monkeypatch.setattr(workflow, "info", info)
        monkeypatch.setattr(workflow, "logger", logger)
        monkeypatch.setattr(
            workflow,
            "patched",
            lambda patch_id: owner_enabled
            or patch_id != AGENT_RUN_STEP_BUDGET_OWNER_PATCH_ID,
        )
        monkeypatch.setattr(workflow, "now", self.clock.now)

        async def wait_condition(condition, timeout=None):
            if condition():
                return True
            if timeout is None:
                raise AssertionError("unbounded wait in a scripted journey")
            self.clock.advance(timeout.total_seconds())
            raise asyncio.TimeoutError()

        monkeypatch.setattr(workflow, "wait_condition", wait_condition)

        journey = self

        async def ensure_manager_and_signal(*_args, **_kwargs):
            journey.slot_requests += 1
            if journey.slot_requests > 1:
                # Cooldown or capacity queueing is not execution.
                journey.clock.advance(_COOLDOWN_WAIT_SECONDS)
            journey.run.slot_assigned_event.set()
            return journey.manager

        async def ensure_manager_started(*_args, **_kwargs):
            return journey.manager

        async def sync_profiles(*_args, **_kwargs):
            return 1

        class _Adapter:
            def __init__(self, **_kwargs) -> None:
                pass

            async def start(self, _request):
                journey.launches.append(journey.clock.now())
                return AgentRunHandle(
                    runId=f"managed-run-{len(journey.launches)}",
                    agentKind="managed",
                    agentId="claude_code",
                    status=RunStatus.running,
                    startedAt=journey.clock.now(),
                    pollHintSeconds=10,
                )

        async def execute_routed_activity(activity_name, payload, **kwargs):
            if activity_name == "agent_runtime.status":
                journey.status_polls.append(dict(kwargs))
                segment = (journey.clock.now() - journey.launches[-1]).total_seconds()
                attempt = len(journey.launches)
                finished = attempt <= len(journey.fetch_results) and (
                    segment >= journey.first_segment_seconds
                )
                return {
                    "runId": f"managed-run-{attempt}",
                    "agentKind": "managed",
                    "agentId": "claude_code",
                    "status": "failed" if finished else "running",
                    # Fresh progress each poll keeps the no-progress watchdog
                    # out of the budget journey.
                    "metadata": {"lastLogOffset": str(len(journey.status_polls))},
                }
            if activity_name == "agent_runtime.fetch_result":
                journey.clock.advance(journey.fetch_advance_seconds)
                return journey.fetch_results[len(journey.launches) - 1]
            if activity_name == "agent_runtime.publish_artifacts":
                journey.publish_kwargs.append(dict(kwargs))
                return payload
            return {}

        monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _Adapter)
        monkeypatch.setattr(
            self.run, "_ensure_manager_and_signal", ensure_manager_and_signal
        )
        monkeypatch.setattr(self.run, "_ensure_manager_started", ensure_manager_started)
        monkeypatch.setattr(self.run, "_sync_manager_profiles", sync_profiles)
        monkeypatch.setattr(self.run, "_uses_codex_session_adapter", lambda _r: False)
        monkeypatch.setattr(
            self.run, "_execute_routed_activity", execute_routed_activity
        )

    async def execute(self, timeout_policy: dict | None = None) -> AgentRunResult:
        return await self.run.run(
            AgentExecutionRequest(
                agentKind="managed",
                agentId="claude_code",
                executionProfileRef="default-managed",
                correlationId="corr-4986",
                idempotencyKey="idem-4986",
                instructionRef="Do the thing.",
                parameters={"publishMode": "none"},
                timeoutPolicy=timeout_policy
                or {
                    "timeout_seconds": _BUDGET_SECONDS,
                    "max_timeout_seconds": _BUDGET_SECONDS,
                    "progress_stall_seconds": _BUDGET_SECONDS,
                },
            )
        )

    def second_segment_seconds(self) -> float:
        return (self.clock.now() - self.launches[-1]).total_seconds()


@pytest.mark.asyncio
async def test_cooldown_reentry_resumes_with_only_the_remaining_allowance(
    monkeypatch,
):
    """Quota recovery cannot buy a fresh execution budget."""

    journey = _Journey(monkeypatch)
    journey.fetch_results = [_RATE_LIMITED]

    result = await journey.execute()

    assert len(journey.launches) == 2
    assert result.metadata["reason"] == "timed_out"
    # The second segment ran only what the first left unused, although the
    # cooldown wait itself was not charged.
    remaining = _BUDGET_SECONDS - _FIRST_SEGMENT_SECONDS
    assert journey.second_segment_seconds() <= remaining
    assert (journey.launches[1] - journey.launches[0]).total_seconds() >= (
        _COOLDOWN_WAIT_SECONDS
    )
    assert result.metadata["consumedExecutionSeconds"] == _BUDGET_SECONDS
    assert [
        (item["action"], item["reason"])
        for item in result.metadata["recoveryDecisions"]
    ] == [("cooldown", "provider_cooldown")]
    # Status reads in the resumed segment are bounded by the same remainder.
    bounded = [
        poll["schedule_to_close_timeout"].total_seconds()
        for poll in journey.status_polls
        if "schedule_to_close_timeout" in poll
    ]
    assert bounded and max(bounded[-3:]) <= remaining


@pytest.mark.asyncio
async def test_progress_extension_survives_a_cooldown_wait(monkeypatch):
    """A cooldown wait is neither execution nor lost progress.

    The first segment progressed past its base window; the resumed segment
    keeps that extension up to the shared ceiling instead of expiring because
    the wait aged its last progress observation.
    """

    journey = _Journey(monkeypatch)
    journey.fetch_results = [_RATE_LIMITED]

    result = await journey.execute(
        {
            "timeout_seconds": 100,
            "max_timeout_seconds": 1000,
            "progress_stall_seconds": 50,
        }
    )

    assert len(journey.launches) == 2
    assert result.metadata["budgetVerdict"] == "expired_max_budget"
    assert journey.second_segment_seconds() == 1000 - _FIRST_SEGMENT_SECONDS
    assert result.metadata["consumedExecutionSeconds"] == 1000


@pytest.mark.asyncio
async def test_retained_histories_keep_their_recorded_cooldown_reset(monkeypatch):
    journey = _Journey(monkeypatch, owner_enabled=False)
    journey.fetch_results = [_RATE_LIMITED]

    result = await journey.execute()

    assert len(journey.launches) == 2
    assert journey.second_segment_seconds() >= _BUDGET_SECONDS
    assert "recoveryDecisions" not in result.metadata
    assert "consumedExecutionSeconds" not in result.metadata


@pytest.mark.asyncio
async def test_automatic_successor_shares_the_attempt_budget(monkeypatch):
    journey = _Journey(monkeypatch)
    journey.fetch_results = [_PROCESS_LOST]

    result = await journey.execute()

    assert len(journey.launches) == 2
    assert journey.second_segment_seconds() <= (
        _BUDGET_SECONDS - _FIRST_SEGMENT_SECONDS
    )
    assert result.metadata["consumedExecutionSeconds"] == _BUDGET_SECONDS
    assert [
        (item["action"], item["reason"])
        for item in result.metadata["recoveryDecisions"]
    ] == [("successor", "managed_process_loss")]


@pytest.mark.asyncio
async def test_non_retryable_process_loss_gets_one_terminal_decision(monkeypatch):
    journey = _Journey(monkeypatch)
    journey.fetch_results = [{**_PROCESS_LOST, "retryRecommendation": "reauthenticate"}]

    result = await journey.execute()

    assert len(journey.launches) == 1
    assert result.provider_error_code == MANAGED_PROCESS_LOST_DURING_RECONCILIATION
    assert [
        (item["action"], item["reason"])
        for item in result.metadata["recoveryDecisions"]
    ] == [("terminal_stop", "non_retryable_failure")]


@pytest.mark.asyncio
async def test_deadline_crossed_during_result_fetch_admits_no_successor(
    monkeypatch,
):
    """The successor is refused, and terminal saving uses its own allowance."""

    journey = _Journey(monkeypatch)
    journey.fetch_results = [_PROCESS_LOST]
    journey.fetch_advance_seconds = _BUDGET_SECONDS

    result = await journey.execute()

    assert len(journey.launches) == 1
    assert result.metadata["reason"] == "timed_out"
    assert result.metadata["unrecoveredFailureId"] == (
        MANAGED_PROCESS_LOST_DURING_RECONCILIATION
    )
    assert [
        (item["action"], item["reason"])
        for item in result.metadata["recoveryDecisions"]
    ] == [("terminal_stop", "execution_budget_exhausted")]
    # Saving after expiry runs under the catalog's bounded save allowance; it
    # neither extends the execution clock nor receives a derived budget.
    assert journey.publish_kwargs
    assert "schedule_to_close_timeout" not in journey.publish_kwargs[-1]


# ---------------------------------------------------------------------------
# Real registered Temporal workflow under a time-skipping clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_reentry_on_real_temporal_keeps_the_cumulative_budget(
    monkeypatch,
):
    launches: list[datetime] = []
    published: list[tuple[datetime, AgentRunResult]] = []
    budget = 120
    first_segment = 50

    def policy(request, **_options):
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 3600,
            "noProgressGraceSeconds": 0,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_step_budget_owner",
        }

    monkeypatch.setattr(
        agent_run_module.MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(policy),
    )

    @activity.defn(name="agent_runtime.launch")
    async def launch(request: dict) -> dict:
        launches.append(activity.info().current_attempt_scheduled_time)
        return {"container_id": "c", "status": "running", "agent_id": "claude_code"}

    @activity.defn(name="agent_runtime.status")
    async def status(request: dict) -> dict:
        segment = (
            activity.info().current_attempt_scheduled_time - launches[-1]
        ).total_seconds()
        finished = len(launches) == 1 and segment >= first_segment
        return {
            "runId": request.get("runId") or "managed-run",
            "agentKind": "managed",
            "agentId": "claude_code",
            "status": "failed" if finished else "running",
            "metadata": {"lastLogOffset": str(segment)},
        }

    @activity.defn(name="agent_runtime.fetch_result")
    async def fetch_result(_request: dict) -> dict:
        return dict(_RATE_LIMITED)

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def publish(result: AgentRunResult) -> AgentRunResult:
        published.append((activity.info().current_attempt_scheduled_time, result))
        return result

    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="step-budget-owner-workflow",
            workflows=[
                agent_run_module.MoonMindAgentRun,
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
                launch,
                status,
                fetch_result,
                publish,
            ],
        ),
        Worker(
            env.client,
            task_queue=agent_run_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "agent_runtime.cancel"
            ).task_queue,
            activities=[harness.mock_cancel],
        ),
    ):
        await env.client.start_workflow(
            harness.MockProviderProfileManager.run,
            {"runtime_id": "claude_code"},
            id="provider-profile-manager:claude_code",
            task_queue="step-budget-owner-workflow",
        )
        handle = await env.client.start_workflow(
            agent_run_module.MoonMindAgentRun.run,
            AgentExecutionRequest(
                agentKind="managed",
                agentId="claude_code",
                executionProfileRef="default-managed",
                correlationId="corr-4986",
                idempotencyKey="idem-4986",
                timeoutPolicy={
                    "timeout_seconds": budget,
                    "max_timeout_seconds": budget,
                    "progress_stall_seconds": budget,
                },
            ),
            id="test-step-budget-owner-cooldown",
            task_queue="step-budget-owner-workflow",
        )
        result = await asyncio.wait_for(handle.result(), timeout=60)
        history = await handle.fetch_history()

    # A replacement worker replays the same cumulative budget and decisions.
    await Replayer(
        workflows=[agent_run_module.MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)

    assert len(launches) == 2
    assert result.metadata["reason"] == "timed_out"
    assert [
        (item["action"], item["reason"])
        for item in result.metadata["recoveryDecisions"]
    ] == [("cooldown", "provider_cooldown")]
    # The resumed segment ends at the cumulative deadline: it is shorter than
    # the full budget by what the first segment consumed (poll slack aside).
    published_at = published[-1][0]
    second_segment = (published_at - launches[1]).total_seconds()
    assert second_segment < budget - first_segment + 15
    assert result.metadata["consumedExecutionSeconds"] <= budget + 15
