from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.security.git_push_scan import (
    GitPushScanBlockedError,
    GitPushScanMaterializationError,
    scan_git_push_range_before_push,
)

pytestmark = pytest.mark.asyncio


async def test_high_security_scan_blocks_secret_in_diff_before_push(tmp_path: Path):
    raw_secret = "blocked-secret-value"
    commands: list[tuple[str, ...]] = []

    async def _run(command: list[str], *, cwd: Path) -> str:
        commands.append(tuple(command))
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return "base-sha\n"
        if command[:2] == ["git", "rev-list"]:
            return "1\n"
        if command[:2] == ["git", "log"]:
            return "abc123 clean commit\n"
        if command[:2] == ["git", "diff"]:
            return f"diff --git a/app.py b/app.py\n+api_key={raw_secret}\n"
        raise AssertionError(f"unexpected command: {command!r}")

    with pytest.raises(GitPushScanBlockedError) as exc_info:
        await scan_git_push_range_before_push(
            repo_dir=tmp_path,
            branch="feature/secret",
            base_ref="origin/main",
            run_git=_run,
            high_security_mode=True,
        )

    message = str(exc_info.value)
    assert "git.diff" in message
    assert raw_secret not in message
    assert any(command[:2] == ("git", "diff") for command in commands)


async def test_high_security_scan_blocks_secret_in_commit_metadata(tmp_path: Path):
    raw_secret = "commit-secret-value"

    async def _run(command: list[str], *, cwd: Path) -> str:
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return "base-sha\n"
        if command[:2] == ["git", "rev-list"]:
            return "1\n"
        if command[:2] == ["git", "log"]:
            return f"abc123 Publish token={raw_secret}\n"
        if command[:2] == ["git", "diff"]:
            return "diff --git a/app.py b/app.py\n+safe = True\n"
        raise AssertionError(f"unexpected command: {command!r}")

    with pytest.raises(GitPushScanBlockedError) as exc_info:
        await scan_git_push_range_before_push(
            repo_dir=tmp_path,
            branch="feature/secret-message",
            base_ref="origin/main",
            run_git=_run,
            high_security_mode=True,
        )

    assert "git.commit.metadata" in str(exc_info.value)
    assert raw_secret not in str(exc_info.value)


async def test_high_security_scan_allows_clean_range(tmp_path: Path):
    commands: list[tuple[str, ...]] = []

    async def _run(command: list[str], *, cwd: Path) -> str:
        commands.append(tuple(command))
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return "base-sha\n"
        if command[:2] == ["git", "rev-list"]:
            return "1\n"
        if command[:2] == ["git", "log"]:
            return "abc123 Clean commit\n"
        if command[:2] == ["git", "diff"]:
            return "diff --git a/app.py b/app.py\n+safe = True\n"
        raise AssertionError(f"unexpected command: {command!r}")

    result = await scan_git_push_range_before_push(
        repo_dir=tmp_path,
        branch="feature/clean",
        base_ref="origin/main",
        run_git=_run,
        high_security_mode=True,
    )

    assert result.allowed is True
    assert any(command[:2] == ("git", "log") for command in commands)
    assert any(command[:2] == ("git", "diff") for command in commands)


async def test_disabled_high_security_mode_skips_git_materialization(tmp_path: Path):
    async def _run(command: list[str], *, cwd: Path) -> str:
        raise AssertionError(f"disabled mode should not run git: {command!r}")

    result = await scan_git_push_range_before_push(
        repo_dir=tmp_path,
        branch="feature/skipped",
        base_ref="origin/main",
        run_git=_run,
        high_security_mode=False,
    )

    assert result.allowed is True
    assert result.high_security_mode is False


async def test_high_security_scan_fails_closed_on_materialization_error(tmp_path: Path):
    async def _run(command: list[str], *, cwd: Path) -> str:
        raise RuntimeError("fatal: could not read Username for token=raw-secret")

    with pytest.raises(GitPushScanMaterializationError) as exc_info:
        await scan_git_push_range_before_push(
            repo_dir=tmp_path,
            branch="feature/fail-closed",
            base_ref="origin/main",
            run_git=_run,
            high_security_mode=True,
        )

    assert "raw-secret" not in str(exc_info.value)
    assert "failed to materialize outbound git push range" in str(exc_info.value)
