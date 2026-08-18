"""Hermetic reliability-journey corpus for the Omnigent fault-injection suite.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criteria 7 and 8).

This is the required-CI reliability journey for the fault lab. It runs the fixed
declarative corpus and a fixed deterministic seed corpus against the *production*
reconciler, asserting the twelve invariants and strict determinism. It is fully
hermetic — no network, credentials, Docker, or Temporal server — so it is safe
for required CI (``integration_ci``) and belongs to the reliability journey.

Larger, rotating seed coverage runs on main/schedule via
``tests/unit/omnigent/faultlab/test_generated_properties.py`` scaled up; this
journey keeps a bounded, predictable budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.omnigent.faultlab import generate_plan, is_deterministic, run_plan
from moonmind.omnigent.faultlab.corpus import (
    INITIAL_CORPUS,
    load_corpus_dir,
    replay_scenario,
    scenario_violations,
)
from moonmind.omnigent.faultlab.diagnostics import build_diagnostic_bundle
from moonmind.omnigent.faultlab.invariants import violations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]

#: The required-CI fixed seed corpus. Bounded so the journey stays predictable.
FIXED_SEED_CORPUS = range(128)

_PACKAGED_SCENARIOS = (
    Path(__file__).parents[3]
    / "moonmind"
    / "omnigent"
    / "faultlab"
    / "scenarios"
)


def test_fixed_seed_corpus_holds_all_invariants_and_is_deterministic():
    for seed in FIXED_SEED_CORPUS:
        plan = generate_plan(seed)
        trace = run_plan(plan)
        found = violations(trace)
        if found:
            bundle = build_diagnostic_bundle(trace, source_ref=f"seed-{seed}")
            pytest.fail(f"seed={seed} violated invariants: {bundle.invariant_violations}")
        # A nondeterministic scenario is itself a failure; no flaky retry passes.
        assert is_deterministic(plan), f"seed={seed} is nondeterministic"


def test_declarative_corpus_scenarios_replay_cleanly():
    scenarios = load_corpus_dir(_PACKAGED_SCENARIOS)
    assert scenarios, "expected packaged fault scenarios"
    assert len(scenarios) == len(INITIAL_CORPUS)
    for scenario in scenarios:
        trace = replay_scenario(scenario)
        assert trace.converged, f"{scenario.scenario_id} did not converge"
        assert scenario_violations(scenario) == [], scenario.scenario_id


def test_every_failure_would_produce_a_reproduction_complete_bundle():
    """A representative faulty run yields a secret-safe, seed-reproducible bundle."""

    trace = run_plan(generate_plan(3698))
    bundle = build_diagnostic_bundle(trace, source_ref="#3698").to_dict()
    # Seed + declarative scenario + journals reproduce without credentials/network.
    assert "seed" in bundle
    assert bundle["scenario"]["schemaVersion"].startswith("moonmind.omnigent-fault-scenario")
    assert bundle["decisionJournal"]
    assert all(
        entry["payloadDigest"].startswith("sha256:")
        for entry in bundle["providerRequestLog"]
    )
