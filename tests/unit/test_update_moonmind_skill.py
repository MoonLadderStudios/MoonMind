from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = (
    ROOT
    / ".agents"
    / "skills"
    / "update-moonmind"
    / "scripts"
    / "run-update-moonmind.sh"
)
REPLAY_ROOT = (
    ROOT
    / "tests"
    / "integration"
    / "reliability"
    / "replays"
    / "skill-resolution-update-skew"
)


def _run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _modern_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("update-moonmind requires Bash")
    version = subprocess.run(
        [bash, "-c", 'printf "%s" "${BASH_VERSINFO[0]}"'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if int(version) < 4:
        pytest.skip("update-moonmind requires Bash 4 or newer")
    return bash


def _run_update_scenario(
    tmp_path: Path,
    *,
    changed_file: str,
    include_all_commands: bool = False,
    remove_agent_runtime_after_update: bool = False,
) -> list[str]:
    bash = _modern_bash()
    seed = tmp_path / "seed"
    remote = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    fake_bin = tmp_path / "bin"
    docker_log = tmp_path / "docker.log"

    seed.mkdir()
    _run_git("init", "-b", "main", cwd=seed)
    _run_git("config", "user.name", "MoonMind Test", cwd=seed)
    _run_git("config", "user.email", "moonmind-test@example.invalid", cwd=seed)
    source_file = seed / changed_file
    source_file.parent.mkdir(parents=True)
    source_file.write_text("VERSION = 1\n", encoding="utf-8")
    _run_git("add", changed_file, cwd=seed)
    _run_git("commit", "-m", "initial", cwd=seed)

    _run_git("init", "--bare", str(remote), cwd=tmp_path)
    _run_git("remote", "add", "origin", str(remote), cwd=seed)
    _run_git("push", "-u", "origin", "main", cwd=seed)
    _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
    _run_git("clone", str(remote), str(checkout), cwd=tmp_path)

    source_file.write_text("VERSION = 2\n", encoding="utf-8")
    _run_git("add", changed_file, cwd=seed)
    _run_git("commit", "-m", "update source", cwd=seed)
    _run_git("push", "origin", "main", cwd=seed)
    expected_head = _run_git("rev-parse", "HEAD", cwd=seed).stdout.strip()

    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"

if [[ "${1:-}" == "ps" ]]; then
  exit 0
fi
if [[ "${1:-}" != "compose" ]]; then
  exit 0
fi
shift

case "${1:-} ${2:-}" in
  "version ")
    exit 0
    ;;
  "config --services")
    if [[ "${REMOVE_AGENT_RUNTIME_AFTER_UPDATE:-false}" == "true" ]] \
      && [[ "$(git -C "$UPDATE_CHECKOUT" rev-parse HEAD)" == "$UPDATE_EXPECTED_HEAD" ]]; then
      printf '%s\\n' api postgres temporal-worker-deployment-control temporal-worker-workflow
    else
      printf '%s\\n' api postgres temporal-worker-agent-runtime temporal-worker-deployment-control temporal-worker-workflow
    fi
    ;;
  "config --format")
    if [[ "${REMOVE_AGENT_RUNTIME_AFTER_UPDATE:-false}" == "true" ]] \
      && [[ "$(git -C "$UPDATE_CHECKOUT" rev-parse HEAD)" == "$UPDATE_EXPECTED_HEAD" ]]; then
      printf '%s\\n' '{"services":{"api":{"image":"moonmind:test"},"postgres":{"image":"postgres:test"},"temporal-worker-deployment-control":{"image":"moonmind:test"},"temporal-worker-workflow":{"image":"moonmind:test"}}}'
    else
      printf '%s\\n' '{"services":{"api":{"image":"moonmind:test"},"postgres":{"image":"postgres:test"},"temporal-worker-agent-runtime":{"image":"moonmind:test"},"temporal-worker-deployment-control":{"image":"moonmind:test"},"temporal-worker-workflow":{"image":"moonmind:test"}}}'
    fi
    ;;
  "pull ")
    exit 0
    ;;
  "ps -a"|"ps -q")
    exit 0
    ;;
  "up -d")
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["DOCKER_LOG"] = str(docker_log)
    env["UPDATE_CHECKOUT"] = str(checkout)
    env["UPDATE_EXPECTED_HEAD"] = expected_head
    env["REMOVE_AGENT_RUNTIME_AFTER_UPDATE"] = str(
        remove_agent_runtime_after_update
    ).lower()
    update = subprocess.run(
        [bash, str(UPDATE_SCRIPT), "--repo", str(checkout), "--branch", "main"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert update.returncode == 0, (
        f"update script failed with exit code {update.returncode}\n"
        f"stdout:\n{update.stdout}\n"
        f"stderr:\n{update.stderr}"
    )

    assert _run_git("rev-parse", "HEAD", cwd=checkout).stdout.strip() == expected_head
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    if include_all_commands:
        return commands
    return [line for line in commands if line.startswith("compose up ")]


def test_runtime_source_update_force_recreates_only_application_services(
    tmp_path: Path,
) -> None:
    up_commands = _run_update_scenario(
        tmp_path,
        changed_file="moonmind/example.py",
    )

    assert len(up_commands) == 2
    recreate_command = next(
        command for command in up_commands if "--force-recreate" in command
    )
    restart_command = next(
        command for command in up_commands if "--force-recreate" not in command
    )
    assert "api" in recreate_command
    assert "temporal-worker-workflow" in recreate_command
    assert "postgres" not in recreate_command
    assert "postgres" in restart_command
    assert all("temporal-worker-deployment-control" not in row for row in up_commands)


def test_documentation_update_does_not_force_recreate_services(
    tmp_path: Path,
) -> None:
    up_commands = _run_update_scenario(
        tmp_path,
        changed_file="docs/guide.md",
    )

    assert len(up_commands) == 1
    assert "--force-recreate" not in up_commands[0]
    assert "api" in up_commands[0]
    assert "postgres" in up_commands[0]
    assert "temporal-worker-workflow" in up_commands[0]
    assert "temporal-worker-deployment-control" not in up_commands[0]


def test_skill_source_update_quiesces_resolver_before_checkout_mutation(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (REPLAY_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (REPLAY_ROOT / "expected-outcome.json").read_text(encoding="utf-8")
    )
    commands = _run_update_scenario(
        tmp_path,
        changed_file=manifest["changedFile"],
        include_all_commands=True,
    )

    stop_command = expected["stopCommand"]
    barrier_recreate = expected["recreateCommand"]
    assert stop_command in commands
    assert barrier_recreate in commands
    assert commands.count(barrier_recreate) == 1
    assert commands.index(stop_command) < commands.index(
        expected["composePullCommand"]
    )
    assert commands.index(expected["composePullCommand"]) < commands.index(
        barrier_recreate
    )
    final_force_recreates = [
        command
        for command in commands[commands.index(barrier_recreate) + 1 :]
        if command.startswith("compose up ") and "--force-recreate" in command
    ]
    assert all(
        expected["barrierService"] not in command
        for command in final_force_recreates
    )


def test_update_uses_one_fetched_commit_without_a_second_fetching_pull() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")

    assert 'git checkout -B "$BRANCH" "$REMOTE_COMMIT"' in script
    assert 'git pull --ff-only origin "$BRANCH"' not in script
    assert '"$POST_PULL_COMMIT" != "$REMOTE_COMMIT"' in script


def test_skill_barrier_does_not_restart_service_removed_by_update(
    tmp_path: Path,
) -> None:
    expected = json.loads(
        (REPLAY_ROOT / "expected-outcome.json").read_text(encoding="utf-8")
    )
    commands = _run_update_scenario(
        tmp_path,
        changed_file=".agents/skills/example/SKILL.md",
        include_all_commands=True,
        remove_agent_runtime_after_update=True,
    )

    assert expected["stopCommand"] in commands
    assert expected["composePullCommand"] in commands
    assert expected["recreateCommand"] not in commands
