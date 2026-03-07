"""Integration tests for codex worker git identity preflight."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.agents.codex_worker.handlers import CodexExecHandler
from moonmind.agents.codex_worker.worker import CodexWorker, CodexWorkerConfig

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_prepare_git_identity_preflight_allows_commit_without_global_identity(
    tmp_path: Path,
) -> None:
    """Local identity preflight should make commits succeed with no global git identity."""

    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=SimpleNamespace(),
        codex_exec_handler=CodexExecHandler(workdir_root=tmp_path),
    )  # type: ignore[arg-type]

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "prepare.log"
    home_dir = tmp_path / "no-global-home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home_dir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home_dir / ".gitconfig"),
    }

    await worker._run_stage_command(
        ["git", "init"],
        cwd=repo_dir,
        log_path=log_path,
        env=env,
    )
    (repo_dir / "README.md").write_text("hello\n", encoding="utf-8")
    await worker._run_stage_command(
        ["git", "add", "README.md"],
        cwd=repo_dir,
        log_path=log_path,
        env=env,
    )

    await worker._run_prepare_git_identity_preflight(
        repo_dir=repo_dir,
        log_path=log_path,
        selected_skills=("pr-resolver",),
        env=env,
    )

    await worker._run_stage_command(
        ["git", "commit", "-m", "preflight identity test"],
        cwd=repo_dir,
        log_path=log_path,
        env=env,
    )

    configured_name = await worker._run_stage_command(
        ["git", "config", "--local", "--get", "user.name"],
        cwd=repo_dir,
        log_path=log_path,
        env=env,
    )
    configured_email = await worker._run_stage_command(
        ["git", "config", "--local", "--get", "user.email"],
        cwd=repo_dir,
        log_path=log_path,
        env=env,
    )
    assert configured_name.stdout.strip() == "MoonMind Worker"
    assert configured_email.stdout.strip() == "moonmind-worker@users.noreply.github.com"
