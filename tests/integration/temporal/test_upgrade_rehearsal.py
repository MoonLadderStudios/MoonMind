from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REHEARSAL_SCRIPT = (
    REPO_ROOT / "services/temporal/scripts/rehearse-visibility-schema-upgrade.sh"
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)

def _base_env(path_prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{path_prefix}:{env['PATH']}"
    env["TEMPORAL_POSTGRES_HOST"] = "temporal-db"
    env["TEMPORAL_POSTGRES_PORT"] = "5432"
    env["TEMPORAL_POSTGRES_USER"] = "temporal"
    env["TEMPORAL_POSTGRES_PASSWORD"] = "temporal"
    env["TEMPORAL_VISIBILITY_DB"] = "temporal_visibility"
    env["TEMPORAL_VISIBILITY_SCHEMA_DIR"] = "/tmp/fake-visibility-schema"
    return env

def test_rehearsal_requires_shard_ack_for_single_shard(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    env = _base_env(fake_bin)
    env["TEMPORAL_REHEARSAL_DRY_RUN"] = "1"
    env["TEMPORAL_NUM_HISTORY_SHARDS"] = "1"
    env.pop("TEMPORAL_SHARD_DECISION_ACK", None)

    denied = subprocess.run(
        ["sh", str(REHEARSAL_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert denied.returncode == 2
    assert "Shard decision gate is not acknowledged" in denied.stdout

    env["TEMPORAL_SHARD_DECISION_ACK"] = "acknowledged"
    allowed = subprocess.run(
        ["sh", str(REHEARSAL_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stderr
    assert "Dry-run mode enabled" in allowed.stdout

def test_rehearsal_executes_visibility_schema_update_command(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _write_executable(
        fake_bin / "pg_isready",
        """#!/usr/bin/env sh
exit 0
""",
    )

    temporal_sql_tool_stub = """#!/usr/bin/env sh
set -eu
state_dir="${FAKE_REHEARSAL_STATE_DIR:?}"
printf '%s\\n' "$*" >> "${state_dir}/sql-tool-calls.log"
exit 0
"""
    _write_executable(fake_bin / "temporal-sql-tool", temporal_sql_tool_stub)

    env = _base_env(fake_bin)
    env["FAKE_REHEARSAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_NUM_HISTORY_SHARDS"] = "1"
    env["TEMPORAL_SHARD_DECISION_ACK"] = "acknowledged"
    env["TEMPORAL_REHEARSAL_DRY_RUN"] = "0"

    run = subprocess.run(
        ["sh", str(REHEARSAL_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "Visibility schema rehearsal completed successfully." in run.stdout

    calls = (state_dir / "sql-tool-calls.log").read_text(encoding="utf-8")
    assert "setup-schema -v 0.0" in calls
    assert "update-schema -d /tmp/fake-visibility-schema" in calls
