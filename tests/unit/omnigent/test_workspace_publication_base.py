"""Replay a clean remediation checkout through real Git publication."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("base_branch", [None, "main", "missing", "candidate"])
@pytest.mark.parametrize("publish_mode", ["branch", "pr"])
async def test_publish_clean_single_branch_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_branch: str | None,
    publish_mode: str,
) -> None:
    """No agent edit or incidental fetch may be required to publish a candidate."""
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", str(origin), cwd=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git("init", "--initial-branch=main", cwd=source)
    (source / "base.txt").write_text("base\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "base", cwd=source)
    git("remote", "add", "origin", str(origin), cwd=source)
    git("push", "origin", "main", cwd=source)
    git("checkout", "-b", "candidate", cwd=source)
    (source / "candidate.txt").write_text("preserved work\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "candidate", cwd=source)
    candidate_sha = git("rev-parse", "HEAD", cwd=source)
    git("push", "origin", "candidate", cwd=source)

    workflow_id, step_id = "workflow", "remediation-4"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git(
        "clone",
        "--single-branch",
        "--branch",
        "candidate",
        str(origin),
        str(workspace),
        cwd=tmp_path,
    )
    assert "origin/main" not in git("branch", "--remotes", cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True,
            pr_url="https://github.com/example/repository/pull/1",
        )
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )
    args = dict(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        publication_identity="remediation-4-publication",
        publish_mode=publish_mode,
        base_branch=base_branch,
        repository="example/repository",
        github_token="fixture-credential",
    )
    if base_branch == "missing":
        with pytest.raises(HarnessPlatformError):
            await publisher.publish_workspace(**args)
        assert git("rev-parse", "HEAD", cwd=workspace) == candidate_sha
        assert "moonmind-job-" not in git("branch", cwd=origin)
        resolve_pr.assert_not_awaited()
        return

    evidence = await publisher.publish_workspace(**args)
    no_new_commits = base_branch == "candidate"
    assert evidence["push_status"] == ("no_commits" if no_new_commits else "pushed")
    assert evidence["push_base_branch"] == ("candidate" if no_new_commits else "main")
    assert evidence["push_head_sha"] == candidate_sha
    assert evidence["push_commit_count"] == (0 if no_new_commits else 1)
    assert evidence["remote_verified"] is True
    assert (
        git("rev-parse", f"refs/heads/{evidence['push_branch']}", cwd=origin)
        == candidate_sha
    )
    assert git("status", "--porcelain", cwd=workspace) == ""
    if publish_mode == "pr":
        assert (
            evidence["pull_request_url"]
            == "https://github.com/example/repository/pull/1"
        )
        resolve_pr.assert_awaited_once_with(
            repo="example/repository",
            selector=evidence["push_branch"],
            github_token="fixture-credential",
        )
    else:
        resolve_pr.assert_not_awaited()
