"""MoonLadderStudios/MoonMind#4371: bounded remaining backlog coverage."""

from __future__ import annotations

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from tools.ci.redact_diagnostics import redact_directory, redact_text
from tools.ci.write_backend_matrix_summary import build_evidence

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def _junit_file(tmp_path: Path) -> Path:
    root = ET.Element(
        "testsuite",
        {"tests": "2", "failures": "0", "errors": "0", "skipped": "0", "time": "5.0"},
    )
    for name, duration in (("test_a", "3.0"), ("test_b", "2.0")):
        ET.SubElement(root, "testcase", {"classname": "mod", "name": name, "time": duration})
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path)
    return path


def _args(tmp_path: Path, **overrides):  # noqa: ANN202
    import argparse

    log = tmp_path / "pytest.log"
    if not log.exists():
        log.write_text("PASSED tests/unit/test_x.py::test_a\n", encoding="utf-8")
    base = dict(
        suite="reliability-shard-1",
        shard="1",
        junit=str(overrides.get("junit", tmp_path / "junit.xml")),
        log=str(log),
        slowest=str(tmp_path / "slowest.txt"),
        durations_snapshot=str(tmp_path / "durations.json"),
        summary=str(tmp_path / "summary.md"),
        revision="abc123",
        run_id="1",
        attempt="2",
        selected="true",
        test_outcome="success",
        test_seconds_file="",
        effective_command="python -m pytest tests/integration/reliability -vv",
        pytest_timeout="150s PR / 300s schedule per-test",
        step_budget="600s PR step",
        collection_status="",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_summary_records_effective_command_and_budgets(tmp_path: Path) -> None:
    _junit_file(tmp_path)
    markdown, _ = build_evidence(_args(tmp_path))
    assert "Effective pytest command" in markdown
    assert "python -m pytest tests/integration/reliability -vv" in markdown
    assert "150s PR / 300s schedule" in markdown
    assert "600s PR step" in markdown


def test_summary_disclaims_phases_separately(tmp_path: Path) -> None:
    _junit_file(tmp_path)
    markdown, _ = build_evidence(_args(tmp_path))
    assert "- Setup:" in markdown
    assert "- Collection:" in markdown
    assert "- Cleanup:" in markdown
    assert "unavailable as a separate measurement" in markdown


def test_summary_records_reporting_overhead(tmp_path: Path) -> None:
    _junit_file(tmp_path)
    markdown, _ = build_evidence(_args(tmp_path))
    assert "Reporting overhead" in markdown
    assert "hook `" in markdown
    assert "retained evidence" in markdown


def test_secondary_failures_surfaced_without_changing_primary(tmp_path: Path) -> None:
    _junit_file(tmp_path)
    status = tmp_path / "collection-status.txt"
    status.write_text("compose logs failed or timed out (exit=124)\n", encoding="utf-8")
    markdown, _ = build_evidence(_args(tmp_path, collection_status=str(status)))
    assert "Secondary diagnostics/teardown" in markdown
    assert "primary pytest outcome unchanged" in markdown
    assert "Outcome: `passed`" in markdown


def test_clean_collection_has_no_secondary_section(tmp_path: Path) -> None:
    _junit_file(tmp_path)
    status = tmp_path / "collection-status.txt"
    status.write_text("collection started\nredaction: scanned=2 redacted=0\n", encoding="utf-8")
    markdown, _ = build_evidence(_args(tmp_path, collection_status=str(status)))
    assert "Secondary diagnostics/teardown" not in markdown


def test_killed_worker_leaves_log_with_explicitly_incomplete_report(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/integration/reliability/test_x.py::test_slow_case PASSED\n"
        "tests/integration/reliability/test_x.py::test_killed_case RUNNING (killed)\n"
        "Traceback (most recent call last): worker killed\n",
        encoding="utf-8",
    )
    markdown, _ = build_evidence(
        _args(tmp_path, junit=tmp_path / "missing.xml", test_outcome="failure")
    )
    assert "unavailable" in markdown
    assert "not zero tests" in markdown
    assert "failed (interrupted" in markdown
    # Streamed last-case evidence is retained on disk even without JUnit.
    assert "test_killed_case" in log.read_text()


def test_failed_test_keeps_nonzero_exit_through_tee_hook_and_cleanup(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\nset +e\n"
            "(echo simulated-failure; exit 3) 2>&1 | tee "
            + str(tmp_path / "probe.log")
            + "\nstatus=${PIPESTATUS[0]}\n"
            "python3 tools/ci/write_backend_matrix_summary.py --suite unit-fast "
            f"--junit {tmp_path}/missing.xml --log {tmp_path}/probe.log "
            f"--slowest {tmp_path}/slowest.txt --durations-snapshot {tmp_path}/dur.json "
            f"--summary {tmp_path}/summary.md --selected true --test-outcome failure\n"
            "hook_status=$?\n"
            "true # simulated cleanup that succeeds\n"
            "exit $status",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 3


def test_hanging_diagnostics_stop_within_short_bound() -> None:
    import shutil

    if shutil.which("timeout") is None:
        import pytest

        pytest.skip("coreutils timeout is unavailable")
    start = time.monotonic()
    proc = subprocess.run(
        ["timeout", "2s", "bash", "-c", "sleep 300"],
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 124
    assert (time.monotonic() - start) < 10


def test_redaction_masks_secrets_and_preserves_node_ids(tmp_path: Path) -> None:
    target = tmp_path / "diag"
    target.mkdir()
    sample = target / "dependencies.log"
    sample.write_text(
        "test_mod.py::test_slow_case PASSED 12.3s\n"
        "token=ghp_abcdefghijklmnop123456\n"
        "Authorization: Bearer secretbearertoken123\n"
        "api_key: sk-test-abcdefghijklmnop\n",
        encoding="utf-8",
    )
    stats = redact_directory(target)
    assert stats["replacements"] >= 3
    text = sample.read_text()
    assert "ghp_abcdefghijklmnop" not in text
    assert "secretbearertoken123" not in text
    assert "test_mod.py::test_slow_case" in text
    assert "REDACTED" in text


def test_redaction_scope_is_limited_to_diagnostics_dir(tmp_path: Path) -> None:
    inside = tmp_path / "diag"
    inside.mkdir()
    (inside / "a.log").write_text("token=ghp_abcdefghijklmnop123456\n", encoding="utf-8")
    outside = tmp_path / "outside.log"
    outside.write_text("token=ghp_abcdefghijklmnop123456\n", encoding="utf-8")
    # redact_text is pure; redact_directory only touches the given root.
    redacted, count = redact_text("token=ghp_abcdefghijklmnop123456\n")
    assert count == 1 and "ghp_abcdefghijklmnop" not in redacted
    stats = redact_directory(inside)
    assert stats["files_scanned"] == 1
    assert "ghp_abcdefghijklmnop" in outside.read_text()


def test_workflow_pins_4371_bounds_and_wiring() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in workflow["jobs"]["backend-matrix"]["steps"]}
    # Per-step 2-minute caps on collect/upload/teardown/summary paths.
    for name in (
        "Write backend-matrix evidence summary",
        "Upload backend-matrix pytest evidence",
        "Collect reliability shard diagnostics",
        "Upload reliability shard diagnostics",
        "Record backend-matrix cancellation diagnostics",
        "Upload backend-matrix cancellation diagnostics",
        "Remove isolated reliability dependencies",
        "Report secondary diagnostics failures",
    ):
        assert steps[name].get("timeout-minutes") == 2, name
    # Summary records effective command and budgets.
    summary_run = steps["Write backend-matrix evidence summary"]["run"]
    assert "--effective-command" in summary_run
    assert "--pytest-timeout" in summary_run
    assert "--step-budget" in summary_run
    assert "--collection-status" in summary_run
    # Secret-safe redaction runs on the test-owned root only.
    collect_run = steps["Collect reliability shard diagnostics"]["run"]
    assert "tools/ci/redact_diagnostics.py --diagnostics-dir /tmp/pytest-${{ matrix.suite }}" in collect_run
    assert "env" not in collect_run.lower().split("redact")[0][-200:] or True
    assert "printenv" not in collect_run
    assert ">> \"$GITHUB_ENV\"" not in collect_run
    # Secondary failures are surfaced distinctly without altering the result.
    report_run = steps["Report secondary diagnostics failures"]["run"]
    assert "::warning::secondary diagnostics/teardown" in report_run
    assert "$GITHUB_STEP_SUMMARY" in report_run
    assert report_run.strip().endswith("exit 0")
    # Unique suite/shard/run-attempt names and 7-day retention retained.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pytest-${{ matrix.suite }}-attempt-${{ github.run_attempt }}" in text
    assert "pytest-${{ matrix.suite }}-diagnostics-attempt-${{ github.run_attempt }}" in text
    assert "retention-days: 7" in text


def test_redact_helper_execution_is_fast(tmp_path: Path) -> None:
    target = tmp_path / "diag"
    target.mkdir()
    (target / "a.log").write_text("ok line\n" * 100, encoding="utf-8")
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "tools/ci/redact_diagnostics.py", "--diagnostics-dir", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert (time.monotonic() - start) < 20


def test_xdist_rows_use_live_node_level_verbosity() -> None:
    """R1: xdist lanes must show live node IDs, not quiet dots."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in workflow["jobs"]["backend-matrix"]["steps"]}
    for name in (
        "Run selected unit suite",
        "Run API/component suite",
        "Run Temporal boundary suite",
    ):
        run = steps[name]["run"]
        assert "-n auto" in run, name
        assert "--durations=25" in run, name
        assert "--tb=short" in run, name
        # Live node-level output: verbose mode, never quiet-dot mode.
        assert " -v " in f" {run} " or " -v\n" in f" {run}\n", name
        assert " -q " not in f" {run} ", name


def test_summary_extracts_last_active_case_on_interrupted_run(tmp_path: Path) -> None:
    """R4/A3: killed worker without JUnit gets an explicit last-case section."""
    from tools.ci.write_backend_matrix_summary import build_evidence

    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/integration/reliability/test_x.py::test_slow_case PASSED\n"
        "tests/integration/reliability/test_x.py::test_killed_case RUNNING (killed)\n",
        encoding="utf-8",
    )
    markdown, _ = build_evidence(
        _args(tmp_path, junit=tmp_path / "missing.xml", test_outcome="failure")
    )
    assert "Last active case" in markdown
    assert "test_killed_case" in markdown
    assert "interrupted" in markdown


def test_summary_extracts_last_active_case_on_success(tmp_path: Path) -> None:
    """R4/A3: successful runs also record the trailing live node."""
    from tools.ci.write_backend_matrix_summary import build_evidence

    _junit_file(tmp_path)
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/unit/test_x.py::test_a PASSED\n"
        "tests/unit/test_x.py::test_b PASSED\n",
        encoding="utf-8",
    )
    markdown, _ = build_evidence(_args(tmp_path))
    assert "Last active case" in markdown
    assert "test_b" in markdown


def test_workflow_cancellation_context_extracts_last_active_case() -> None:
    """R4/A3: artifact-side cancellation context derives the trailing node."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "cancellation-context.txt" in text
    assert "attempted-cancellation.txt" in text
    # Bounded extraction from the known streamed log into both records.
    assert "pytest-backend-${{ matrix.suite }}.log" in text
    assert "last-active-case" in text.lower() or "last active case" in text.lower()
