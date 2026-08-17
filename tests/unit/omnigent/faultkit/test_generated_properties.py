"""AC4 / AC5 / AC10: generated tests enforce the properties and reproduce failures.

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultkit.ci_policy import PR_CI_SEEDS
from moonmind.omnigent.faultkit.commands import COMMAND_WINDOWS, LogicalCommand
from moonmind.omnigent.faultkit.generator import generate_scenario
from moonmind.omnigent.faultkit.harness import run_scenario
from moonmind.omnigent.faultkit.invariants import INVARIANTS, check_invariants
from moonmind.omnigent.faultkit.minimizer import minimize_scenario
from moonmind.omnigent.faultkit.reconciler import Decision, FaultKitReconciler
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    Scenario,
    ScenarioStep,
    SideEffectKind,
)


def test_twelve_named_invariants_exist() -> None:
    keys = {inv.key for inv in INVARIANTS}
    assert keys == {
        "at_most_once_submission",
        "eventual_convergence",
        "monotonic_authority",
        "fencing_safety",
        "no_blind_ambiguity_retry",
        "distinct_terminality",
        "lease_safety",
        "cleanup_safety",
        "historical_read_safety",
        "compatibility_safety",
        "secret_safety",
        "deterministic_replay",
    }
    assert len(INVARIANTS) == 12


@pytest.mark.parametrize("seed", PR_CI_SEEDS)
def test_generated_corpus_satisfies_every_invariant(seed: int) -> None:
    result = run_scenario(generate_scenario(seed))
    violations = check_invariants(result)
    assert violations == [], [f"{v.invariant}: {v.detail}" for v in violations]


@pytest.mark.parametrize("window", COMMAND_WINDOWS)
def test_crash_at_every_command_window_converges(window) -> None:
    scenario = Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=1,
        steps=(
            ScenarioStep(on=LogicalCommand.ENSURE_SESSION, side_effect=SideEffectKind.CREATED),
            ScenarioStep(on=LogicalCommand.SUBMIT_TURN, side_effect=SideEffectKind.ACCEPTED, crash_at=window),
            ScenarioStep(on=LogicalCommand.RECONCILE, snapshot={"sessionState": "idle", "turnState": "completed"}),
            ScenarioStep(on=LogicalCommand.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
        ),
        name=f"crash-{window.value}",
    )
    result = run_scenario(scenario)
    assert check_invariants(result) == []
    assert result.reconciler_view.turn_state.value == "completed"


def test_deterministic_replay_from_seed() -> None:
    first = run_scenario(generate_scenario(4242))
    second = run_scenario(generate_scenario(4242))
    assert first.decision_journal() == second.decision_journal()
    assert first.recorder.to_journal() == second.recorder.to_journal()


class _BlindRetryReconciler(FaultKitReconciler):
    """Deliberately broken: blindly re-submits an ambiguous turn identity."""

    def _authorize(self, step, idempotency_key):  # type: ignore[no-untyped-def]
        if step.on is LogicalCommand.SUBMIT_TURN:
            return Decision.PROCEED
        return super()._authorize(step, idempotency_key)


def _blind_retry_fails(scenario: Scenario) -> bool:
    result = run_scenario(scenario, reconciler_factory=_BlindRetryReconciler)
    return any(
        v.invariant == "no_blind_ambiguity_retry" for v in check_invariants(result)
    )


def test_suite_detects_and_minimizes_a_regression() -> None:
    # Find a generated scenario a broken reconciler violates.
    failing = next(
        (s for seed in range(200) if _blind_retry_fails(s := generate_scenario(seed))),
        None,
    )
    assert failing is not None, "expected the broken reconciler to violate a property"

    minimized = minimize_scenario(failing, _blind_retry_fails)
    # The minimized scenario still reproduces the failure...
    assert _blind_retry_fails(minimized)
    # ...is genuinely smaller...
    assert len(minimized.steps) <= len(failing.steps)
    assert len(minimized.steps) <= 3
    # ...and the correct reconciler passes it (no false positive).
    assert check_invariants(run_scenario(minimized)) == []
