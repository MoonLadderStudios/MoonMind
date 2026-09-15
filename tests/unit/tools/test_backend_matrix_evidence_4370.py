"""MoonLadderStudios/MoonMind#4370: pytest log/report/timing evidence hook."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.ci.write_backend_matrix_summary import (
    build_evidence,
    classify_outcome,
    parse_junit,
    write_durations_snapshot,
    write_slowest_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def _junit_file(tmp_path: Path) -> Path:
    root = ET.Element("testsuite", {"tests": "3", "failures": "1", "errors": "0", "skipped": "1", "time": "12.5"})
    for name, duration in (("test_a", "10.0"), ("test_b", "2.0"), ("test_c", "0.5")):
        ET.SubElement(root, "testcase", {"classname": "mod", "name": name, "time": duration})
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path)
    return path


def test_parse_junit_counts_from_attributes_not_progress(tmp_path: Path) -> None:
    summary = parse_junit(_junit_file(tmp_path))
    assert (summary.tests, summary.failures, summary.errors, summary.skipped) == (3, 1, 0, 1)
    assert summary.cases[0].nodeid == "mod::test_a"
    assert summary.cases[0].time == 10.0


def test_missing_junit_is_unavailable_not_zero(tmp_path: Path) -> None:
    args_ns = _args(
        tmp_path,
        junit=tmp_path / "missing.xml",
        selected="true",
        outcome="failure",
    )
    markdown, _ = build_evidence(args_ns)
    assert "unavailable" in markdown
    assert "not zero tests" in markdown
    assert "tests=`0`" not in markdown
    assert "Outcome: `failed (interrupted" in markdown


def test_cancelled_without_junit_never_reports_success(tmp_path: Path) -> None:
    assert classify_outcome(True, False, "cancelled") == "canceled (interrupted -- no final JUnit report)"
    args_ns = _args(tmp_path, junit=tmp_path / "missing.xml", selected="true", outcome="cancelled")
    markdown, _ = build_evidence(args_ns)
    assert "canceled" in markdown
    assert "not zero tests" in markdown
    assert "passed" not in markdown.split("Outcome:")[1].split("\n")[0].replace("passed`", "X") or True
    # Outcome line must not claim passed.
    outcome_line = next(line for line in markdown.splitlines() if line.startswith("- Outcome:"))
    assert "passed" not in outcome_line


def test_unselected_row_reports_intentionally_unselected(tmp_path: Path) -> None:
    args_ns = _args(tmp_path, junit=tmp_path / "missing.xml", selected="false", outcome="skipped")
    markdown, _ = build_evidence(args_ns)
    assert "intentionally unselected" in markdown


def test_slowest_and_durations_snapshot_are_separate_files(tmp_path: Path) -> None:
    junit_path = _junit_file(tmp_path)
    summary = parse_junit(junit_path)
    slowest = tmp_path / "slowest.txt"
    snapshot = tmp_path / "durations.json"
    write_slowest_report(summary, slowest)
    write_durations_snapshot("reliability-shard-1", summary, snapshot)
    assert "test_a" in slowest.read_text()
    payload = json.loads(snapshot.read_text())
    assert payload["suite"] == "reliability-shard-1"
    assert payload["tests"] == 3
    assert payload["cases"][0]["nodeid"] == "mod::test_a"


def test_hook_never_fails_job_on_bad_xml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<not-xml", encoding="utf-8")
    summary_file = tmp_path / "summary.md"
    proc = subprocess.run(
        [
            "python3", "tools/ci/write_backend_matrix_summary.py",
            "--suite", "unit-fast",
            "--junit", str(bad),
            "--log", str(tmp_path / "missing.log"),
            "--slowest", str(tmp_path / "slowest.txt"),
            "--durations-snapshot", str(tmp_path / "durations.json"),
            "--summary", str(summary_file),
            "--selected", "true",
            "--test-outcome", "failure",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "JUnit parse failed" in summary_file.read_text()


def test_tee_failure_still_fails_step() -> None:
    proc = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nset +e\n(false) 2>&1 | tee /tmp/4370-tee-probe.log\nstatus=${PIPESTATUS[0]}\nexit $status"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0


def test_workflow_wiring_pins() -> None:
    text = WORKFLOW.read_text()
    # R1: streamed text log with unbuffered delivery, pipefail-safe tee.
    assert "PYTHONUNBUFFERED" in text
    assert "2>&1 | tee artifacts/pytest-backend-" in text
    assert "status=${PIPESTATUS[0]}" in text
    assert "set -euo pipefail" in text
    # R2: serial reliability verbosity; slowest report derived by the hook.
    assert "-vv --tb=short --durations=25" in text
    assert "tools/ci/write_backend_matrix_summary.py" in text
    # R3: suite/shard/run-attempt identity, success+failure+cancellation
    # coverage, modest retention, known files only.
    assert "pytest-${{ matrix.suite }}-attempt-${{ github.run_attempt }}" in text
    assert "retention-days: 7" in text
    assert "if-no-files-found: warn" in text
    assert "pytest-backend-${{ matrix.suite }}-slowest.txt" in text
    assert "pytest-backend-${{ matrix.suite }}-durations.json" in text
    # R4: bounded scoped collection with record-and-continue.
    assert "timeout 100s docker compose" in text
    assert "collection-status.txt" in text
    assert "not zero tests" in text
    # R5: per-job step summary via the hook, never progress-% parsing.
    assert "$GITHUB_STEP_SUMMARY" in text
    assert "--test-outcome" in text
    # ci-required stays a fast no-checkout aggregator (R7 untouched).
    assert "ci-required" in text


def _args(tmp_path: Path, *, junit: Path, selected: str, outcome: str):  # noqa: ANN202
    import argparse

    log = tmp_path / "pytest.log"
    log.write_text("streamed output\n", encoding="utf-8")
    return argparse.Namespace(
        suite="unit-fast",
        shard="",
        junit=str(junit),
        log=str(log),
        slowest=str(tmp_path / "slowest.txt"),
        durations_snapshot=str(tmp_path / "durations.json"),
        summary=str(tmp_path / "summary.md"),
        revision="abc123",
        run_id="1",
        attempt="1",
        selected=selected,
        test_outcome=outcome,
        test_seconds_file="",
    )
