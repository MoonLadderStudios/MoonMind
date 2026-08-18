"""Model-based / property-style generated reliability suite.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criteria 4 and 12).

Thousands of seed-generated fault interleavings run against the *production*
reconciler and are cross-checked against the independent reference model. Every
run must satisfy the twelve invariants. A nondeterministic scenario is itself a
failure (the CI policy forbids flaky-retry passes), so determinism is asserted
directly rather than by re-running.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultlab import (
    generate_plan,
    is_deterministic,
    run_plan,
)
from moonmind.omnigent.faultlab.diagnostics import build_diagnostic_bundle
from moonmind.omnigent.faultlab.invariants import check_all, violations

#: The deterministic bounded corpus that runs in required PR CI.
PR_CI_SEEDS = range(400)


def test_generated_corpus_satisfies_all_invariants():
    """Every seed in the bounded corpus holds all twelve invariants."""

    failures: list[str] = []
    for seed in PR_CI_SEEDS:
        trace = run_plan(generate_plan(seed))
        found = violations(trace)
        if found:
            # Emit a reproduction-complete, secret-safe bundle for the first few.
            bundle = build_diagnostic_bundle(trace, source_ref=f"seed-{seed}")
            failures.append(f"seed={seed} violations={found} bundle={bundle.seed}")
    assert not failures, "\n".join(failures[:10])


def test_generated_runs_never_violate_reference_ordering():
    """The reconciler never emits a command the independent model rejects."""

    for seed in PR_CI_SEEDS:
        trace = run_plan(generate_plan(seed))
        assert trace.reference_violation is None, (
            f"seed={seed}: {trace.reference_violation}"
        )


def test_reconciler_and_reference_model_agree_on_outcome():
    """The two independently written models agree on terminal outcome and closure."""

    for seed in PR_CI_SEEDS:
        trace = run_plan(generate_plan(seed))
        reference = trace.reference
        assert reference.is_closed(), f"seed={seed}: reference did not close"
        recon = (
            trace.final.terminal_outcome.value
            if trace.final.terminal_outcome
            else None
        )
        assert reference.terminal_outcome == recon, (
            f"seed={seed}: reference={reference.terminal_outcome} reconciler={recon}"
        )


def test_generated_runs_converge_to_ground_truth():
    for seed in PR_CI_SEEDS:
        trace = run_plan(generate_plan(seed))
        assert trace.converged, f"seed={seed} did not converge"
        # Convergence check already asserts the terminal equals the ground truth
        # (or cancellation); assert the invariant name explicitly here too.
        assert check_all(trace)["eventual_convergence"] == []


def test_generated_runs_are_at_most_once():
    for seed in PR_CI_SEEDS:
        trace = run_plan(generate_plan(seed))
        assert trace.ledger.keys_with_multiple_side_effects() == [], (
            f"seed={seed} performed a duplicate side effect"
        )


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 1234, 3698, 99999])
def test_representative_seeds_are_deterministic(seed):
    assert is_deterministic(generate_plan(seed))
