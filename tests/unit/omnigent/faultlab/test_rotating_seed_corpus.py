"""Rotating seed-corpus policy and the scheduled/dispatched expanded sweep.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 8).

Two things are proven here:

* The seed-range *policy* (:mod:`tools.omnigent_faultlab.ci_seeds`) is
  hermetic and deterministic: with no environment it is the fixed PR corpus; with
  the namespaced rotating vars it is a bounded ``range(offset, offset + count)``;
  malformed budgets fail fast. These tests run in required PR CI (fast).

* The *expanded rotating sweep* runs the generated corpus over a larger,
  date-rotated seed window against the production reconciler and the independent
  reference model, under explicit scenario-count and wall-time budgets. It is
  gated behind the rotating env vars so it is a no-op skip on every PR and runs
  only in the scheduled/dispatched CI job that sets them. Any violation writes a
  reproduction-complete, secret-safe diagnostic bundle (seed, minimized scenario,
  decision journal, provider request log, safe refs) for upload, and every run
  records its window and outcome (:mod:`tools.omnigent_faultlab.rotating_result`),
  which that job requires.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tools.omnigent_faultlab import (
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
from tools.omnigent_faultlab.ci_seeds import (
    DEFAULT_ROTATING_COUNT,
    ROTATING_COUNT_ENV,
    ROTATING_ENABLED_ENV,
    ROTATING_OFFSET_ENV,
)
from tools.omnigent_faultlab.diagnostics import (
    build_diagnostic_bundle,
    write_diagnostic_bundle,
)
from tools.omnigent_faultlab.invariants import violations
from tools.omnigent_faultlab.rotating_result import (
    RESULT_FILENAME,
    SweepResultError,
    check_sweep_result,
    main,
    write_sweep_result,
)

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
# Recorded sweep result (hermetic; runs in required PR CI)
# ---------------------------------------------------------------------------


def _write(directory, **overrides):
    values = {
        "seeds": range(8000, 8500),
        "ran": 500,
        "last_seed": 8499,
        "failing_seeds": [],
        "bundles_written": 0,
        "budget_seconds": 900.0,
        "elapsed_seconds": 12.5,
    }
    values.update(overrides)
    return write_sweep_result(directory, **values)


def test_recorded_result_carries_window_progress_and_failures(tmp_path):
    path = _write(
        tmp_path,
        ran=120,
        last_seed=8119,
        failing_seeds=list(range(8000, 8040)),
        bundles_written=25,
    )

    assert path == tmp_path / RESULT_FILENAME
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "offset": 8000,
        "count": 500,
        "ran": 120,
        "lastSeed": 8119,
        "truncated": True,
        "failures": 40,
        "failingSeeds": list(range(8000, 8025)),
        "bundlesWritten": 25,
        "budgetSeconds": 900.0,
        "elapsedSeconds": 12.5,
        "error": None,
        "status": "failed",
    }


def test_matching_passed_result_is_accepted_and_reports_truncation(tmp_path):
    _write(tmp_path, ran=300, last_seed=8299)

    record = check_sweep_result(tmp_path, offset=8000, count=500)

    assert record["status"] == "passed"
    assert record["truncated"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"seeds": range(0, 400)}, "window"),
        ({"ran": 0, "last_seed": None}, "no seed"),
        ({"failing_seeds": [8003]}, "8003"),
        ({"ran": 4, "last_seed": 8003, "error": "RuntimeError"}, "RuntimeError"),
    ],
)
def test_mismatched_empty_or_failing_result_is_rejected(tmp_path, overrides, message):
    _write(tmp_path, **overrides)

    with pytest.raises(SweepResultError, match=message):
        check_sweep_result(tmp_path, offset=8000, count=500)


def test_missing_result_means_the_sweep_did_not_run(tmp_path):
    with pytest.raises(SweepResultError, match="did not record"):
        check_sweep_result(tmp_path, offset=8000, count=500)


def test_record_command_summarizes_a_passed_sweep(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _write(tmp_path)

    assert main(["--dir", str(tmp_path), "--offset", "8000", "--count", "500"]) == 0

    out = capsys.readouterr().out
    assert "::notice title=Rotating fault-lab sweep::" in out
    assert "window=8000..8500 ran=500 failures=0" in out
    assert "8000..8500" in summary.read_text(encoding="utf-8")


def test_record_command_fails_when_the_sweep_was_skipped(tmp_path, capsys):
    assert main(["--dir", str(tmp_path), "--offset", "8000", "--count", "500"]) == 1
    assert "::error title=Rotating fault-lab sweep::" in capsys.readouterr().out


def test_sweep_records_the_window_it_executed(tmp_path, monkeypatch):
    monkeypatch.setenv(ROTATING_ENABLED_ENV, "1")
    monkeypatch.setenv(ROTATING_OFFSET_ENV, "5000")
    monkeypatch.setenv(ROTATING_COUNT_ENV, "3")
    monkeypatch.setenv(_DIAGNOSTICS_DIR_ENV, str(tmp_path))

    test_rotating_seed_corpus_holds_all_invariants()

    record = check_sweep_result(tmp_path, offset=5000, count=3)
    assert (record["ran"], record["lastSeed"], record["truncated"]) == (3, 5002, False)


# ---------------------------------------------------------------------------
# Expanded rotating sweep (gated to the scheduled/dispatched CI job)
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
        "rotating seed sweep runs only on schedule/dispatch "
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
    last_seed: int | None = None
    failures: list[str] = []
    failing_seeds: list[int] = []
    bundles_written = 0
    error: str | None = None
    try:
        for seed in seeds:
            if time.monotonic() - started > budget:
                break
            ran += 1
            last_seed = seed

            plan = generate_plan(seed)
            trace = run_plan(plan)

            found = violations(trace)
            reference_violation = trace.reference_violation
            duplicate_side_effects = trace.ledger.keys_with_multiple_side_effects()
            # is_deterministic re-runs the plan; a nondeterministic scenario is itself
            # a failure (no flaky-retry passes).
            nondeterministic = not is_deterministic(plan)

            if (
                found
                or reference_violation
                or duplicate_side_effects
                or nondeterministic
            ):
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
                failing_seeds.append(seed)

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
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        # Recorded on success too: the job requires this record, so a skipped
        # sweep or one that covered another window cannot pass.
        write_sweep_result(
            diagnostics_dir,
            seeds=seeds,
            ran=ran,
            last_seed=last_seed,
            failing_seeds=failing_seeds,
            bundles_written=bundles_written,
            budget_seconds=budget,
            elapsed_seconds=time.monotonic() - started,
            error=error,
        )

    print(
        f"rotating sweep: window={seeds.start}..{seeds.stop} ran={ran} "
        f"failures={len(failures)} budget={budget}s"
    )
    assert not failures, (
        f"{len(failures)} rotating seed(s) violated invariants; "
        f"bundles under {diagnostics_dir}:\n" + "\n".join(failures[:_MAX_BUNDLES])
    )
