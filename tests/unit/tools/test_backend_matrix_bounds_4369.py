"""MoonLadderStudios/MoonMind#4369: bounded backend-matrix execution.

Fast/API/Temporal lanes get tight per-test pytest-timeout defaults with
explicit 7-minute native test-step bounds and 15-minute per-row job bounds;
reliability keeps its 150s/300s per-test and 12-minute step bounds under a
reduced 20-minute per-row job bound (down from the blanket 30). Ordinary
validation stops promptly with ``--maxfail=1`` while schedules keep
collecting diagnostics. Small real-subprocess reproductions prove the
installed pytest-timeout hook interrupts a stuck body, async wait, and
setup, that teardown still runs after a failure, and that the tee/PIPESTATUS
hook preserves the original exit code.

Remaining gaps: the whole reliability dependency start has a native bound,
faulthandler stack dumps precede each terminating per-test timeout (serial
and xdist), the reliability shell deadline interrupts pytest so teardown and
JUnit survive and escalates to a hard kill, and the evidence summary reports
cooperative deadline stops, hard kills and unrecorded exits distinctly.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import yaml

from tools.ci.write_backend_matrix_summary import build_evidence
from tools.select_test_suites import select_suites

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"
DOCS = REPO_ROOT / "docs" / "Development" / "BackendTestSelection.md"


def _run_pytest(
    probe: Path, *args: str, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *args,
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


def _write(tmp_path: Path, name: str, body: str) -> Path:
    probe = tmp_path / name
    probe.write_text(body, encoding="utf-8")
    return probe


def test_stuck_body_interrupted_by_installed_timeout(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "test_4369_stuck_body.py",
        "import time\n\ndef test_stuck_body():\n    time.sleep(30)\n",
    )
    start = time.monotonic()
    proc = _run_pytest(probe, "--timeout=2", timeout=60)
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert elapsed < 25
    assert (
        "Timeout" in proc.stdout + proc.stderr
        or "timeout" in (proc.stdout + proc.stderr).lower()
    )


def test_stuck_async_wait_interrupted_by_installed_timeout(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "test_4369_stuck_async.py",
        "import asyncio\nimport pytest\n\n"
        "@pytest.mark.asyncio\nasync def test_stuck_async_wait():\n"
        "    await asyncio.sleep(30)\n",
    )
    start = time.monotonic()
    proc = _run_pytest(probe, "--timeout=2", timeout=60)
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert elapsed < 25


def test_stuck_setup_interrupted_by_installed_timeout(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "test_4369_stuck_setup.py",
        "import time\nimport pytest\n\n"
        "@pytest.fixture\ndef hanging_setup():\n    time.sleep(30)\n\n"
        "def test_uses_setup(hanging_setup):\n    pass\n",
    )
    start = time.monotonic()
    proc = _run_pytest(probe, "--timeout=2", timeout=60)
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert elapsed < 25


def test_teardown_runs_after_failure_and_failure_preserved(tmp_path: Path) -> None:
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = _write(
        tmp_path,
        "test_4369_teardown.py",
        "import os\nfrom pathlib import Path\nimport pytest\n\n"
        "@pytest.fixture\ndef teardown_marker():\n"
        "    yield\n"
        '    (Path(os.environ["PROBE_DIR_4369"]) / "teardown_ran.txt").write_text("tore down\\n")\n'
        "def test_fails_but_tears_down(teardown_marker):\n"
        "    assert False, 'original failure'\n",
    )
    env = dict(os.environ, PROBE_DIR_4369=str(probe_dir))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert proc.returncode != 0
    assert (probe_dir / "teardown_ran.txt").exists()
    assert "original failure" in proc.stdout + proc.stderr


def test_exit_code_preserved_through_tee_hook(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "test_4369_exit_code.py",
        "def test_always_fails():\n    assert False, 'boom'\n",
    )
    log = tmp_path / "probe.log"
    proc = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\nset +e\n"
            f"{sys.executable} -m pytest -q -p no:cacheprovider --timeout=2 "
            f"{probe} 2>&1 | tee {log}\n"
            "status=${PIPESTATUS[0]}\n"
            "exit $status",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0
    assert "boom" in log.read_text() or "failed" in log.read_text().lower()


def test_maxfail_stops_owning_invocation_promptly(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "test_4369_maxfail.py",
        "import time\n\ndef test_a_fails_fast():\n    assert False, 'first'\n\n"
        "def test_b_would_be_slow():\n    time.sleep(20)\n    assert False, 'second'\n",
    )
    start = time.monotonic()
    proc = _run_pytest(probe, "--timeout=25", "--maxfail=1", timeout=60)
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert elapsed < 20
    assert "stopping after 1 failure" in (proc.stdout + proc.stderr).lower()


def _workflow_steps() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {step["name"]: step for step in workflow["jobs"]["backend-matrix"]["steps"]}


def test_fast_lanes_use_tight_per_test_timeouts() -> None:
    steps = _workflow_steps()
    unit_run = steps["Run selected unit suite"]["run"]
    api_run = steps["Run API/component suite"]["run"]
    temporal_run = steps["Run Temporal boundary suite"]["run"]
    assert "--timeout 60" in unit_run
    assert "--timeout 120" in api_run
    assert "--timeout 120" in temporal_run
    for name, run in (
        ("Run selected unit suite", unit_run),
        ("Run API/component suite", api_run),
        ("Run Temporal boundary suite", temporal_run),
    ):
        assert "--timeout 600" not in run, name


def test_fast_lanes_have_explicit_step_bounds() -> None:
    steps = _workflow_steps()
    matrix = json.loads(select_suites([]).as_outputs()["backend_matrix"])["include"]
    by_suite = {row["suite"]: row for row in matrix}
    for name, suite, measured_seconds in (
        # Slowest observed unit-fast test step on main (433s) overran a
        # 7-minute bound; a step bound must leave headroom over it.
        ("Run selected unit suite", "unit-fast", 433),
        ("Run API/component suite", "api-component", 0),
        ("Run Temporal boundary suite", "temporal-boundary", 0),
    ):
        bound = steps[name].get("timeout-minutes")
        assert isinstance(bound, int), name
        assert bound * 60 >= measured_seconds * 1.2, name
        # Setup (~2-3 minutes) and bounded reporting (2-minute caps) fit
        # inside the row's job budget after a full-length test step.
        assert bound + 3 + 2 <= by_suite[suite]["job_minutes"], name


def test_ordinary_validation_uses_maxfail_but_schedules_collect() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--maxfail=1" in text
    for name in (
        "Run selected unit suite",
        "Run API/component suite",
        "Run Temporal boundary suite",
        "Run hermetic reliability shard",
    ):
        run = _workflow_steps()[name]["run"]
        assert "--maxfail=1" in run or "maxfail" in run, name
        # Schedule rows must be able to skip it to keep diagnostics.
        assert "schedule" in run, name


def test_per_row_job_bounds_replace_blanket_thirty_minutes() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["backend-matrix"]
    assert job.get("timeout-minutes") != 30
    matrix = json.loads(select_suites([]).as_outputs()["backend_matrix"])["include"]
    by_suite = {row["suite"]: row for row in matrix}
    assert by_suite["unit-fast"].get("job_minutes") == 15
    assert by_suite["api-component"].get("job_minutes") == 15
    assert by_suite["temporal-boundary"].get("job_minutes") == 15
    for shard in (
        "reliability-shard-1",
        "reliability-shard-2",
        "reliability-shard-3",
        "reliability-shard-4",
    ):
        # The heaviest duration-balanced partition holds ~500s of tests plus
        # collection/shutdown overhead, so reliability rows keep a larger
        # measured bound instead of a 480s-style aspirational cutoff.
        assert by_suite[shard].get("job_minutes") >= 12, shard
    assert "matrix.job_minutes" in WORKFLOW.read_text(encoding="utf-8")


def test_no_extra_timeout_framework_retry_or_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--session-timeout" not in text
    assert "--reruns" not in text
    assert "--maxfail=1" in text  # prompt stop, not a retry loop
    steps = _workflow_steps()
    for name in (
        "Run selected unit suite",
        "Run API/component suite",
        "Run Temporal boundary suite",
        "Run hermetic reliability shard",
    ):
        assert steps[name].get("continue-on-error") is None, name


# ---------------------------------------------------------------------------
# Remaining #4369 gaps: bounded dependency start, stack dumps before the
# terminating per-test deadline, and graceful-versus-hard shell deadlines.
# ---------------------------------------------------------------------------


FAST_RUN_STEPS = (
    "Run selected unit suite",
    "Run API/component suite",
    "Run Temporal boundary suite",
)


def test_reliability_dependency_start_has_finite_outer_bound() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    rows = json.loads(select_suites([]).as_outputs()["backend_matrix"])["include"]
    reliability_job_minutes = min(
        row["job_minutes"] for row in rows if row["suite"].startswith("reliability-")
    )
    start = _workflow_steps()["Start isolated reliability dependencies"]
    bound = start.get("timeout-minutes")
    # The whole step (pull, up --wait and address inspection) gets a native
    # outer bound; the readiness wait alone does not bound the pull.
    assert isinstance(bound, int) and bound > 0
    assert bound < reliability_job_minutes
    assert "--wait-timeout" in start["run"]


def test_fast_lane_stack_dumps_precede_terminating_timeouts() -> None:
    steps = _workflow_steps()
    for name in FAST_RUN_STEPS:
        run = steps[name]["run"]
        dump = re.search(r"-o faulthandler_timeout=(\d+)", run)
        deadline = re.search(r"--timeout (\d+)", run)
        assert dump is not None, name
        assert deadline is not None, name
        assert 0 < int(dump.group(1)) < int(deadline.group(1)), name


def test_reliability_stack_dumps_precede_terminating_timeouts() -> None:
    run = _workflow_steps()["Run hermetic reliability shard"]["run"]
    deadlines = [int(value) for value in re.findall(r"pytest_timeout=(\d+)", run)]
    dumps = [int(value) for value in re.findall(r"dump_timeout=(\d+)", run)]
    # One pair for ordinary runs and one for schedules.
    assert len(deadlines) == 2
    assert len(dumps) == len(deadlines)
    for dump, deadline in zip(dumps, deadlines):
        assert 0 < dump < deadline
    assert '-o faulthandler_timeout="$dump_timeout"' in run


def _probe_stuck_test(tmp_path: Path, name: str) -> Path:
    return _write(
        tmp_path,
        name,
        "import time\n\ndef stuck_in_helper():\n    time.sleep(30)\n\n"
        "def test_stuck():\n    stuck_in_helper()\n",
    )


def test_stack_dump_precedes_timeout_in_serial_invocation(tmp_path: Path) -> None:
    probe = _probe_stuck_test(tmp_path, "test_4369_dump_serial.py")
    start = time.monotonic()
    proc = _run_pytest(probe, "-o", "faulthandler_timeout=1", "--timeout=4", timeout=60)
    elapsed = time.monotonic() - start
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert elapsed < 25
    # faulthandler's stderr dump names the stuck frame; pytest-timeout then
    # terminates the test at its own later deadline.
    assert "Timeout (0:00:01)!" in proc.stderr
    dump = proc.stderr[proc.stderr.index("Timeout (0:00:01)!") :]
    assert "stuck_in_helper" in dump
    assert "Failed: Timeout" in output


def test_stack_dump_precedes_timeout_under_xdist(tmp_path: Path) -> None:
    probe = _probe_stuck_test(tmp_path, "test_4369_dump_xdist.py")
    start = time.monotonic()
    proc = _run_pytest(
        probe, "-n", "2", "-o", "faulthandler_timeout=1", "--timeout=4", timeout=90
    )
    elapsed = time.monotonic() - start
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert elapsed < 60
    # Worker stderr reaches the combined log that the workflow tees.
    assert "Timeout (0:00:01)!" in output
    assert "stuck_in_helper" in output


def _reliability_deadline_options() -> tuple[str, str]:
    run = _workflow_steps()["Run hermetic reliability shard"]["run"]
    invocation = re.search(
        r'timeout ([^\n]*?)"\$\{step_budget\}s" python -m pytest', run
    )
    assert (
        invocation is not None
    ), "reliability pytest must run under the shell deadline"
    options = invocation.group(1)
    signal_match = re.search(r"--signal=(\w+)", options)
    kill_match = re.search(r"--kill-after=(\d+)s", options)
    assert signal_match is not None, options
    assert kill_match is not None, options
    return signal_match.group(1), kill_match.group(1)


def _run_under_deadline(
    command: list[str],
    *,
    signal_name: str,
    kill_after: str,
    budget: str,
    log: Path,
    env=None,
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` the way the reliability step does: deadline, tee, PIPESTATUS."""
    quoted = " ".join(shlex.quote(part) for part in command)
    script = (
        "set -euo pipefail\nset +e\n"
        f"timeout --signal={signal_name} --kill-after={kill_after} {budget} {quoted} "
        f"2>&1 | tee {shlex.quote(str(log))}\n"
        "status=${PIPESTATUS[0]}\n"
        "exit $status\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )


def test_reliability_deadline_grace_fits_inside_native_step_bound() -> None:
    _, kill_after = _reliability_deadline_options()
    step = _workflow_steps()["Run hermetic reliability shard"]
    budgets = [int(value) for value in re.findall(r"step_budget=(\d+)", step["run"])]
    assert budgets
    # The shell deadline plus its kill grace stays inside the native step
    # bound, so the shell records an exit status before Actions kills it.
    assert max(budgets) + int(kill_after) < step["timeout-minutes"] * 60


def test_reliability_deadline_hard_kills_a_process_ignoring_interrupts(
    tmp_path: Path,
) -> None:
    signal_name, _ = _reliability_deadline_options()
    start = time.monotonic()
    proc = _run_under_deadline(
        [
            sys.executable,
            "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n",
        ],
        signal_name=signal_name,
        kill_after="1s",
        budget="1s",
        log=tmp_path / "ignored.log",
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 137
    assert elapsed < 10


def test_reliability_deadline_interrupt_keeps_junit_teardown_and_stack(
    tmp_path: Path,
) -> None:
    signal_name, _ = _reliability_deadline_options()
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = _write(
        tmp_path,
        "test_4369_deadline.py",
        "import os, time\nfrom pathlib import Path\nimport pytest\n\n"
        "@pytest.fixture\ndef owned_stack():\n"
        "    yield\n"
        '    (Path(os.environ["PROBE_DIR_4369"]) / "teardown_ran.txt").write_text("down\\n")\n\n'
        "def stuck_in_journey():\n    time.sleep(30)\n\n"
        "def test_stuck_journey(owned_stack):\n    stuck_in_journey()\n",
    )
    junit = tmp_path / "junit.xml"
    log = tmp_path / "deadline.log"
    env = dict(os.environ, PROBE_DIR_4369=str(probe_dir))
    start = time.monotonic()
    proc = _run_under_deadline(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            f"--junitxml={junit}",
            str(probe),
        ],
        signal_name=signal_name,
        kill_after="10s",
        budget="3s",
        log=log,
        env=env,
    )
    elapsed = time.monotonic() - start
    output = log.read_text(encoding="utf-8")
    # Cooperative deadline: pytest exits inside the grace period, so the
    # shell sees 124 (not a 137 hard kill) and the run is unsuccessful.
    assert proc.returncode == 124
    assert elapsed < 12
    assert junit.exists()
    assert (probe_dir / "teardown_ran.txt").exists()
    # The teed log names the interrupted location inside the stuck journey.
    assert "KeyboardInterrupt" in output
    assert "test_4369_deadline.py:11" in output


def test_every_backend_row_records_its_test_exit_status() -> None:
    steps = _workflow_steps()
    for name in (*FAST_RUN_STEPS, "Run hermetic reliability shard"):
        run = steps[name]["run"]
        assert "status=${PIPESTATUS[0]}" in run, name
        assert "-test-status.txt" in run, name
    summary_run = steps["Write backend-matrix evidence summary"]["run"]
    assert "--test-status-file" in summary_run


def _summary_for_status(tmp_path: Path, status: str | None, outcome: str) -> str:
    import argparse

    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/integration/reliability/test_x.py::test_journey\n", encoding="utf-8"
    )
    status_file = tmp_path / "status.txt"
    if status is not None:
        status_file.write_text(f"{status}\n", encoding="utf-8")
    args = argparse.Namespace(
        suite="reliability-shard-1",
        shard="1",
        junit=str(tmp_path / "missing-junit.xml"),
        log=str(log),
        slowest=str(tmp_path / "slowest.txt"),
        durations_snapshot=str(tmp_path / "durations.json"),
        summary=str(tmp_path / "summary.md"),
        revision="abc123",
        run_id="1",
        attempt="1",
        selected="true",
        test_outcome=outcome,
        test_seconds_file="",
        test_status_file=str(status_file),
        effective_command="python -m pytest tests/integration/reliability",
        pytest_timeout="150s",
        step_budget="600s",
        collection_status="",
    )
    markdown, _ = build_evidence(args)
    return markdown


def test_summary_distinguishes_cooperative_deadline_hard_kill_and_unavailable(
    tmp_path: Path,
) -> None:
    cooperative = _summary_for_status(tmp_path / "a", "124", "failure")
    hard_kill = _summary_for_status(tmp_path / "b", "137", "failure")
    unrecorded = _summary_for_status(tmp_path / "c", None, "failure")
    ordinary = _summary_for_status(tmp_path / "d", "1", "failure")

    def termination(markdown: str) -> str:
        line = next(
            line for line in markdown.splitlines() if line.startswith("- Termination:")
        )
        return line.lower()

    assert "cooperative" in termination(cooperative)
    assert "hard kill" in termination(hard_kill)
    assert "unavailable" in termination(unrecorded)
    assert "exit status 1" in termination(ordinary)
    labels = {termination(m) for m in (cooperative, hard_kill, unrecorded, ordinary)}
    assert len(labels) == 4
    # None of them is reported as a pass or as zero tests.
    for markdown in (cooperative, hard_kill, unrecorded, ordinary):
        assert "Outcome: `passed" not in markdown
        assert "tests=`0`" not in markdown
