"""Actual wall-time records from the existing CI reporting owner."""
import json

import pytest

from tools.ci import write_backend_matrix_summary as reporting


def test_phase_record_uses_monotonic_clock_and_only_ci_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting.time, "monotonic", lambda: 12.5)
    monkeypatch.setenv("MOONMIND_CI_REVISION", "abc")
    monkeypatch.setenv("MOONMIND_CI_SUITE", "integration-hermetic")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    monkeypatch.setenv("SECRET_TOKEN", "must-not-be-recorded")
    output = tmp_path / "phase-timings.jsonl"
    assert reporting.phase_timing_main([
        "--output", str(output), "--phase", "compose-down", "--start", "10", "--exit-code", "124",
    ]) == 0
    record = json.loads(output.read_text())
    assert record == {
        "schema": "moonmind.integration-phase-timing/v1",
        "phase": "compose-down", "startMonotonic": 10.0, "endMonotonic": 12.5,
        "elapsedSeconds": 2.5, "exitCode": 124, "revision": "abc",
        "suite": "integration-hermetic", "runId": "42", "attempt": "3",
    }


@pytest.mark.parametrize("start", ["nan", "inf", "-1", "13"])
def test_invalid_observation_cannot_become_measured_zero(tmp_path, monkeypatch, start):
    monkeypatch.setattr(reporting.time, "monotonic", lambda: 12.5)
    with pytest.raises(SystemExit):
        reporting.phase_timing_main([
            "--output", str(tmp_path / "out"), "--phase", "main-pytest",
            "--start", start, "--exit-code", "0",
        ])
    assert not (tmp_path / "out").exists()


def test_unwritable_timing_artifact_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reporting.time, "monotonic", lambda: 12.5)
    assert reporting.phase_timing_main([
        "--output", str(tmp_path), "--phase", "main-pytest", "--start", "10", "--exit-code", "7",
    ]) == 1
    assert "cannot retain integration phase timing" in capsys.readouterr().err
