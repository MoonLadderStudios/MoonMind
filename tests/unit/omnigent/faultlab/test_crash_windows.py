"""Crash injection at every logical command window and fencing safety.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 10 and
invariant 4 fencing safety).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moonmind.omnigent.faultlab import FaultPlan, apply_decision, run_plan
from moonmind.omnigent.faultlab.harness import _initial_durable, _intent
from moonmind.omnigent.faultlab.invariants import violations
from moonmind.omnigent.faultlab.provider import ProgrammableFakeProvider
from moonmind.omnigent.faultlab.scenario import (
    CommandWindow,
    LogicalOperation,
)
from moonmind.omnigent.reconciler import DecisionKind, reconcile, ObservationSet

_CRASHABLE = [
    LogicalOperation.ENSURE_PROFILE_LEASE,
    LogicalOperation.ENSURE_HOST,
    LogicalOperation.ENSURE_SESSION,
    LogicalOperation.SUBMIT_TURN,
    LogicalOperation.HARVEST_EVIDENCE,
    LogicalOperation.BEGIN_CLEANUP,
    LogicalOperation.RELEASE_LEASES,
]


@pytest.mark.parametrize("operation", _CRASHABLE)
@pytest.mark.parametrize("window", list(CommandWindow))
def test_crash_at_every_window_still_converges_safely(operation, window):
    """A crash at each window on each command still converges at-most-once."""

    plan = FaultPlan(command_crashes={operation: window}, recovery_round=3)
    trace = run_plan(plan)
    assert trace.converged, f"{operation.value}/{window.value} did not converge"
    assert violations(trace) == []
    assert trace.crashes_fired, "expected the crash to actually fire"


def test_submit_crash_after_side_effect_does_not_duplicate_the_turn():
    """A crash after the submit side effect but before receipt must dedup."""

    plan = FaultPlan(
        command_crashes={
            LogicalOperation.SUBMIT_TURN: (
                CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT
            )
        },
        recovery_round=3,
    )
    trace = run_plan(plan)
    submit_effects = [
        rec for rec in trace.ledger.side_effects if rec.operation.value == "submit_turn"
    ]
    assert len(submit_effects) == 1


def test_fencing_rejects_a_stale_generation_command():
    """A decision computed against a superseded generation mutates nothing."""

    plan = FaultPlan()
    intent = _intent(plan)
    durable = _initial_durable(plan)
    provider = ProgrammableFakeProvider()

    decision = reconcile(
        intent=intent,
        durable=durable,
        observations=ObservationSet(),
        now=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert decision.kind == DecisionKind.ENSURE_PROFILE_LEASE

    # A replacement generation takes over before the old decision is applied.
    superseded = durable.model_copy(update={"fencing_generation": durable.fencing_generation + 1})
    next_durable, fenced = apply_decision(superseded, decision, provider)

    assert fenced is True
    assert next_durable == superseded  # no mutation
    assert provider.ledger.side_effects == []  # no side effect performed
