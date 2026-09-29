"""Current-run gate evidence for tactics-test (MoonLadderStudios/MoonMind#4277).

Runs the actual portable entrypoint with a disposable repository and stubbed
``moonmind``/``docker`` CLIs. A zero-test run, a failed automation result
behind a zero exit code, and an early failure that leaves an earlier
``latest/gate.json`` in place must never be credited as current success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (
    REPO_ROOT
    / ".agents"
    / "skills"
    / "tactics-test"
    / "scripts"
    / "run_dood_unreal_tactics.sh"
)
_MANAGED_MARKERS = ("MOONMIND_AGENT_RUN_ID", "MOONMIND_RUNTIME_ID", "MOONMIND_URL")
_PRIOR_PASS = '{"status":"PASS","resultsDir":"prior-verified-run"}\n'
_NO_TESTS = "LogAutomationCommandLine: Display: No automation tests matched 'Tactics.Unit.Missing'"
_FAILED = (
    "LogAutomationController: Error: Test Completed. Result={Fail} "
    "Name={PlayerReadyNotification} Path={Tactics.Unit.PlayerReadyNotification}"
)
_PASSED = (
    "LogAutomationController: Display: Test Completed. Result={Success} "
    "Name={PlayerReadyNotification} Path={Tactics.Unit.PlayerReadyNotification}"
)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment without bash
        pytest.skip("bash is not available")
    return bash


def _stub(bin_dir: Path, name: str, output: str, *, exit_code: int = 0) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(
        f"#!{_bash()}\ncat <<'OUT'\n{output}\nOUT\nexit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _system_tools_without_docker(tmp_path: Path) -> Path:
    """System tools minus ``docker``, so hosts that ship one stay hermetic."""

    tools = tmp_path / "system-tools"
    tools.mkdir()
    for directory in (Path("/usr/bin"), Path("/bin")):
        for tool in directory.iterdir():
            target = tools / tool.name
            if tool.name != "docker" and not target.exists():
                target.symlink_to(tool)
    return tools


def _repo(tmp_path: Path, results_subdir: str) -> tuple[Path, Path]:
    repo = tmp_path / "tactics-repo"
    latest = repo / results_subdir / "latest"
    latest.mkdir(parents=True)
    (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")
    gate = latest / "gate.json"
    gate.write_text(_PRIOR_PASS, encoding="utf-8")
    return repo, gate


def _run(
    tmp_path: Path, repo: Path, *args: str, bin_dir: Path, managed: bool
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _MANAGED_MARKERS
    }
    env["PATH"] = f"{bin_dir}{os.pathsep}{_system_tools_without_docker(tmp_path)}"
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    if managed:
        env.update(
            {
                "MOONMIND_AGENT_RUN_ID": "run-1",
                "MOONMIND_RUNTIME_ID": "runtime-1",
                "MOONMIND_URL": "http://api:8000",
            }
        )
    return subprocess.run(
        [_bash(), str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )


def _managed_test_phase(tmp_path: Path, log_line: str) -> tuple[int, dict]:
    repo, gate = _repo(tmp_path, ".artifacts/moonmind-unreal-tactics")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(shutil.which("python3") or "/usr/bin/python3")
    _stub(
        bin_dir,
        "moonmind",
        f"[stdout] {log_line}\ncontainer job job-1: succeeded (exitCode=0)",
    )
    completed = _run(
        tmp_path, repo, "--phase", "test", bin_dir=bin_dir, managed=True
    )
    return completed.returncode, json.loads(gate.read_text(encoding="utf-8"))


def test_managed_zero_test_run_is_not_test_success(tmp_path: Path) -> None:
    code, gate = _managed_test_phase(tmp_path, _NO_TESTS)

    assert gate["status"] == "FAIL"
    assert gate["testStatus"] == "no_tests"
    assert code != 0


def test_managed_failed_automation_result_is_not_success(tmp_path: Path) -> None:
    code, gate = _managed_test_phase(tmp_path, _FAILED)

    assert (gate["status"], gate["testStatus"]) == ("FAIL", "fail")
    assert code != 0


def test_managed_executed_passing_test_is_current_success(tmp_path: Path) -> None:
    code, gate = _managed_test_phase(tmp_path, _PASSED)

    assert (gate["status"], gate["testStatus"]) == ("PASS", "pass")
    assert gate["resultsDir"] != "prior-verified-run"
    assert code == 0


def test_managed_early_failure_does_not_leave_prior_pass_gate(tmp_path: Path) -> None:
    repo, gate = _repo(tmp_path, ".artifacts/moonmind-unreal-tactics")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(shutil.which("python3") or "/usr/bin/python3")

    completed = _run(tmp_path, repo, bin_dir=bin_dir, managed=True)

    assert completed.returncode != 0
    current = json.loads(gate.read_text(encoding="utf-8"))
    assert current["status"] != "PASS"
    assert current["resultsDir"] != "prior-verified-run"


def test_partial_managed_markers_stop_before_any_runner(tmp_path: Path) -> None:
    repo, gate = _repo(tmp_path, ".artifacts/dood-unreal-tactics")
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "docker", "unexpected docker call", exit_code=0)
    env_marker = {"MOONMIND_URL": "http://api:8000"}

    completed = subprocess.run(
        [_bash(), str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(tmp_path),
            **env_marker,
        },
        timeout=120,
    )

    assert completed.returncode == 2
    assert "MOONMIND_AGENT_RUN_ID" in completed.stderr
    assert gate.read_text(encoding="utf-8") == _PRIOR_PASS


def test_standalone_missing_docker_does_not_leave_prior_pass_gate(
    tmp_path: Path,
) -> None:
    repo, gate = _repo(tmp_path, ".artifacts/dood-unreal-tactics")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    completed = _run(tmp_path, repo, bin_dir=bin_dir, managed=False)

    assert completed.returncode != 0
    assert json.loads(gate.read_text(encoding="utf-8"))["status"] == "FAIL"


def test_standalone_zero_test_run_is_not_test_success(tmp_path: Path) -> None:
    repo, gate = _repo(tmp_path, ".artifacts/dood-unreal-tactics")
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "docker", _NO_TESTS)

    completed = _run(
        tmp_path, repo, "--phase", "test", bin_dir=bin_dir, managed=False
    )

    current = json.loads(gate.read_text(encoding="utf-8"))
    assert (current["status"], current["testStatus"]) == ("FAIL", "no_tests")
    assert completed.returncode != 0
