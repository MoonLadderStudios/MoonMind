"""Rotating seed-corpus policy and the main/schedule expanded sweep.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 8).

Two things are proven here:

* The seed-range *policy* (:mod:`moonmind.omnigent.faultlab.ci_seeds`) is
  hermetic and deterministic: with no environment it is the fixed PR corpus; with
  the namespaced rotating vars it is a bounded ``range(offset, offset + count)``;
  malformed budgets fail fast. These tests run in required PR CI (fast).

* The *expanded rotating sweep* runs the generated corpus over a larger,
  date-rotated seed window against the production reconciler and the independent
  reference model, under explicit scenario-count and wall-time budgets. It is
  gated behind the rotating env vars so it is a no-op skip on every PR and runs
  only in the scheduled/main CI job that sets them. Any violation writes a
  reproduction-complete, secret-safe diagnostic bundle (seed, minimized scenario,
  decision journal, provider request log, safe refs) for upload.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from moonmind.omnigent.faultlab import (
    PR_CI_SEED_COUNT,
    generate_plan,
    is_deterministic,
    minimize_plan,
    pr_ci_seeds,
    resolve_seed_corpus,
    rotating_enabled,
    rotating_seeds,
    run_plan,
)
from moonmind.omnigent.faultlab.ci_seeds import (
    DEFAULT_ROTATING_COUNT,
    ROTATING_COUNT_ENV,
    ROTATING_ENABLED_ENV,
    ROTATING_OFFSET_ENV,
)
from moonmind.omnigent.faultlab.diagnostics import (
    build_diagnostic_bundle,
    write_diagnostic_bundle,
)
from moonmind.omnigent.faultlab.invariants import violations

# ---------------------------------------------------------------------------
# Seed-range policy (hermetic; runs in required PR CI)
# ---------------------------------------------------------------------------


def test_default_is_the_fixed_pr_corpus():
    """No rotating env -> the fixed, deterministic PR corpus (the default path)."""

    assert pr_ci_seeds() == range(PR_CI_SEED_COUNT)
    assert rotating_seeds({}) is None
    assert rotating_enabled({}) is False
    assert resolve_seed_corpus({}) == range(PR_CI_SEED_COUNT)


def test_rotating_env_selects_a_bounded_window():
    env = {
        ROTATING_ENABLED_ENV: "1",
        ROTATING_OFFSET_ENV: "8000",
        ROTATING_COUNT_ENV: "500",
    }
    assert rotating_enabled(env) is True
    assert rotating_seeds(env) == range(8000, 8500)
    assert resolve_seed_corpus(env) == range(8000, 8500)


def test_rotating_env_without_count_uses_default_budget():
    env = {ROTATING_ENABLED_ENV: "true", ROTATING_OFFSET_ENV: "1000"}
    assert rotating_seeds(env) == range(1000, 1000 + DEFAULT_ROTATING_COUNT)


def test_rotating_disabled_flag_is_ignored():
    env = {ROTATING_ENABLED_ENV: "0", ROTATING_OFFSET_ENV: "1000"}
    assert rotating_seeds(env) is None
    assert resolve_seed_corpus(env) == range(PR_CI_SEED_COUNT)


@pytest.mark.parametrize(
    "env",
    [
        {ROTATING_ENABLED_ENV: "1", ROTATING_COUNT_ENV: "0"},
        {ROTATING_ENABLED_ENV: "1", ROTATING_COUNT_ENV: "-5"},
        {ROTATING_ENABLED_ENV: "1", ROTATING_OFFSET_ENV: "-1"},
        {ROTATING_ENABLED_ENV: "1", ROTATING_COUNT_ENV: "not-an-int"},
    ],
)
def test_malformed_budget_fails_fast(env):
    with pytest.raises(ValueError):
        rotating_seeds(env)


# ---------------------------------------------------------------------------
# Expanded rotating sweep (gated to the scheduled/main CI job)
# ---------------------------------------------------------------------------

_TIME_BUDGET_ENV = "MOONMIND_FAULTLAB_TIME_BUDGET_SECONDS"
_DIAGNOSTICS_DIR_ENV = "MOONMIND_FAULTLAB_DIAGNOSTICS_DIR"
_DEFAULT_TIME_BUDGET_SECONDS = 600.0
#: Cap how many failing bundles we serialize so a systemic break stays bounded.
_MAX_BUNDLES = 25


def _time_budget_seconds() -> float:
    raw = os.environ.get(_TIME_BUDGET_ENV)
    if not raw or not raw.strip():
        return _DEFAULT_TIME_BUDGET_SECONDS
    value = float(raw.strip())
    if value <= 0:
        raise ValueError(f"{_TIME_BUDGET_ENV} must be > 0, got {value}")
    return value


def _diagnostics_dir() -> Path:
    return Path(os.environ.get(_DIAGNOSTICS_DIR_ENV, "artifacts/faultlab-rotating"))


@pytest.mark.skipif(
    not rotating_enabled(),
    reason=(
        "rotating seed sweep runs only on main/schedule "
        f"(set {ROTATING_ENABLED_ENV}=1 with an offset/count window)"
    ),
)
def test_rotating_seed_corpus_holds_all_invariants():
    """Sweep the rotating window; every seed holds all invariants deterministically.

    Bounded by an explicit scenario-count window (the resolved seed range) and a
    wall-time budget. Because the wall-time budget may stop the sweep early on a
    slow runner, it is a *coverage* bound, not a correctness gate: correctness is
    asserted for every seed actually executed, and a failure of any executed seed
    still fails the job with a reproduction-complete bundle.
    """

    seeds = resolve_seed_corpus()
    assert len(seeds) > 0, "rotating window must contain at least one seed"

    budget = _time_budget_seconds()
    diagnostics_dir = _diagnostics_dir()
    started = time.monotonic()

    ran = 0
    failures: list[str] = []
    bundles_written = 0
    for seed in seeds:
        if time.monotonic() - started > budget:
            break
        ran += 1

        plan = generate_plan(seed)
        trace = run_plan(plan)

        found = violations(trace)
        reference_violation = trace.reference_violation
        duplicate_side_effects = trace.ledger.keys_with_multiple_side_effects()
        # is_deterministic re-runs the plan; a nondeterministic scenario is itself
        # a failure (no flaky-retry passes).
        nondeterministic = not is_deterministic(plan)

        if found or reference_violation or duplicate_side_effects or nondeterministic:
            reasons = []
            if found:
                reasons.append(f"invariants={found}")
            if reference_violation:
                reasons.append(f"reference={reference_violation}")
            if duplicate_side_effects:
                reasons.append(f"duplicate_side_effects={duplicate_side_effects}")
            if nondeterministic:
                reasons.append("nondeterministic")
            failures.append(f"seed={seed} {' '.join(reasons)}")

            if bundles_written < _MAX_BUNDLES:
                # Minimize the failing plan so the stored fixture is small; fall
                # back to the raw trace when only reference/determinism failed
                # (the invariant oracle has nothing to minimize against).
                try:
                    minimized_trace = run_plan(minimize_plan(plan))
                except ValueError:
                    minimized_trace = trace
                bundle = build_diagnostic_bundle(
                    minimized_trace, source_ref=f"rotating-seed-{seed}"
                )
                write_diagnostic_bundle(bundle, diagnostics_dir)
                bundles_written += 1

    print(
        f"rotating sweep: window={seeds.start}..{seeds.stop} ran={ran} "
        f"failures={len(failures)} budget={budget}s"
    )
    assert not failures, (
        f"{len(failures)} rotating seed(s) violated invariants; "
        f"bundles under {diagnostics_dir}:\n" + "\n".join(failures[:_MAX_BUNDLES])
    )
