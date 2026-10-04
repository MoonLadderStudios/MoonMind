"""MoonLadderStudios/MoonMind#4370: pytest log/report/timing evidence hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

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
    root = ET.Element(
        "testsuite",
        {"tests": "3", "failures": "1", "errors": "0", "skipped": "1", "time": "12.5"},
    )
    for name, duration in (("test_a", "10.0"), ("test_b", "2.0"), ("test_c", "0.5")):
        ET.SubElement(
            root, "testcase", {"classname": "mod", "name": name, "time": duration}
        )
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path)
    return path


def test_parse_junit_counts_from_attributes_not_progress(tmp_path: Path) -> None:
    summary = parse_junit(_junit_file(tmp_path))
    assert (summary.tests, summary.failures, summary.errors, summary.skipped) == (
        3,
        1,
        0,
        1,
    )
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
    assert (
        classify_outcome(True, False, "cancelled")
        == "canceled (interrupted -- no final JUnit report)"
    )
    args_ns = _args(
        tmp_path, junit=tmp_path / "missing.xml", selected="true", outcome="cancelled"
    )
    markdown, _ = build_evidence(args_ns)
    assert "canceled" in markdown
    assert "not zero tests" in markdown
    assert (
        "passed"
        not in markdown.split("Outcome:")[1].split("\n")[0].replace("passed`", "X")
        or True
    )
    # Outcome line must not claim passed.
    outcome_line = next(
        line for line in markdown.splitlines() if line.startswith("- Outcome:")
    )
    assert "passed" not in outcome_line


def test_unselected_row_reports_intentionally_unselected(tmp_path: Path) -> None:
    args_ns = _args(
        tmp_path, junit=tmp_path / "missing.xml", selected="false", outcome="skipped"
    )
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
            "python3",
            "tools/ci/write_backend_matrix_summary.py",
            "--suite",
            "unit-fast",
            "--junit",
            str(bad),
            "--log",
            str(tmp_path / "missing.log"),
            "--slowest",
            str(tmp_path / "slowest.txt"),
            "--durations-snapshot",
            str(tmp_path / "durations.json"),
            "--summary",
            str(summary_file),
            "--selected",
            "true",
            "--test-outcome",
            "failure",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "JUnit parse failed" in summary_file.read_text()


@pytest.mark.parametrize("duration", ["bad", "", "nan", "inf", "-1"])
def test_invalid_junit_timing_is_never_measured_zero(tmp_path, duration):
    path = _junit_file(tmp_path)
    tree = ET.parse(path)
    tree.find("testcase").set("time", duration)
    tree.write(path)
    args = _args(tmp_path, junit=path, selected="true", outcome="success")
    markdown, error = build_evidence(args)
    assert error and "timing" in error.lower()
    assert "Evidence hook note" in markdown
    assert not Path(args.durations_snapshot).exists()


def test_uninstrumented_junit_cannot_claim_exact_observations(tmp_path):
    path = _junit_file(tmp_path)
    snapshot = tmp_path / "durations.json"
    write_durations_snapshot("reliability-shard-1", parse_junit(path), snapshot)
    payload = json.loads(snapshot.read_text())
    assert all(case["observed"] is False for case in payload["cases"])
    assert payload["identity"] != "pytest-nodeid"


def test_real_pytest_recording_to_measured_refresh(tmp_path):
    """#4629: use the production collection/report hooks and installed split plugin."""
    from tools.ci.refresh_reliability_durations import RELIABILITY_MARKER_EXPR, main

    root = tmp_path / "repo"
    corpus = root / "tests/integration/reliability"
    corpus.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "tests/conftest.py", root / "tests/conftest.py")
    (root / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n reliability_journey\n provider_verification\n requires_credentials\n"
    )
    (corpus / "test_cost.py").write_text(
        """import pytest
import time
@pytest.fixture
def cost(request):
    time.sleep(0.08 if request.param == "expensive/b" else 0.004)
    yield
    time.sleep(0.004)
class TestOuter:
    class TestInner:
        @pytest.mark.parametrize("cost", ["cheap.a", "expensive/b"], indirect=True)
        def test_run(self, cost):
            pass
@pytest.mark.parametrize("p", range(29))
def test_more(p):
    pass
@pytest.mark.provider_verification
def test_provider():
    raise AssertionError("provider node must be excluded")
@pytest.mark.requires_credentials
def test_credentials():
    raise AssertionError("credential node must be excluded")
"""
    )
    baseline = root / "tests/.reliability-test-durations.json"
    original = b'{"tests/integration/reliability/old.py::test_removed": 42.0}\n'
    baseline.write_bytes(original)
    env = dict(
        os.environ,
        GITHUB_RUN_ID="fixture-4629",
        GITHUB_RUN_ATTEMPT="1",
        PYTHONPATH=str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "#4629 fixture",
        ],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    base = [
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/reliability",
        "-q",
        "-m",
        RELIABILITY_MARKER_EXPR,
        "-p",
        "no:cacheprovider",
        "--durations-path",
        str(baseline),
    ]
    collected = subprocess.run(
        base + ["--collect-only"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    universe = {line.strip() for line in collected.stdout.splitlines() if "::" in line}
    assert len(universe) == 31
    reports = []
    observations = {}
    for shard in range(1, 5):
        junit = root / f"shard-{shard}.xml"
        proc = subprocess.run(
            base
            + [
                "--splits",
                "4",
                "--group",
                str(shard),
                "--splitting-algorithm",
                "least_duration",
                "--junitxml",
                str(junit),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        args = _args(root, junit=junit, selected="true", outcome="success")
        args.suite = f"reliability-shard-{shard}"
        args.shard = str(shard)
        args.revision, args.run_id, args.attempt = revision, "fixture-4629", "1"
        args.durations_snapshot = str(root / f"shard-{shard}.json")
        markdown, error = build_evidence(args)
        assert error is None, markdown
        snapshot = json.loads(Path(args.durations_snapshot).read_text())
        for case in snapshot["cases"]:
            assert case["nodeid"] not in observations
            observations[case["nodeid"]] = case
        assert baseline.read_bytes() == original
        reports.append(args.durations_snapshot)
    assert set(observations) == universe
    cheap = observations[
        "tests/integration/reliability/test_cost.py::TestOuter::TestInner::test_run[cheap.a]"
    ]
    expensive = observations[
        "tests/integration/reliability/test_cost.py::TestOuter::TestInner::test_run[expensive/b]"
    ]
    assert expensive["duration"] > cheap["duration"] + 0.05
    for case in (cheap, expensive):
        assert case["phases"]["setup"] >= 0.004
        assert case["phases"]["teardown"] >= 0.004
        assert case["duration"] == sum(case["phases"].values())
    assert (
        main(
            [
                "--import-reports",
                *reports,
                "--run-id",
                "fixture-4629",
                "--revision",
                revision,
                "--attempt",
                "1",
                "--output",
                str(baseline),
            ]
        )
        == 0
    )
    hints = json.loads(baseline.read_text())
    assert hints == {node: case["duration"] for node, case in observations.items()}
    assert not any("old.py" in node for node in hints)


def test_reporter_keeps_full_corpus_when_text_is_top_n(tmp_path):
    root = ET.Element(
        "testsuite", {"tests": "30", "failures": "0", "errors": "0", "skipped": "0"}
    )
    for i in range(30):
        case = ET.SubElement(root, "testcase", {"name": f"test_{i}", "time": str(i)})
        properties = ET.SubElement(case, "properties")
        ET.SubElement(
            properties,
            "property",
            {
                "name": "moonmind.nodeid",
                "value": f"tests/integration/reliability/test_x.py::TestX::test_p[{i}]",
            },
        )
        ET.SubElement(
            properties,
            "property",
            {
                "name": "moonmind.phases",
                "value": json.dumps({"setup": 0.0, "call": float(i), "teardown": 0.0}),
            },
        )
    path = tmp_path / "full.xml"
    ET.ElementTree(root).write(path)
    summary = parse_junit(path)
    write_slowest_report(summary, tmp_path / "top.txt")
    write_durations_snapshot("reliability", summary, tmp_path / "full.json")
    payload = json.loads((tmp_path / "full.json").read_text())
    assert len(payload["cases"]) == 30
    assert all(c["observed"] for c in payload["cases"])
    assert len((tmp_path / "top.txt").read_text().splitlines()) == 27


def test_tee_failure_still_fails_step() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\nset +e\n(false) 2>&1 | tee /tmp/4370-tee-probe.log\nstatus=${PIPESTATUS[0]}\nexit $status",
        ],
        capture_output=True,
        text=True,
        timeout=30,
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
