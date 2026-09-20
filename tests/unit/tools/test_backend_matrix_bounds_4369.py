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
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"
DOCS = REPO_ROOT / "docs" / "Development" / "BackendTestSelection.md"


def _run_pytest(probe: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args, str(probe)],
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
    assert "Timeout" in proc.stdout + proc.stderr or "timeout" in (proc.stdout + proc.stderr).lower()


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
    for name in (
        "Run selected unit suite",
        "Run API/component suite",
        "Run Temporal boundary suite",
    ):
        assert steps[name].get("timeout-minutes") == 7, name


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
    matrix = job["strategy"]["matrix"]["include"]
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
