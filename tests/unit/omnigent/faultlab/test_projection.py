"""Boundary-neutral projection preserves the fault attempt journal.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7).

A boundary replay (PostgreSQL repository, Temporal, API) re-proves the reliability
invariants by replaying the projected command stream. If the projection carried
only the final successful transitions, a crashed / lost-response run and a
fault-free run would project identically, so the replay could not tell whether its
retry and at-most-once handling actually survives the injected ambiguity. These
tests prove the projection preserves the attempt journal and fault disposition
needed to replay each authority handoff.
"""

from __future__ import annotations

from moonmind.omnigent.faultlab import FaultPlan, project_run, run_plan
from moonmind.omnigent.faultlab.scenario import (
    CommandWindow,
    LogicalOperation,
    ResponseBehavior,
)

_CLEAN_PLAN = FaultPlan(seed=7, recovery_round=2)
_CRASHED_PLAN = FaultPlan(
    seed=7,
    recovery_round=2,
    command_crashes={
        LogicalOperation.SUBMIT_TURN: CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT
    },
)
_DROPPED_PLAN = FaultPlan(seed=7, recovery_round=3, submit_response=ResponseBehavior.DROP)


def test_clean_run_projects_no_fault_disposition():
    run = project_run(run_plan(_CLEAN_PLAN))
    assert run.crashes == ()
    assert run.faulted_commands == ()
    for command in run.commands:
        assert command.attempts == 1
        assert command.crash_windows == ()
        assert command.delivered is True
        assert command.faulted is False


def test_crashed_submit_projects_its_crash_window():
    run = project_run(run_plan(_CRASHED_PLAN))
    submit = run.submit_commands[0]
    assert submit.faulted, "a crashed submit must project a fault disposition"
    assert CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT.value in submit.crash_windows
    # The crash and its retry are visible as a run-level authority handoff.
    assert any(
        command_id == submit.command_id
        and window == CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT.value
        for command_id, window in run.crashes
    )


def test_dropped_submit_projects_lost_receipt_or_retry():
    run = project_run(run_plan(_DROPPED_PLAN))
    submit = run.submit_commands[0]
    # A dropped submit response is either retried (attempts > 1) or its receipt is
    # lost (delivered False) — either way the projection marks it faulted.
    assert submit.faulted
    assert ResponseBehavior.DROP.value in submit.responses


def test_faulted_and_clean_runs_share_commands_but_differ_in_disposition():
    """The exact regression: the two runs would be indistinguishable without the
    attempt disposition."""

    clean = project_run(run_plan(_CLEAN_PLAN))
    crashed = project_run(run_plan(_CRASHED_PLAN))
    # Same logical command stream (ids + types) ...
    assert [(c.command_id, c.command_type) for c in clean.commands] == [
        (c.command_id, c.command_type) for c in crashed.commands
    ]
    # ... but the fault disposition distinguishes them.
    assert clean.faulted_commands == ()
    assert crashed.faulted_commands != ()


def test_at_most_once_holds_despite_the_crash_retry():
    """The independent ledger still proves one side effect per key under the crash."""

    run = project_run(run_plan(_CRASHED_PLAN))
    assert run.ledger_multiple_side_effect_keys == ()
