"""AgentRun's progress-aware execution-budget decisions.

Regression cover for MoonLadderStudios/MoonMind#3771. The AgentRun workflow
terminated a managed run at its flat one-hour budget while the runtime was
actively working — it had written files three seconds before the kill — and
reported the outcome as "no observable progress". An hour of uncommitted work
was discarded.

These exercise the decision methods the poll loop actually calls, in both patch
states, so the in-flight (pre-patch) path is covered alongside the new one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    ExecutionBudget,
    evaluate_execution_budget,
    resolve_execution_budget,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    RunStatus,
)

_BUDGET = ExecutionBudget(
    base_seconds=3600,
    max_seconds=21600,
    progress_stall_seconds=900,
)

_START = datetime(2026, 8, 25, 7, 24, tzinfo=UTC)
_BASE_SECONDS = 300


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


# ---------------------------------------------------------------------------
# Progress observation
# ---------------------------------------------------------------------------


def test_idle_seconds_is_none_before_any_progress_is_observed():
    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=True,
            last_progress_at=None,
            now=_START + timedelta(hours=1),
        )
        is None
    )


def test_idle_seconds_measures_from_the_last_observation():
    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=True,
            last_progress_at=_START + timedelta(minutes=59),
            now=_START + timedelta(hours=1),
        )
        == 60.0
    )


def test_idle_seconds_is_none_when_the_patch_is_off():
    """An in-flight run records no progress evidence, so it cannot be extended."""

    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=False,
            last_progress_at=_START + timedelta(minutes=59),
            now=_START + timedelta(hours=1),
        )
        is None
    )


# ---------------------------------------------------------------------------
# The #3771 journey
# ---------------------------------------------------------------------------


def test_working_run_is_not_terminated_at_the_base_budget():
    """The exact failure: base budget reached, progress three seconds ago."""

    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=3.0,
    )

    assert verdict == "continue"


def test_working_run_keeps_a_positive_poll_budget_past_the_base_window():
    """The deadline must roll forward too, or the poll loop breaks out early
    with a non-positive remaining budget even though the run may continue."""

    deadline = MoonMindAgentRun._budget_deadline_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=3.0,
    )

    assert deadline - 3600.0 > 0


def test_working_run_finally_stops_at_the_ceiling_and_says_so():
    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=float(_BUDGET.max_seconds),
        idle_progress_seconds=1.0,
    )
    detail = MoonMindAgentRun._budget_expiry_detail_for(
        budget=_BUDGET,
        progress_aware=True,
        verdict=verdict,
    )

    assert verdict == "expired_max_budget"
    # It must not accuse a run that was demonstrably working of doing nothing.
    assert "no observable progress" not in detail
    assert "maximum execution budget" in detail


def test_quiet_run_is_still_terminated_at_the_base_budget():
    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=float(_BUDGET.progress_stall_seconds),
    )
    detail = MoonMindAgentRun._budget_expiry_detail_for(
        budget=_BUDGET,
        progress_aware=True,
        verdict=verdict,
    )

    assert verdict == "expired_no_progress"
    assert "no observable progress" in detail


def test_run_with_no_evidence_at_all_is_terminated_at_the_base_budget():
    assert (
        MoonMindAgentRun._budget_verdict_for(
            budget=_BUDGET,
            progress_aware=True,
            elapsed_seconds=3600.0,
            idle_progress_seconds=None,
        )
        == "expired_no_progress"
    )


# ---------------------------------------------------------------------------
# In-flight safety: a run replaying from before the patch is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("elapsed", "idle", "expected"),
    [
        (3599.0, 0.0, "continue"),
        (3600.0, 0.0, "expired_no_progress"),
        (3600.0, None, "expired_no_progress"),
        (10000.0, 0.0, "expired_no_progress"),
    ],
)
def test_unpatched_runs_keep_the_flat_deadline(elapsed, idle, expected):
    """Fresh progress must not extend a run that recorded the flat deadline."""

    assert (
        MoonMindAgentRun._budget_verdict_for(
            budget=_BUDGET,
            progress_aware=False,
            elapsed_seconds=elapsed,
            idle_progress_seconds=idle,
        )
        == expected
    )
    assert (
        MoonMindAgentRun._budget_deadline_for(
            budget=_BUDGET,
            progress_aware=False,
            elapsed_seconds=elapsed,
            idle_progress_seconds=idle,
        )
        == float(_BUDGET.base_seconds)
    )


def test_unpatched_expiry_detail_matches_the_previous_wording():
    """In-flight runs keep the message their operators are already reading."""

    assert (
        MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET,
            progress_aware=False,
            verdict="expired_no_progress",
        )
        == "made no observable progress and exceeded its execution budget"
    )


# ---------------------------------------------------------------------------
# Terminal result carries the evidence the decision was made on
# ---------------------------------------------------------------------------


def test_ceiling_timeout_result_records_the_budget_and_verdict():
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=float(_BUDGET.max_seconds),
        elapsed_seconds=float(_BUDGET.max_seconds),
        detail=MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET, progress_aware=True, verdict="expired_max_budget"
        ),
        budget=_BUDGET,
        verdict="expired_max_budget",
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["budgetVerdict"] == "expired_max_budget"
    assert result.metadata["budgetExtendedForProgress"] is True
    assert result.metadata["executionBudget"] == {
        "baseSeconds": 3600,
        "maxSeconds": 21600,
        "progressStallSeconds": 900,
    }
    assert "no observable progress" not in result.summary


def test_stalled_timeout_result_is_not_labelled_as_extended():
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=float(_BUDGET.base_seconds),
        elapsed_seconds=float(_BUDGET.base_seconds),
        detail=MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET, progress_aware=True, verdict="expired_no_progress"
        ),
        budget=_BUDGET,
        verdict="expired_no_progress",
    )

    assert result.metadata["budgetVerdict"] == "expired_no_progress"
    assert result.metadata["budgetExtendedForProgress"] is False
    assert "no observable progress" in result.summary


def test_unpatched_timeout_result_omits_the_budget_metadata():
    """In-flight runs keep the metadata shape their history already carries."""

    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=3600,
        elapsed_seconds=3600,
        detail="made no observable progress and exceeded its execution budget",
    )

    assert "executionBudget" not in result.metadata
    assert "budgetVerdict" not in result.metadata
    assert "budgetExtendedForProgress" not in result.metadata


# ---------------------------------------------------------------------------
# The supervisor must receive the same budget the workflow enforces
# ---------------------------------------------------------------------------


def test_published_policy_carries_the_whole_budget():
    request = _request()

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=True
    )

    # What the launcher serializes is what the supervisor resolves.
    launch_payload = request.model_dump(mode="json", by_alias=True)
    published = launch_payload["timeoutPolicy"]
    assert published["timeout_seconds"] == _BUDGET.base_seconds
    assert published["max_timeout_seconds"] == _BUDGET.max_seconds
    assert published["progress_stall_seconds"] == _BUDGET.progress_stall_seconds
    assert (
        resolve_execution_budget(agent_kind="managed", timeout_policy=published)
        == _BUDGET
    )


def test_published_policy_preserves_unrelated_timeout_policy_keys():
    request = _request()
    request.timeout_policy = {"custom_key": "kept"}

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=True
    )

    assert request.timeout_policy["custom_key"] == "kept"


def test_unpatched_publish_pins_the_supervisor_to_the_flat_deadline():
    """A pre-patch history publishes a budget that cannot outlive its workflow.

    The launch activity re-resolves the budget from this payload on every launch
    and retry, including retries dispatched after the progress-aware deployment.
    Publishing only the base window let it derive a progress-aware ceiling the
    replayed workflow never granted: the workflow timed out at the base window
    and released the provider slot while the supervisor carried the process on to
    that ceiling, leaving untracked work running against a reassigned profile.
    """

    request = _request()

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=False
    )

    assert request.timeout_policy == {
        "timeout_seconds": _BUDGET.base_seconds,
        "max_timeout_seconds": _BUDGET.base_seconds,
        "progress_stall_seconds": _BUDGET.base_seconds,
        "execution_budget_mode": "flat",
    }
    # The activity resolving this payload reaches the same flat deadline.
    supervisor_budget = resolve_execution_budget(
        agent_kind="managed", timeout_policy=request.timeout_policy
    )
    assert supervisor_budget.max_seconds == _BUDGET.base_seconds
    assert supervisor_budget.mode == "flat"
    assert (
        evaluate_execution_budget(
            budget=supervisor_budget,
            elapsed_seconds=float(_BUDGET.base_seconds),
            # Even continuous progress cannot extend a flat budget.
            idle_progress_seconds=0.0,
        )
        != "continue"
    )


# ---------------------------------------------------------------------------
# Workflow boundary: the deadline a lane can actually enforce
# ---------------------------------------------------------------------------


def _configure_workflow_runtime(monkeypatch, *, clock) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-1",
            "run_id": "run-1",
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
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _id: True)
    monkeypatch.setattr(agent_run_module.workflow, "now", clock.now)


class _Clock:
    """Deterministic workflow clock advanced only by the bounded waits."""

    def __init__(self) -> None:
        self._now = _START

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_streaming_lane_is_bounded_by_the_deadline_it_can_enforce(
    monkeypatch,
):
    """A lane that cannot observe progress must not be sized to the ceiling.

    The workflow is blocked awaiting this activity for its whole duration, so it
    cannot re-evaluate the budget, and the activity heartbeat is emitted
    independently of semantic progress. Sizing ScheduleToClose to the
    progress-aware ceiling would let a run that never makes progress hold its
    provider slot for the full ceiling with nothing able to stop it.
    """

    clock = _Clock()
    _configure_workflow_runtime(monkeypatch, clock=clock)
    run = MoonMindAgentRun()
    execute_kwargs: dict[str, object] = {}

    async def fake_wait_condition(_condition, timeout=None):
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(activity_name, payload, **kwargs):
        if activity_name == "integration.resolve_adapter_metadata":
            return {
                "agent_id": "openclaw",
                "execution_style": "streaming_gateway",
            }
        if activity_name == "integration.openclaw.execute":
            execute_kwargs.update(kwargs)
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module.workflow, "wait_condition", fake_wait_condition
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="openclaw",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="Do the thing.",
        parameters={"publishMode": "none"},
        timeoutPolicy={"timeout_seconds": 300},
    )
    budget = resolve_execution_budget(
        agent_kind="external", timeout_policy=request.timeout_policy
    )
    assert budget.max_seconds > budget.base_seconds  # the ceiling is real

    await run.run(request)

    assert execute_kwargs, "the streaming lane activity was never dispatched"
    assert execute_kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=budget.base_seconds
    )
    assert execute_kwargs["start_to_close_timeout"] == timedelta(
        seconds=budget.base_seconds
    )


def _status_payload(status: str, log_offset: str) -> dict:
    return {
        "runId": "managed-run-1",
        "agentKind": "managed",
        "agentId": "claude_code",
        "status": status,
        "metadata": {"lastLogOffset": log_offset},
    }


class _StubManagerHandle:
    """Stand-in for the provider-profile manager's external workflow handle."""

    def __init__(self) -> None:
        self.signals: list[tuple[str, object]] = []

    async def signal(self, name, payload=None):
        self.signals.append((name, payload))


@pytest.mark.asyncio
async def test_final_status_reconciliation_before_the_budget_verdict(monkeypatch):
    """A budget verdict must not be accepted on evidence a poll would refresh.

    The bounded wait can consume the entire known remaining budget. The status
    poll was then skipped and the run declared timed out, so a supervisor
    ``lastLogOffset`` advance that landed during that wait — progress that
    extends the budget — was never read and the provider slot was released while
    the process was working.
    """

    clock = _Clock()
    _configure_workflow_runtime(monkeypatch, clock=clock)
    run = MoonMindAgentRun()
    manager = _StubManagerHandle()
    status_polls: list[float] = []
    reconciliation_polls: list[float] = []

    async def fake_wait_condition(condition, timeout=None):
        if condition():
            return True
        if timeout is not None:
            clock.advance(timeout.total_seconds())
        raise asyncio.TimeoutError()

    async def _ensure_manager(*_args, **_kwargs):
        run.slot_assigned_event.set()
        return manager

    async def _sync_profiles(*_args, **_kwargs):
        return 1

    class _StubAdapter:
        def __init__(self, **_kwargs) -> None:
            pass

        async def start(self, _request):
            return AgentRunHandle(
                runId="managed-run-1",
                agentKind="managed",
                agentId="claude_code",
                status=RunStatus.running,
                startedAt=clock.now(),
                pollHintSeconds=10,
            )

    async def fake_execute_routed_activity(activity_name, payload, **kwargs):
        if activity_name == "agent_runtime.status":
            elapsed = (clock.now() - _START).total_seconds()
            status_polls.append(elapsed)
            # The final reconciliation is the one poll dispatched without a
            # remaining-budget clamp: the budget looks spent, so the poll is
            # given the activity's own configured window instead of zero.
            is_reconciliation = "schedule_to_close_timeout" not in kwargs
            if is_reconciliation:
                reconciliation_polls.append(elapsed)
                # The supervisor advance that landed during the last bounded
                # wait — the evidence the old guard never read.
                return _status_payload("running", "4096")
            if reconciliation_polls:
                return _status_payload("completed", "8192")
            # Quiet throughout the base window.
            return _status_payload("running", "0")
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        return {}

    monkeypatch.setattr(
        agent_run_module.workflow, "wait_condition", fake_wait_condition
    )
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _StubAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", _ensure_manager)
    monkeypatch.setattr(run, "_ensure_manager_started", _ensure_manager)
    monkeypatch.setattr(run, "_sync_manager_profiles", _sync_profiles)
    monkeypatch.setattr(run, "_uses_codex_session_adapter", lambda _r: False)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="default-managed",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="Do the thing.",
        parameters={"publishMode": "none"},
        timeoutPolicy={"timeout_seconds": _BASE_SECONDS},
    )

    result = await run.run(request)

    assert status_polls, "the run never polled managed status"
    # Exactly one reconciliation: the boundary is checked against fresh evidence
    # once, not polled in a loop after the budget is spent.
    assert len(reconciliation_polls) == 1
    assert reconciliation_polls[0] >= _BASE_SECONDS
    # The refreshed progress extended the budget, so the run was not timed out
    # and its provider slot was not released while the process was working.
    assert run.run_status != RunStatus.timed_out
    assert result.failure_class is None
