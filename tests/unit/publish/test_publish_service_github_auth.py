from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from moonmind.config.settings import settings
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


async def test_publish_branch_blocks_high_security_scan_before_push(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    calls: list[dict[str, object]] = []
    scan_call_count = 0

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    async def _mock_exec(*args, **kwargs):
        nonlocal scan_call_count
        del kwargs
        scan_call_count += 1
        proc = AsyncMock()
        if scan_call_count == 1:
            proc.communicate = AsyncMock(
                return_value=(b"commit local-sha\nsubject MM-813\n", b"")
            )
            proc.returncode = 0
        elif scan_call_count == 2:
            proc.communicate = AsyncMock(return_value=(b"app/config.py\n", b""))
            proc.returncode = 0
        elif scan_call_count == 3:
            proc.communicate = AsyncMock(
                return_value=(b"+api_key=do-not-print-this-value\n", b"")
            )
            proc.returncode = 0
        else:
            raise AssertionError(f"Unexpected scan subprocess: {args!r}")
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
        with pytest.raises(RuntimeError) as exc_info:
            await PublishService().publish(
                job_id=uuid4(),
                instruction="MM-813 make change",
                publish_mode="branch",
                publish_base_branch="main",
                runtime_mode="codex",
                repo_dir=tmp_path,
                run_command=_run,
                repo=None,
            )

    message = str(exc_info.value)
    assert "git.push.diff:app/config.py" in message
    assert "do-not-print-this-value" not in message
    assert not any(call["command"][:2] == ["git", "push"] for call in calls)


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
