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
@pytest.mark.parametrize(
    "candidate_authority",
    [
        "accepted",
        "omitted",
        "stale",
        "other_workflow",
        "other_repository",
        "other_branch",
    ],
)
async def test_publish_clean_single_branch_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_branch: str | None,
    publish_mode: str,
    candidate_authority: str,
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
    if candidate_authority != "omitted":
        args["accepted_published_head"] = {
            "workflowId": (
                "another-workflow"
                if candidate_authority == "other_workflow"
                else workflow_id
            ),
            "repository": (
                "another/repository"
                if candidate_authority == "other_repository"
                else "example/repository"
            ),
            "branch": (
                "another-branch"
                if candidate_authority == "other_branch"
                else "candidate"
            ),
            "headSha": "1" * 40 if candidate_authority == "stale" else candidate_sha,
        }
    if base_branch == "missing" or candidate_authority == "other_branch":
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
    if candidate_authority == "accepted" and not no_new_commits:
        assert evidence["push_branch"] == "candidate"
        assert "moonmind-job-" not in git("branch", cwd=origin)
    assert (
        git("rev-parse", f"refs/heads/{evidence['push_branch']}", cwd=origin)
        == candidate_sha
    )
    assert git("status", "--porcelain", cwd=workspace) == ""
    if publish_mode == "pr" and (
        not no_new_commits or candidate_authority == "accepted"
    ):
        assert (
            evidence["pull_request_url"]
            == "https://github.com/example/repository/pull/1"
        )
        resolve_pr.assert_awaited_once_with(
            repo="example/repository",
            selector=evidence["push_branch"],
            github_token="fixture-credential",
            expected_head_sha=candidate_sha,
            expected_base_branch=base_branch or "main",
            expected_draft=False,
        )
    else:
        assert "pull_request_url" not in evidence
        resolve_pr.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("base_branch", [None, "", "main", "release"])
async def test_unchanged_shared_base_cannot_supply_an_unrelated_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_branch: str | None,
) -> None:
    """A main-headed release PR is unrelated to an unchanged issue checkout."""
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    branch = base_branch or "main"
    origin = tmp_path / "origin.git"
    git("init", "--bare", str(origin), cwd=tmp_path)
    workflow_id, step_id = "workflow", "unchanged"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    git("init", f"--initial-branch={branch}", cwd=workspace)
    (workspace / "existing.txt").write_text("existing work\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "existing", cwd=workspace)
    git("remote", "add", "origin", str(origin), cwd=workspace)
    git("push", "origin", branch, cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True,
            pr_url="https://github.com/example/repository/pull/999",
        )
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )

    evidence = await OmnigentWorkspacePublicationService(tmp_path).publish_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        publication_identity="unchanged-publication",
        publish_mode="pr",
        base_branch=base_branch,
        repository="example/repository",
        github_token="fixture-credential",
    )

    assert evidence["push_status"] == "no_commits"
    assert evidence["remote_verified"] is True
    assert evidence["push_branch"] == branch
    assert "pull_request_url" not in evidence
    resolve_pr.assert_not_awaited()
