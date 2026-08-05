from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
CLEANUP_SCRIPT = REPO_ROOT / "tools" / "cleanup-docker-space.sh"


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("cleanup-docker-space.sh requires Bash")
    return bash


def _run_cleanup(
    tmp_path: Path,
    *args: str,
    sidecar_ids: str = "abc123def456",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG:?}"

if [[ "${1:-} ${2:-}" == "ps --filter" ]]; then
  printf '%s\\n' "${FAKE_SIDECAR_IDS:-}"
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(docker_log)
    env["FAKE_SIDECAR_IDS"] = sidecar_ids
    result = subprocess.run(
        [_bash(), str(CLEANUP_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    return result, commands


def test_dry_run_is_read_only_and_does_not_launch_diagnostic_container(
    tmp_path: Path,
) -> None:
    assert os.access(CLEANUP_SCRIPT, os.X_OK)

    result, commands = _run_cleanup(tmp_path, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert commands == [
        "system df",
        "ps --filter label=moonmind.kind=session-docker-sidecar --format {{.ID}}",
        (
            "exec abc123def456 docker "
            "-H unix:///var/run/moonmind-docker/docker.sock system df"
        ),
        "system df",
    ]
    assert "Would run: docker exec abc123def456 docker" in result.stdout
    assert "Would run: docker container prune -f" in result.stdout
    assert "Would run: docker image prune -f" in result.stdout
    assert "Would run: docker builder prune -af" in result.stdout
    assert "--dry-run" not in "\n".join(commands)
    assert all(not command.startswith("run ") for command in commands)
    assert all(" prune " not in f" {command} " for command in commands)


def test_cleanup_prunes_only_unused_sidecar_images_and_safe_host_resources(
    tmp_path: Path,
) -> None:
    result, commands = _run_cleanup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert commands == [
        "system df",
        "ps --filter label=moonmind.kind=session-docker-sidecar --format {{.ID}}",
        (
            "exec abc123def456 docker "
            "-H unix:///var/run/moonmind-docker/docker.sock system df"
        ),
        (
            "exec abc123def456 docker "
            "-H unix:///var/run/moonmind-docker/docker.sock image prune -af"
        ),
        "container prune -f",
        "image prune -f",
        "builder prune -af",
        "system df",
    ]
    assert "volume prune" not in "\n".join(commands)
    assert all(not command.startswith("run ") for command in commands)


def test_cleanup_rejects_unexpected_sidecar_identifier(tmp_path: Path) -> None:
    result, commands = _run_cleanup(tmp_path, sidecar_ids="not-a-container-id")

    assert result.returncode == 1
    assert "refusing unexpected Docker container id" in result.stderr
    assert all(not command.startswith("exec ") for command in commands)
