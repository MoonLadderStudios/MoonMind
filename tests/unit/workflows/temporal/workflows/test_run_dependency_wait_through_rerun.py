"""Tests for the unified wait-through-rerun dependency gate behavior.

Under DEPENDENCY_WAIT_THROUGH_RERUN_PATCH, a non-success terminal prerequisite
outcome (failed, canceled, terminated, timed_out, unresolvable) keeps the
dependent run blocked in waiting_on_dependencies until the same prerequisite
workflowId later reaches completed, the dependent is canceled, or an operator
bypasses the gate.

Tests in this file fall into two layers:

1. Direct method-level tests that monkey-patch workflow.patched/workflow.now
   and call _record_dependency_outcome to exercise the gate's state machine
   without spinning up a Temporal time-skipping environment. Fast.

2. One end-to-end test using WorkflowEnvironment.start_time_skipping() that
   verifies a failed prerequisite, observed via reconciliation, keeps the
   dependent waiting until a later DependencyResolved completed signal
   unblocks it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflows.run import (
    DEPENDENCY_GATE_PATCH,
    DEPENDENCY_RESOLUTION_BYPASSED,
    DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE,
    DEPENDENCY_RESOLUTION_NOT_APPLICABLE,
    DEPENDENCY_RESOLUTION_SATISFIED,
    DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN,
    DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN,
    DEPENDENCY_WAIT_THROUGH_RERUN_PATCH,
    MoonMindRunWorkflow,
    STATE_WAITING_ON_DEPENDENCIES,
)


# ---------------------------------------------------------------------------
# Direct method-level tests
# ---------------------------------------------------------------------------


def _make_workflow_under_rerun_patch(
    monkeypatch,
    *,
    declared: list[str],
    now: datetime | None = None,
) -> MoonMindRunWorkflow:
    """Build a workflow instance with rerun patch enabled and gate primed."""
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id
        in {DEPENDENCY_GATE_PATCH, DEPENDENCY_WAIT_THROUGH_RERUN_PATCH},
    )
    fixed_now = now or datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(workflow, "now", lambda: fixed_now)

    instance = MoonMindRunWorkflow()
    instance._declared_dependencies = list(declared)
    instance._unresolved_dependency_ids = set(declared)
    instance._dependency_resolution = DEPENDENCY_RESOLUTION_NOT_APPLICABLE
    return instance


def test_failed_prerequisite_keeps_dependent_waiting(monkeypatch) -> None:
    """A 'failed' prerequisite terminal observation does not fail the dependent."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="upstream broken",
    )

    assert wf._dependency_failure is None
    assert wf._failed_dependency_id is None
    assert "dep-1" in wf._unresolved_dependency_ids
    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN
    assert outcome["failureCount"] == 1
    assert outcome["lastFailedAt"] == "2026-05-08T12:00:00Z"
    assert outcome["terminalState"] == "failed"


@pytest.mark.parametrize(
    "terminal_state,close_status",
    [
        ("canceled", "canceled"),
        ("terminated", "terminated"),
        ("timed_out", "timed_out"),
    ],
)
def test_non_failed_terminals_keep_dependent_waiting(
    monkeypatch, terminal_state: str, close_status: str
) -> None:
    """canceled/terminated/timed_out prerequisites also keep the dependent waiting."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state=terminal_state,
        close_status=close_status,
        resolved_at="2026-05-08T12:00:00Z",
        failure_category=f"dependency_{terminal_state}",
        message=None,
    )

    assert wf._dependency_failure is None
    assert "dep-1" in wf._unresolved_dependency_ids
    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN
    assert outcome["terminalState"] == terminal_state
    assert outcome["closeStatus"] == close_status
    assert outcome["failureCount"] == 1


def test_unresolvable_prerequisite_keeps_dependent_waiting(monkeypatch) -> None:
    """An unresolvable prerequisite (missing-from-snapshot) keeps the dependent waiting."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_missing_dependency("dep-1")

    assert wf._dependency_failure is None
    assert "dep-1" in wf._unresolved_dependency_ids
    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN
    assert outcome["failureCategory"] == "dependency_unresolved"
    assert outcome["failureCount"] == 1


def test_prerequisite_completion_after_failure_unblocks_dependent(monkeypatch) -> None:
    """A completed observation after a failed observation transitions to satisfied_after_rerun."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="upstream broken",
    )

    assert "dep-1" in wf._unresolved_dependency_ids

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:15:00Z",
        failure_category=None,
        message="completed after rerun",
    )

    assert wf._dependency_failure is None
    assert "dep-1" not in wf._unresolved_dependency_ids
    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN
    assert outcome["terminalState"] == "completed"
    assert outcome["resolvedAt"] == "2026-05-08T12:15:00Z"
    assert outcome["failureCount"] == 1
    assert outcome["lastFailedAt"] == "2026-05-08T12:00:00Z"
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN


def test_first_try_completion_yields_satisfied_resolution(monkeypatch) -> None:
    """A clean completion with no prior failures yields per-dep satisfied (not after_rerun)."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category=None,
        message=None,
    )

    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_SATISFIED
    assert outcome["failureCount"] == 0
    assert outcome["lastFailedAt"] is None
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED


def test_stale_failed_signal_after_satisfied_is_ignored(monkeypatch) -> None:
    """A late failed signal arriving after a satisfied outcome must not revert resolution."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category=None,
        message=None,
    )
    assert "dep-1" not in wf._unresolved_dependency_ids
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:30:00Z",
        failure_category="dependency_failed",
        message="late stale failed signal",
    )

    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_SATISFIED
    assert outcome["terminalState"] == "completed"
    assert "dep-1" not in wf._unresolved_dependency_ids
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED
    assert wf._dependency_failure is None


def test_duplicate_failure_observation_does_not_increment_count(monkeypatch) -> None:
    """Reconciliation that re-observes the same failure cycle must not double-count."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="first observation",
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="duplicate observation",
    )

    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["failureCount"] == 1
    assert outcome["lastFailedAt"] == "2026-05-08T12:00:00Z"


def test_distinct_failure_cycles_increment_count(monkeypatch) -> None:
    """Two distinct non-success terminals (different resolved_at) increment failureCount."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message=None,
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:30:00Z",
        failure_category="dependency_failed",
        message=None,
    )

    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["failureCount"] == 2
    assert outcome["lastFailedAt"] == "2026-05-08T12:30:00Z"


def test_top_level_resolution_satisfied_when_all_prereqs_succeed_first_try(
    monkeypatch,
) -> None:
    """When every prerequisite succeeds without failures, top-level resolution is satisfied."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1", "dep-2"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category=None,
        message=None,
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-2",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:01:00Z",
        failure_category=None,
        message=None,
    )

    assert wf._unresolved_dependency_ids == set()
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED


def test_top_level_resolution_satisfied_after_rerun_when_any_prereq_failed(
    monkeypatch,
) -> None:
    """When any prerequisite failed before satisfaction, top-level rolls up to satisfied_after_rerun."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1", "dep-2"])

    # dep-1 succeeds first try
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category=None,
        message=None,
    )
    # dep-2 fails then succeeds
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-2",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message=None,
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-2",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T12:30:00Z",
        failure_category=None,
        message=None,
    )

    assert wf._unresolved_dependency_ids == set()
    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN


def test_dependency_metadata_includes_per_outcome_diagnostic_fields(monkeypatch) -> None:
    """run_summary / memo dependency block surfaces failureCount and lastFailedAt."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="upstream broken",
    )

    metadata = wf._dependency_metadata()
    deps = metadata["dependencies"]
    assert deps["declaredIds"] == ["dep-1"]
    assert deps["resolution"] == DEPENDENCY_RESOLUTION_NOT_APPLICABLE
    assert len(deps["outcomes"]) == 1
    outcome = deps["outcomes"][0]
    assert outcome["resolution"] == DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN
    assert outcome["failureCount"] == 1
    assert outcome["lastFailedAt"] == "2026-05-08T12:00:00Z"


def test_bypass_records_per_outcome_resolution_bypassed(monkeypatch) -> None:
    """Bypassing the dependency wait records bypassed resolution per outcome."""

    fixed_now = datetime(2026, 5, 8, 13, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id
        in {DEPENDENCY_GATE_PATCH, DEPENDENCY_WAIT_THROUGH_RERUN_PATCH},
    )
    monkeypatch.setattr(workflow, "now", lambda: fixed_now)

    wf = MoonMindRunWorkflow()
    wf._declared_dependencies = ["dep-1", "dep-2"]
    wf._unresolved_dependency_ids = {"dep-1", "dep-2"}
    wf._state = STATE_WAITING_ON_DEPENDENCIES
    wf._dependency_wait_started_at = fixed_now
    monkeypatch.setattr(wf, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(wf, "_update_memo", lambda: None)

    # First record a failure observation for dep-1 so we can verify
    # failureCount/lastFailedAt are preserved into the bypassed outcome.
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message=None,
    )

    wf._bypass_dependencies({"reason": "operator override"})

    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_BYPASSED
    assert wf._unresolved_dependency_ids == set()
    dep1 = wf._dependency_outcomes_by_id["dep-1"]
    dep2 = wf._dependency_outcomes_by_id["dep-2"]
    assert dep1["resolution"] == DEPENDENCY_RESOLUTION_BYPASSED
    assert dep1["failureCount"] == 1
    assert dep1["lastFailedAt"] == "2026-05-08T12:00:00Z"
    assert dep2["resolution"] == DEPENDENCY_RESOLUTION_BYPASSED
    assert dep2["failureCount"] == 0



def test_late_completed_signal_after_bypass_preserves_bypassed_resolution(
    monkeypatch,
) -> None:
    """A late completed signal must not erase an operator bypass audit trail."""

    fixed_now = datetime(2026, 5, 8, 13, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id
        in {DEPENDENCY_GATE_PATCH, DEPENDENCY_WAIT_THROUGH_RERUN_PATCH},
    )
    monkeypatch.setattr(workflow, "now", lambda: fixed_now)

    wf = MoonMindRunWorkflow()
    wf._declared_dependencies = ["dep-1"]
    wf._unresolved_dependency_ids = {"dep-1"}
    wf._state = STATE_WAITING_ON_DEPENDENCIES
    wf._dependency_wait_started_at = fixed_now
    monkeypatch.setattr(wf, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(wf, "_update_memo", lambda: None)

    wf._bypass_dependencies({"reason": "operator override"})
    before = dict(wf._dependency_outcomes_by_id["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T13:15:00Z",
        failure_category=None,
        message="late completion",
    )

    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_BYPASSED
    assert wf._dependency_outcomes_by_id["dep-1"] == before


def test_late_signals_after_manual_override_preserve_manual_resolution(
    monkeypatch,
) -> None:
    """Late dependency signals must not rewrite a manual skip decision."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])
    monkeypatch.setattr(wf, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(wf, "_update_memo", lambda: None)
    wf._state = STATE_WAITING_ON_DEPENDENCIES

    wf.skip_dependency_wait()

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="completed",
        close_status="completed",
        resolved_at="2026-05-08T13:15:00Z",
        failure_category=None,
        message="late completion",
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T13:30:00Z",
        failure_category="dependency_failed",
        message="late failure",
    )

    assert wf._dependency_resolution == DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
    assert wf._dependency_outcomes_by_id == {}
    assert wf._dependency_failure is None


def test_top_level_resolution_preserves_override_outcomes(monkeypatch) -> None:
    """Top-level rollup prioritizes operator override outcomes over satisfaction."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1", "dep-2"])
    wf._dependency_outcomes_by_id["dep-1"] = {
        "workflowId": "dep-1",
        "resolution": DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN,
    }
    wf._dependency_outcomes_by_id["dep-2"] = {
        "workflowId": "dep-2",
        "resolution": DEPENDENCY_RESOLUTION_BYPASSED,
    }

    assert wf._compute_top_level_resolution() == DEPENDENCY_RESOLUTION_BYPASSED

    wf._dependency_outcomes_by_id["dep-2"]["resolution"] = (
        DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
    )

    assert (
        wf._compute_top_level_resolution()
        == DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
    )


def test_repeated_reconcile_observation_with_new_timestamp_is_not_new_failure(
    monkeypatch,
) -> None:
    """Fresh reconcile timestamps for the same failed prerequisite are idempotent."""

    wf = _make_workflow_under_rerun_patch(monkeypatch, declared=["dep-1"])

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="upstream broken",
    )
    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:30:00Z",
        failure_category="dependency_failed",
        message="upstream broken",
    )

    outcome = wf._dependency_outcomes_by_id["dep-1"]
    assert outcome["failureCount"] == 1
    assert outcome["lastFailedAt"] == "2026-05-08T12:00:00Z"

# ---------------------------------------------------------------------------
# Backwards-compatibility: legacy fail-fast path
# ---------------------------------------------------------------------------


def test_legacy_workflows_without_rerun_patch_still_fail_fast(monkeypatch) -> None:
    """In-flight workflows whose history predates the rerun patch keep fail-fast."""

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        workflow,
        "now",
        lambda: datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc),
    )

    wf = MoonMindRunWorkflow()
    wf._declared_dependencies = ["dep-1"]
    wf._unresolved_dependency_ids = {"dep-1"}

    wf._record_dependency_outcome(
        prerequisite_workflow_id="dep-1",
        terminal_state="failed",
        close_status="failed",
        resolved_at="2026-05-08T12:00:00Z",
        failure_category="dependency_failed",
        message="legacy upstream broken",
    )

    assert wf._dependency_failure is not None
    assert wf._failed_dependency_id == "dep-1"
    assert "dep-1" not in wf._unresolved_dependency_ids


# ---------------------------------------------------------------------------
# End-to-end: failed prereq + later DependencyResolved completed signal
# ---------------------------------------------------------------------------


async def _fake_execute_activity(activity_name, *args, **kwargs):
    if activity_name == "artifact.read":
        import json

        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {"title": "Test"},
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "tools": [],
                "nodes": [],
                "edges": [],
            }
        ).encode("utf-8")
    return {}


@pytest.fixture
def mock_run_environment(monkeypatch):
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_trusted_owner_metadata",
        lambda self: ("user", "user-1"),
    )
    monkeypatch.setattr(workflow, "execute_activity", _fake_execute_activity)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)

    async def fake_planning_stage(*args, **kwargs):
        return "ref-123"

    async def fake_execution_stage(*args, **kwargs):
        pass

    monkeypatch.setattr(
        MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage
    )


@pytest.mark.asyncio
async def test_failed_prereq_then_completed_signal_unblocks_dependent(
    mock_run_environment,
    monkeypatch,
):
    """Reconciliation observes a failed prereq; later a completed signal unblocks the run."""

    reconcile_call_count = 0

    async def fake_reconcile(self, dependency_ids):
        nonlocal reconcile_call_count
        reconcile_call_count += 1
        # Always observe failed terminal — the gate must keep waiting under
        # the rerun patch, not auto-fail.
        for workflow_id in dependency_ids:
            self._record_dependency_outcome(
                prerequisite_workflow_id=workflow_id,
                terminal_state="failed",
                close_status="failed",
                resolved_at=f"2026-05-08T12:0{reconcile_call_count}:00Z",
                failure_category="dependency_failed",
                message="upstream broken",
            )

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id
        in {DEPENDENCY_GATE_PATCH, DEPENDENCY_WAIT_THROUGH_RERUN_PATCH},
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-rerun",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-rerun-1"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-rerun",
                task_queue="test-task-queue-rerun",
            )

            # Wait until the workflow enters the dependency-wait state.
            for _ in range(50):
                status = await handle.query("get_status")
                if status.get("state") == "waiting_on_dependencies":
                    break
                await asyncio.sleep(0.01)

            status = await handle.query("get_status")
            assert status.get("state") == "waiting_on_dependencies", (
                f"workflow did not enter dependency wait, got {status}"
            )

            # Advance virtual time enough to fire at least one reconcile cycle
            # so the failed observation is recorded. The dependent must NOT
            # auto-fail under the rerun patch — it should remain in
            # waiting_on_dependencies.
            await env.sleep(35)

            status = await handle.query("get_status")
            assert status.get("state") == "waiting_on_dependencies", (
                "Failed prerequisite under wait-through-rerun must not fail "
                f"the dependent. Got state={status.get('state')!r}"
            )

            # Now send a completed signal to simulate a successful rerun.
            await handle.signal(
                "DependencyResolved",
                {
                    "prerequisiteWorkflowId": "dep-rerun-1",
                    "terminalState": "completed",
                    "closeStatus": "completed",
                    "resolvedAt": "2026-05-08T13:00:00Z",
                    "failureCategory": None,
                    "message": "completed after rerun",
                },
            )

            result = await handle.result()

    assert result["status"] == "success"
    assert reconcile_call_count >= 1
