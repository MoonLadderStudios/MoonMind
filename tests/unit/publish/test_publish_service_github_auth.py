from __future__ import annotations

import sys
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


async def test_publish_pr_gh_fallback_injects_explicit_token_without_repo(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("GITHUB_TOKEN", "publish-token")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh-token")
    calls: list[dict[str, object]] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    result = await PublishService(gh_binary=sys.executable).publish(
        job_id=uuid4(),
        instruction="MM-673 make change",
        publish_mode="pr",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=tmp_path,
        run_command=_run,
        repo=None,
    )

    gh_call = next(
        call for call in calls if call["command"][:3] == [sys.executable, "pr", "create"]
    )
    gh_env = gh_call["env"]
    assert result.startswith("published PR from moonmind-job-")
    assert gh_env["GITHUB_TOKEN"] == "publish-token"
    assert gh_env["GH_TOKEN"] == "publish-token"
    assert gh_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GH_REPO" not in gh_env
    assert "publish-token" in gh_call["redaction_values"]
    assert "ambient-gh-token" not in gh_call["redaction_values"]


async def test_publish_pr_gh_fallback_rejects_ambient_auth_without_token(
    monkeypatch, tmp_path: Path
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.delenv("MOONMIND_GITHUB_TOKEN_REF", raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "ambient-gh-config"))
    calls: list[dict[str, object]] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    with pytest.raises(RuntimeError, match="requires an explicit resolved token"):
        await PublishService(gh_binary=sys.executable).publish(
            job_id=uuid4(),
            instruction="MM-673 make change",
            publish_mode="pr",
            publish_base_branch="main",
            runtime_mode="codex",
            repo_dir=tmp_path,
            run_command=_run,
            repo=None,
        )

    assert not any(
        call["command"][:3] == [sys.executable, "pr", "create"] for call in calls
    )
