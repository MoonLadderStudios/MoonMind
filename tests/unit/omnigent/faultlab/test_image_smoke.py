"""Unit coverage for the exact-image fault-matrix smoke (AC7 image layer).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7).

These tests prove the *portable core* of the exact-image smoke is correct and
secret-safe. The CI job runs the very same ``run_image_fault_matrix`` inside the
deployable API/worker image; here we prove it converges cleanly, is
deterministic, and emits only bounded, digest-free evidence so a failing image
smoke is diagnosable without leaking scenario content.
"""

from __future__ import annotations

from moonmind.omnigent.faultlab.image_smoke import (
    IMAGE_SMOKE_SCHEMA_VERSION,
    run_image_fault_matrix,
)


def test_image_fault_matrix_is_clean_and_deterministic() -> None:
    report = run_image_fault_matrix(seed_count=12, source_commit="deadbeef")
    assert report.ok
    assert report.failing_seeds == ()
    assert len(report.results) == 12
    for result in report.results:
        assert result.converged
        assert result.deterministic
        assert result.violation_count == 0
        assert result.violated_invariants == ()


def test_image_fault_matrix_report_is_bounded_and_secret_safe() -> None:
    report = run_image_fault_matrix(seed_count=6, source_commit="cafef00d")
    payload = report.to_dict()
    assert payload["schemaVersion"] == IMAGE_SMOKE_SCHEMA_VERSION
    assert payload["sourceCommit"] == "cafef00d"
    assert payload["ok"] is True
    # The report carries only bounded scalars and invariant *names*, never raw
    # scenario payloads, prompts, or credentials (invariant 11).
    for result in payload["results"]:
        assert set(result) == {
            "seed",
            "converged",
            "deterministic",
            "violationCount",
            "violatedInvariants",
            "ok",
        }
        assert isinstance(result["seed"], int)
        assert all(isinstance(name, str) for name in result["violatedInvariants"])


def test_image_fault_matrix_is_reproducible() -> None:
    # A seed matrix produces identical decisions and observations run-to-run
    # (deterministic replay, invariant 12) so the image smoke is not flaky.
    first = run_image_fault_matrix(seed_count=8).to_dict()
    second = run_image_fault_matrix(seed_count=8).to_dict()
    assert first["results"] == second["results"]
