"""Replay a clean remediation checkout through real Git publication."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.publish.service import PublishService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
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
    if publish_mode == "branch":
        assert evidence["push_branch"] == (base_branch or "main")
    elif not no_new_commits:
        assert evidence["push_branch"] != (base_branch or "main")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("authored_branch", [None, "main", "moonmind-job-34d23444"])
@pytest.mark.parametrize("input_shape", ["branch", "startingBranch", "repository"])
@pytest.mark.parametrize("publish_mode", ["branch", "pr"])
async def test_selected_branch_publication_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored_branch: str | None,
    input_shape: str,
    publish_mode: str,
) -> None:
    """Replay the escaped input through the request boundary and real remote."""
    replay = json.loads(
        (
            Path(__file__).parent / "fixtures/selected-branch-publication.json"
        ).read_text()
    )
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    resolve_pr = AsyncMock(return_value=SimpleNamespace(resolved=False))
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )
    branch = authored_branch or "main"
    origin = tmp_path / "origin.git"
    git("init", "--bare", str(origin), cwd=tmp_path)
    workflow_id, step_id = replay["workflowId"], replay["stepExecutionId"]
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    git("init", f"--initial-branch={branch}", cwd=workspace)
    (workspace / "work.txt").write_text("original PR\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "original PR", cwd=workspace)
    git("remote", "add", "origin", str(origin), cwd=workspace)
    git("push", "origin", branch, cwd=workspace)
    original_sha = git("rev-parse", "HEAD", cwd=workspace)
    (workspace / "work.txt").write_text("completed work\n")
    git("commit", "-am", "complete existing PR", cwd=workspace)
    head_sha = git("rev-parse", "HEAD", cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    request_payload = replay["request"]
    spec = request_payload["workspaceSpec"]
    spec.pop("branch")
    if input_shape == "repository":
        spec["repository"] = {"repository": {"name": "MoonLadderStudios/MoonMind"}}
        if authored_branch is not None:
            spec["repository"]["branch"] = {"name": authored_branch}
    elif authored_branch is not None:
        spec[input_shape] = authored_branch
    spec["workspaceLocator"] = {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "relativePath": "repo",
    }
    request_payload["parameters"]["publishMode"] = publish_mode
    request = AgentExecutionRequest.model_validate(request_payload)
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    args = dict(
        request=request,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
    )
    evidence = await publisher.publish_request_workspace(**args)

    assert evidence["push_status"] == "pushed"
    assert evidence["push_head_sha"] == head_sha
    assert evidence["remote_verified"] is True
    assert evidence["push_commit_count"] == 1
    if publish_mode == "branch":
        assert evidence["push_branch"] == branch
        assert git("rev-parse", f"refs/heads/{branch}", cwd=origin) == head_sha
        assert (
            git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin)
            == branch
        )
        # The old payload is retryable without another commit or a new branch.
        retry = await publisher.publish_request_workspace(**args)
        assert retry["push_status"] == "no_commits"
        assert retry["push_branch"] == branch
        assert retry["push_head_sha"] == head_sha
        assert retry["remote_verified"] is True
        resolve_pr.assert_not_awaited()
    else:
        assert evidence["push_branch"] != branch
        assert git("rev-parse", f"refs/heads/{branch}", cwd=origin) == original_sha
        assert git("rev-parse", evidence["push_branch"], cwd=origin) == head_sha


@pytest.mark.asyncio
@pytest.mark.parametrize("race_during_push", [False, True])
async def test_selected_branch_rejects_remote_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race_during_push: bool
) -> None:
    """An authored PR branch must never be force-replaced with a stale checkout."""
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", str(origin), cwd=tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git("init", "--initial-branch=selected", cwd=workspace)
    (workspace / "work.txt").write_text("original\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "original", cwd=workspace)
    git("remote", "add", "origin", str(origin), cwd=workspace)
    git("push", "origin", "selected", cwd=workspace)
    peer = tmp_path / "peer"
    git("clone", "--branch", "selected", str(origin), str(peer), cwd=tmp_path)
    (peer / "peer.txt").write_text("concurrent work\n")
    git("add", ".", cwd=peer)
    git("commit", "-m", "concurrent work", cwd=peer)
    peer_sha = git("rev-parse", "HEAD", cwd=peer)
    if not race_during_push:
        git("push", "origin", "selected", cwd=peer)
    (workspace / "work.txt").write_text("agent work\n")
    git("commit", "-am", "agent work", cwd=workspace)
    agent_sha = git("rev-parse", "HEAD", cwd=workspace)

    async def run_command(command, *, cwd, check=True, **_kwargs):
        if command[:2] == ["git", "push"] and race_during_push:
            git("push", "origin", "selected", cwd=peer)
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        if check and result.returncode:
            raise RuntimeError(result.stderr)
        return result

    with pytest.raises(RuntimeError):
        await PublishService().publish(
            job_id=uuid4(),
            instruction="update selected PR",
            publish_mode="branch",
            publish_base_branch="selected",
            publication_branch_name="selected",
            runtime_mode="omnigent",
            repo_dir=workspace,
            run_command=run_command,
            publish_existing_commits=True,
            verify_remote=True,
        )
    assert git("rev-parse", "selected", cwd=origin) == peer_sha
    assert git("rev-parse", "HEAD", cwd=workspace) == agent_sha
