"""Trustworthy dry-run terminal evidence for tactics-test (#4277).

Executable regression coverage for MoonLadderStudios/MoonMind#4277: a
``--dry-run`` preview of the portable Tactics build/test entrypoint is
explicitly non-mutating. It reports ``status="SKIPPED"`` as a stdout preview
only, preserves any prior verified gate artifact, and creates no new
timestamped results directory. This exercises the actual shell entrypoint
with a disposable repository and a stubbed Docker CLI (no live Unreal
toolchain, daemon, or deployment mutation required).
"""

from __future__ import annotations

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


def test_tactics_dry_run_is_non_mutating_and_reports_skipped_preview(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment without bash
        pytest.skip("bash is not available")
    assert SCRIPT.is_file(), f"missing entrypoint: {SCRIPT}"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_stub = fake_bin / "docker"
    docker_stub.write_text(
        f"#!{bash}\n"
        'printf "docker %s\\n" "$*" >> "$COMMAND_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)

    repo = tmp_path / "tactics-repo"
    latest = repo / ".artifacts" / "dood-unreal-tactics" / "latest"
    latest.mkdir(parents=True)
    (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")
    gate = latest / "gate.json"
    prior_gate = '{"status":"PASS","resultsDir":"prior-verified-run"}\n'
    gate.write_text(prior_gate, encoding="utf-8")
    before_entries = sorted(p.parent.relative_to(repo) for p in repo.rglob("*"))

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "COMMAND_LOG": str(tmp_path / "commands.log"),
    }
    # The entrypoint delegates to the managed Python path when MoonMind
    # session identity is present; this regression targets the portable
    # direct-Docker fallback, so run without session identity.
    env.pop("MOONMIND_AGENT_RUN_ID", None)
    env.pop("MOONMIND_RUNTIME_ID", None)
    env.pop("MOONMIND_URL", None)

    completed = subprocess.run(
        [bash, str(SCRIPT), "--repo", str(repo), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert 'status="SKIPPED"' in completed.stdout
    assert "no gate artifact written" in completed.stdout
    # Prior verified evidence survives the preview unchanged.
    assert gate.read_text(encoding="utf-8") == prior_gate
    # No timestamped results directory is created by a preview.
    after_entries = sorted(p.parent.relative_to(repo) for p in repo.rglob("*"))
    assert after_entries == before_entries
