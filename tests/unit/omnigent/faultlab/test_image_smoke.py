"""Unit coverage for the exact-image fault-matrix smoke (AC7 image layer).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7).

These tests prove the *portable core* of the exact-image smoke is correct and
secret-safe. The CI job runs the very same ``run_image_fault_matrix`` inside the
deployable API/worker image; here we prove it converges cleanly, is
deterministic, and emits only bounded, digest-free evidence so a failing image
smoke is diagnosable without leaking scenario content.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultlab.image_smoke import (
    BUILD_ID_FILE_ENV,
    IMAGE_SMOKE_SCHEMA_VERSION,
    UnknownImageSmokeRoleError,
    read_image_build_id,
    run_image_fault_matrix,
    verify_role_entrypoint,
)


def test_image_fault_matrix_is_clean_and_deterministic() -> None:
    report = run_image_fault_matrix(
        seed_count=12, image_ref="registry/app@sha256:" + "0" * 64
    )
    assert report.ok
    assert report.failing_seeds == ()
    assert len(report.results) == 12
    for result in report.results:
        assert result.converged
        assert result.deterministic
        assert result.violation_count == 0
        assert result.violated_invariants == ()


def test_image_fault_matrix_report_is_bounded_and_secret_safe() -> None:
    ref = "registry/app@sha256:" + "a" * 64
    report = run_image_fault_matrix(seed_count=6, image_ref=ref, role="worker")
    payload = report.to_dict()
    assert payload["schemaVersion"] == IMAGE_SMOKE_SCHEMA_VERSION
    # Provenance is the image's own verified identity, never a checkout commit.
    assert payload["imageRef"] == ref
    assert payload["role"] == "worker"
    assert "sourceCommit" not in payload
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


def test_read_image_build_id_reads_the_stamped_file(tmp_path) -> None:
    build_id_file = tmp_path / ".moonmind-build-id"
    build_id_file.write_text("20260818.42\n", encoding="utf-8")
    assert read_image_build_id(build_id_file) == "20260818.42"


def test_read_image_build_id_is_none_when_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(BUILD_ID_FILE_ENV, str(tmp_path / "missing"))
    assert read_image_build_id() is None


@pytest.mark.parametrize("role", ["api", "worker"])
def test_verify_role_entrypoint_resolves_known_roles(role) -> None:
    # The role's real startup module resolves in the current runtime (find_spec
    # would raise ImportError if it were stripped from the shipped image).
    assert verify_role_entrypoint(role)


def test_verify_role_entrypoint_rejects_unknown_role() -> None:
    with pytest.raises(UnknownImageSmokeRoleError):
        verify_role_entrypoint("scheduler")


def test_run_image_fault_matrix_rejects_unknown_role() -> None:
    with pytest.raises(UnknownImageSmokeRoleError):
        run_image_fault_matrix(seed_count=2, role="scheduler")
