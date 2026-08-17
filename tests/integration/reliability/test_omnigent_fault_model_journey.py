"""Hermetic reliability journey for the Omnigent fault-injection model suite.

MoonLadderStudios/MoonMind#3709.

This journey runs the fixed reliability seed corpus and the full incident corpus
through the reconciler-under-test, enforces all twelve invariants, and proves the
diagnostic bundle emitted on any failure is reproducible and credential-free.

The suite is layered by design: the same declarative scenarios drive the pure
domain reconciler here, and are the reusable driver for the heavier PostgreSQL,
Temporal, API/browser, and exact-image layers described in
``docs/Omnigent/FaultInjectionReliabilitySuite.md``. This journey covers the pure
domain, incident-corpus, and diagnostic boundaries hermetically (no Temporal
server, no database, no credentials, no network).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultkit.ci_policy import (
    FIXED_RELIABILITY_SEEDS,
    ROTATING_TIME_BUDGET_SECONDS,
)
from moonmind.omnigent.faultkit.corpus import initial_incident_scenarios
from moonmind.omnigent.faultkit.diagnostics import build_diagnostic_bundle
from moonmind.omnigent.faultkit.generator import generate_scenario
from moonmind.omnigent.faultkit.harness import run_scenario
from moonmind.omnigent.faultkit.invariants import check_invariants
from moonmind.omnigent.faultkit.recording import scan_for_secrets

pytestmark = pytest.mark.reliability_journey


def test_fixed_reliability_seed_corpus_holds_every_invariant() -> None:
    assert ROTATING_TIME_BUDGET_SECONDS == 30 * 60
    for seed in FIXED_RELIABILITY_SEEDS:
        result = run_scenario(generate_scenario(seed))
        violations = check_invariants(result)
        assert violations == [], (
            f"seed {seed} violated: {[v.invariant for v in violations]}"
        )


def test_incident_corpus_journey_with_diagnostics() -> None:
    for incident in initial_incident_scenarios():
        result = run_scenario(incident.scenario)
        violations = check_invariants(result)
        assert violations == [], (
            f"incident {incident.slug} violated: {[v.invariant for v in violations]}"
        )
        # A diagnostic bundle is always producible and always secret-free.
        bundle = build_diagnostic_bundle(result, violations=violations)
        assert scan_for_secrets(bundle) == []
        assert bundle["scenarioName"] == incident.slug


def test_rotating_corpus_sample_is_deterministic_and_safe() -> None:
    # A bounded slice of the rotating corpus, kept well inside the CI budget.
    for seed in range(200, 260):
        first = run_scenario(generate_scenario(seed))
        assert check_invariants(first) == []
