from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SCRIPT = REPO_ROOT / "services/temporal/scripts/bootstrap-namespace.sh"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def test_namespace_bootstrap_is_idempotent_and_storage_cap_aware(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    temporal_stub = """#!/usr/bin/env sh
set -eu
state_dir="${FAKE_TEMPORAL_STATE_DIR:?}"
cmd="$*"
printf '%s\\n' "$cmd" >> "${state_dir}/calls.log"
case "$cmd" in
  *"cluster health"*)
    exit 0
    ;;
  *"namespace describe"*)
    if [ -f "${state_dir}/namespace.exists" ]; then
      exit 0
    fi
    exit 1
    ;;
  *"namespace create"*)
    touch "${state_dir}/namespace.exists"
    exit 0
    ;;
  *"namespace update"*)
    exit 0
    ;;
esac
exit 0
"""
    _write_executable(fake_bin / "temporal", temporal_stub)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "moonmind"
    env.pop("TEMPORAL_NAMESPACE_RETENTION_DAYS", None)
    env["TEMPORAL_RETENTION_MAX_STORAGE_GB"] = "24"
    env["TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY"] = "6"

    first_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    assert (
        "Derived namespace retention 4 day(s) from storage cap 24 GB at 6 GB/day."
        in first_run.stdout
    )
    assert "Namespace does not exist; creating" in first_run.stdout
    assert "Storage cap guardrail is 24 GB with retention 4 day(s)." in first_run.stdout

    second_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second_run.returncode == 0, second_run.stderr
    assert "Namespace exists; updating retention" in second_run.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert "--retention 96h" in calls
    assert "namespace create" in calls
    assert "namespace update" in calls


def test_namespace_bootstrap_skips_create_for_default_namespace(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    temporal_stub = """#!/usr/bin/env sh
set -eu
state_dir="${FAKE_TEMPORAL_STATE_DIR:?}"
cmd="$*"
printf '%s\\n' "$cmd" >> "${state_dir}/calls.log"
case "$cmd" in
  *"cluster health"*)
    echo "health"
    ;;
  *"namespace describe"*)
    echo "describe"
    ;;
  *"namespace create"*)
    echo "create"
    ;;
  *"namespace update"*)
    echo "update"
    ;;
  *"search-attribute list"*)
    echo "search list"
    ;;
  *"search-attribute create"*)
    touch "${state_dir}/search.exists"
    echo "search create"
    ;;
esac
"""
    _write_executable(fake_bin / "temporal", temporal_stub)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "default"
    env.pop("TEMPORAL_NAMESPACE_RETENTION_DAYS", None)

    first_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    assert (
        "Built-in default namespace detected. Skipping namespace create/update and retention policy."
        in first_run.stdout
    )
    assert "Namespace does not exist; creating" not in first_run.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert "namespace create" not in calls
    assert "namespace update" not in calls
    assert "search-attribute create" in calls
