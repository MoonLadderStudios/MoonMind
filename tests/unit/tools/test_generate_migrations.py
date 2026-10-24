"""Exercise the migration helper without starting Docker or touching a database."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "tools/generate_migrations.sh"

CLI = """
import json, os, sys
from pathlib import Path

command = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ["MIGRATION_TEST_CALLS"]).open("a") as output:
    output.write(json.dumps({
        "command": command, "args": args,
        "database_password": os.environ.get("POSTGRES_PASSWORD"),
    }) + "\\n")
if command == "docker":
    if args[:1] == ["ps"] and os.environ.get("MIGRATION_TEST_RUNNING") == "1":
        print("postgres_alembic_gen")
    elif args[:3] == ["exec", "postgres_alembic_gen", "printenv"]:
        if os.environ.get("MIGRATION_TEST_CREDENTIAL_ERROR") == "1":
            raise SystemExit(1)
        print(os.environ.get("MIGRATION_TEST_DATABASE_PASSWORD", ""))
elif command == "openssl":
    raise SystemExit(1)
"""


def _run_helper(tmp_path: Path, *, existing: str | None = None, **settings: str):
    repo = tmp_path / "repo"
    scripts = repo / "tools"
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    migrations = repo / "api_service/migrations"
    migrations.mkdir(parents=True)
    (migrations / "alembic.ini").touch()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("docker", "poetry"):
        binary = binaries / name
        binary.write_text(f"#!{sys.executable}\n" + CLI)
        binary.chmod(0o755)
    if settings.pop("broken_random", ""):
        binary = binaries / "openssl"
        binary.write_text(f"#!{sys.executable}\n" + CLI)
        binary.chmod(0o755)
    calls_path = tmp_path / "calls.jsonl"
    env = {
        key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ
    }
    env.update(
        PATH=str(binaries) + os.pathsep + env["PATH"],
        MIGRATION_TEST_CALLS=str(calls_path),
        **settings,
    )
    if existing is not None:
        env.update(
            MIGRATION_TEST_RUNNING="1", MIGRATION_TEST_DATABASE_PASSWORD=existing
        )
    result = subprocess.run(
        ["bash", str(scripts / SCRIPT.name)],
        cwd=repo,
        env=env,
        input="add_field\ny\ny\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    return result, calls


def test_new_database_has_random_password_and_loopback_only_port(tmp_path: Path):
    generated = []
    for attempt in range(2):
        result, calls = _run_helper(tmp_path / str(attempt))
        assert result.returncode == 0, result.stderr
        run = next(
            call["args"]
            for call in calls
            if call["command"] == "docker" and call["args"][:1] == ["run"]
        )
        assert run[run.index("-p") + 1] == "127.0.0.1:5432:5432"
        password = next(
            arg.split("=", 1)[1] for arg in run if arg.startswith("POSTGRES_PASSWORD=")
        )
        assert len(password) >= 48
        assert password not in result.stdout + result.stderr
        migrations = [call for call in calls if call["command"] == "poetry"]
        assert len(migrations) == 2
        assert all(call["database_password"] == password for call in migrations)
        assert any(call["args"] == ["stop", "postgres_alembic_gen"] for call in calls)
        generated.append(password)
    assert generated[0] != generated[1]


def test_existing_database_reuses_its_configured_password(tmp_path: Path):
    password = secrets.token_hex(24)
    result, calls = _run_helper(tmp_path, existing=password)
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout + result.stderr
    assert all(
        call["args"][:1] not in (["run"], ["stop"], ["rm"])
        for call in calls
        if call["command"] == "docker"
    )
    migrations = [call for call in calls if call["command"] == "poetry"]
    assert len(migrations) == 2
    assert all(call["database_password"] == password for call in migrations)


@pytest.mark.parametrize("read_error", ["0", "1"])
def test_missing_existing_password_stops_before_migration(
    tmp_path: Path, read_error: str
):
    result, calls = _run_helper(
        tmp_path, existing="", MIGRATION_TEST_CREDENTIAL_ERROR=read_error
    )
    assert result.returncode != 0
    assert "configured PostgreSQL password" in result.stdout + result.stderr
    assert not any(call["command"] == "poetry" for call in calls)
    assert not any(call["args"][:1] in (["stop"], ["rm"]) for call in calls)


def test_failed_password_generation_does_not_start_database(tmp_path: Path):
    result, calls = _run_helper(tmp_path, broken_random="1")
    assert result.returncode != 0
    assert not any(call["command"] == "poetry" for call in calls)
    assert not any(call["args"][:1] == ["run"] for call in calls)
