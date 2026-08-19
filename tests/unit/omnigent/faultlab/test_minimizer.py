"""Failing-plan minimization preserves the failure and is seed-reproducible.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 5).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultlab import FaultPlan, minimize_plan
from moonmind.omnigent.faultlab.harness import ObservationFault
from moonmind.omnigent.faultlab.scenario import (
    CommandWindow,
    LogicalOperation,
    ResponseBehavior,
)


def _drop_oracle(plan: FaultPlan) -> frozenset:
    """Synthetic failure: the plan drops the submit response.

    The production reducer handles every generated fault, so a synthetic oracle
    is used to exercise the *minimizer mechanism* against a known failure shape.
    """

    return (
        frozenset({"synthetic_drop"})
        if plan.submit_response == ResponseBehavior.DROP
        else frozenset()
    )


def test_minimize_reduces_to_the_essential_fault():
    big = FaultPlan(
        submit_response=ResponseBehavior.DROP,
        observation_faults=(
            ObservationFault.RUNNING,
            ObservationFault.DROP_SNAPSHOT,
            ObservationFault.MISSED_EDGE,
        ),
        command_crashes={
            LogicalOperation.ENSURE_HOST: CommandWindow.BEFORE_CLAIM,
            LogicalOperation.SUBMIT_TURN: (
                CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT
            ),
        },
        recovery_round=9,
        max_turn_attempts=3,
    )
    minimized = minimize_plan(big, oracle=_drop_oracle)

    # Everything not needed to reproduce the drop failure is removed.
    assert minimized.submit_response == ResponseBehavior.DROP
    assert minimized.observation_faults == ()
    assert minimized.command_crashes == {}
    # The failure is still reproduced by the reduced plan.
    assert _drop_oracle(minimized)


def test_minimize_is_deterministic():
    big = FaultPlan(
        submit_response=ResponseBehavior.DROP,
        observation_faults=(ObservationFault.RUNNING, ObservationFault.MISSED_EDGE),
        recovery_round=7,
    )
    first = minimize_plan(big, oracle=_drop_oracle)
    second = minimize_plan(big, oracle=_drop_oracle)
    assert first == second


def test_minimize_rejects_a_non_failing_plan():
    healthy = FaultPlan()
    with pytest.raises(ValueError):
        minimize_plan(healthy, oracle=_drop_oracle)
