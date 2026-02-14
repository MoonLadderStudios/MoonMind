"""Unit tests for codex worker handler logic."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker import handlers
from moonmind.agents.codex_worker.handlers import (
    CodexExecHandler,
    CodexExecPayload,
    CodexWorkerHandlerError,
    CommandResult,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


def test_codex_exec_payload_requires_repository_and_instruction() -> None:
    """Required payload fields should be enforced."""

    with pytest.raises(CodexWorkerHandlerError):
        CodexExecPayload.from_payload({"instruction": "do work"})

    with pytest.raises(CodexWorkerHandlerError):
        CodexExecPayload.from_payload({"repository": "MoonLadderStudios/MoonMind"})


async def test_handler_runs_clone_exec_and_diff(tmp_path: Path) -> None:
    """Handler should run core codex_exec command sequence."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(command, *, cwd, log_path, check=True):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "ref": "main",
            "workdirMode": "fresh_clone",
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    assert any(cmd[:2] == ["git", "clone"] for cmd in calls)
    assert ["codex", "exec", "Implement task"] in calls
    assert any(cmd[:2] == ["git", "diff"] for cmd in calls)
    assert any(item.name == "logs/codex_exec.log" for item in result.artifacts)
    assert any(item.name == "patches/changes.patch" for item in result.artifacts)


async def test_handler_publish_pr_invokes_gh(tmp_path: Path, monkeypatch) -> None:
    """Publish mode `pr` should invoke gh PR creation command."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(command, *, cwd, log_path, check=True):
        calls.append(list(command))
        if command[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(tuple(command), 0, " M changed.py\n", "")
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/a b/a\n", "")
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(handlers, "verify_cli_is_executable", lambda _name: "gh")
    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement publish test",
            "publish": {"mode": "pr", "baseBranch": "main"},
        },
    )

    assert result.succeeded is True
    assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


async def test_handler_publish_commit_failure_returns_failed_result(
    tmp_path: Path,
) -> None:
    """Publish flow should fail when git commit returns an error."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    async def fake_run_command(command, *, cwd, log_path, check=True):
        if command[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(tuple(command), 0, " M changed.py\n", "")
        if command[:2] == ["git", "commit"]:
            raise CodexWorkerHandlerError("command failed (1): git commit -m message")
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/a b/a\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement publish failure test",
            "publish": {"mode": "branch"},
        },
    )

    assert result.succeeded is False
    assert result.error_message is not None
    assert "git commit" in result.error_message


async def test_handler_invalid_payload_returns_failed_result(tmp_path: Path) -> None:
    """Handler should normalize validation failures into failed results."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    result = await handler.handle(job_id=uuid4(), payload={"repository": "repo-only"})

    assert result.succeeded is False
    assert result.error_message is not None
