"""Executable remediation slice for MoonLadderStudios/MoonMind#4264.

Executable regression coverage for the trustworthy terminal outcomes
sub-slice (#4277) without asserting documentation wording, headings,
structure, counts, or required phrases (prohibited by AGENTS.md):

- the managed Tactics runner ``--dry-run`` preview is non-mutating: it
  reports ``status="SKIPPED"`` on stdout only, preserves any prior verified
  gate artifact, and creates no timestamped results directory (covers the
  default managed path where the shell entrypoint execs the managed runner);
- the managed gate artifact is phase-aware: a ``--phase build`` PASS gate
  names only ``buildLog`` and a ``--phase test`` PASS gate names only
  ``testLog``, so a verifier never rejects a successful single-phase run
  for a skipped phase's absent log file.

Documentation wording itself is reviewed, not unit tested.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TACTICS_SCRIPT_DIR = (
    REPO_ROOT / ".agents" / "skills" / "tactics-test" / "scripts"
)
MANAGED_RUNNER = TACTICS_SCRIPT_DIR / "run_moonmind_unreal_tactics.py"


def _load_managed_runner():
    spec = importlib.util.spec_from_file_location(
        "run_moonmind_unreal_tactics_under_test", MANAGED_RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_repo(tmp_path: Path, *, subdir: str) -> tuple[Path, Path, str]:
    repo = tmp_path / "tactics-repo"
    latest = repo / ".artifacts" / subdir / "latest"
    latest.mkdir(parents=True)
    (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")
    gate = latest / "gate.json"
    prior_gate = '{"status":"PASS","resultsDir":"prior-verified-run"}\n'
    gate.write_text(prior_gate, encoding="utf-8")
    return repo, gate, prior_gate


def test_managed_dry_run_is_non_mutating_and_reports_skipped_preview(
    tmp_path: Path,
) -> None:
    assert MANAGED_RUNNER.is_file(), f"missing runner: {MANAGED_RUNNER}"
    repo, gate, prior_gate = _init_repo(
        tmp_path, subdir="moonmind-unreal-tactics"
    )
    before = sorted(p.relative_to(repo) for p in repo.rglob("*"))

    completed = subprocess.run(
        [sys.executable, str(MANAGED_RUNNER), "--repo", str(repo), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert 'status="SKIPPED"' in completed.stdout
    assert "no gate artifact written" in completed.stdout
    assert gate.read_text(encoding="utf-8") == prior_gate
    after = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    assert after == before


def test_managed_gate_omits_skipped_phase_logs(tmp_path: Path) -> None:
    module = _load_managed_runner()

    for phase, expected_log, absent_log in (
        ("build", "buildLog", "testLog"),
        ("test", "testLog", "buildLog"),
    ):
        repo = tmp_path / f"repo-{phase}"
        repo.mkdir(parents=True)
        (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")

        def _fake_run_job(
            spec, *, request_id, log_path, dry_run=False
        ):  # noqa: ANN001, ANN202
            assert not dry_run
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text("ok\n", encoding="utf-8")
            return 0

        module._run_job = _fake_run_job  # type: ignore[attr-defined]
        argv = [
            "run_moonmind_unreal_tactics.py",
            "--repo",
            str(repo),
            "--phase",
            phase,
        ]
        old_argv = sys.argv
        sys.argv = argv
        try:
            assert module.main() == 0
        finally:
            sys.argv = old_argv

        gate_path = repo / ".artifacts" / "moonmind-unreal-tactics" / "latest" / "gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        assert gate["status"] == "PASS"
        assert gate["phase"] == phase
        assert expected_log in gate, f"{phase}: missing {expected_log}"
        assert absent_log not in gate, f"{phase}: must omit {absent_log}"
        assert Path(gate[expected_log]).is_file()
