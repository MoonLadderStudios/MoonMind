"""AC9 / AC8: diagnostic bundles are safe & reproducible; CI corpus policy.

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

import json

import pytest

from moonmind.omnigent.faultkit.ci_policy import (
    FIXED_RELIABILITY_SEEDS,
    PR_CI_MAX_SCENARIOS,
    PR_CI_SEEDS,
    rotating_seeds,
)
from moonmind.omnigent.faultkit.diagnostics import (
    build_diagnostic_bundle,
    write_diagnostic_bundle,
)
from moonmind.omnigent.faultkit.generator import generate_scenario
from moonmind.omnigent.faultkit.harness import run_scenario
from moonmind.omnigent.faultkit.recording import scan_for_secrets


def test_diagnostic_bundle_is_reproducible_and_credential_free(tmp_path) -> None:
    result = run_scenario(generate_scenario(31))
    bundle = build_diagnostic_bundle(result)

    # Enough to reproduce without credentials / network / raw logs.
    assert bundle["seed"] == 31
    assert bundle["minimizedScenario"]["seed"] == 31
    assert "decisionJournal" in bundle
    assert "providerRequestLog" in bundle
    assert bundle["issue"] == "MoonLadderStudios/MoonMind#3709"

    # No secret-like content anywhere.
    assert scan_for_secrets(bundle) == []

    path = write_diagnostic_bundle(result, tmp_path / "bundle.json")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["seed"] == 31


def test_diagnostic_bundle_reproduces_the_run() -> None:
    result = run_scenario(generate_scenario(99))
    bundle = build_diagnostic_bundle(result)
    from moonmind.omnigent.faultkit.scenario import load_scenario

    replay = run_scenario(load_scenario(bundle["minimizedScenario"]))
    assert replay.decision_journal() == result.decision_journal()


def test_pr_ci_corpus_is_bounded_and_deterministic() -> None:
    assert len(PR_CI_SEEDS) <= PR_CI_MAX_SCENARIOS
    assert PR_CI_SEEDS == tuple(range(len(PR_CI_SEEDS)))


def test_rotating_seed_windows_do_not_overlap() -> None:
    shard0 = set(rotating_seeds(0))
    shard1 = set(rotating_seeds(1))
    assert shard0.isdisjoint(shard1)
    with pytest.raises(ValueError):
        rotating_seeds(-1)


def test_fixed_reliability_seed_corpus_is_declared() -> None:
    assert 12345 in FIXED_RELIABILITY_SEEDS
    assert 3698 in FIXED_RELIABILITY_SEEDS  # missed terminal edge incident
