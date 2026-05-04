from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.publish.service import PublishService

pytestmark = pytest.mark.asyncio


async def test_publish_branch_push_uses_resolved_token_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "publish-token")
    calls: list[dict[str, object]] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    result = await PublishService().publish(
        job_id=uuid4(),
        instruction="make change",
        publish_mode="branch",
        publish_base_branch=None,
        runtime_mode="codex",
        repo_dir=tmp_path,
        run_command=_run,
        repo="owner/repo",
    )

    push_call = next(call for call in calls if call["command"][:2] == ["git", "push"])
    env = push_call["env"]
    assert result.startswith("published branch")
    assert env["GITHUB_TOKEN"] == "publish-token"
    assert env["GH_TOKEN"] == "publish-token"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "publish-token" in push_call["redaction_values"]


async def test_publish_pr_uses_rest_service_without_ambient_gh(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "publish-token")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh-token")
    calls: list[dict[str, object]] = []
    created: dict[str, object] = {}

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    async def _create_pr(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(created=True, url="https://github.com/owner/repo/pull/1")

    service = PublishService(github_create_pull_request=_create_pr)
    result = await service.publish(
        job_id=uuid4(),
        instruction="make change",
        publish_mode="pr",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=tmp_path,
        run_command=_run,
        repo="owner/repo",
    )

    assert result == "published PR https://github.com/owner/repo/pull/1"
    assert created["github_token"] == "publish-token"
    assert created["repo"] == "owner/repo"
    assert not any(call["command"][:3] == ["gh", "pr", "create"] for call in calls)
