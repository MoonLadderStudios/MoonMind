"""An unchanged saved checkout survives ordinary remote-base advancement."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.publish.service import PublishService
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.omnigent.test_workspace_publication_base import git

pytestmark = [pytest.mark.asyncio, pytest.mark.reliability_journey]


@pytest.mark.parametrize("publish_mode", ["pr", "branch"])
@pytest.mark.parametrize("base_branch", [None, "main"])
@pytest.mark.parametrize("relation", ["same", "advanced", "diverged"])
async def test_unchanged_candidate_retains_identity_without_publication_authority(
    tmp_path, monkeypatch, publish_mode, base_branch, relation
):
    fixture = json.loads(
        Path("tests/fixtures/reliability/unchanged-base-publication.json").read_text()
    )
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    workflow_id, step_id = fixture["workflowId"], "unchanged-remediation"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    git("init", "--initial-branch=main", cwd=workspace)
    (workspace / "original.txt").write_text("saved input\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "original", cwd=workspace)
    original = git("rev-parse", "HEAD", cwd=workspace)
    git("remote", "add", "origin", str(origin), cwd=workspace)
    git("push", "origin", "main", cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    writer = tmp_path / "writer"
    git("clone", str(origin), str(writer), cwd=tmp_path)
    if relation != "same":
        if relation == "diverged":
            git("checkout", "--orphan", "replacement", cwd=writer)
            git("rm", "-rf", ".", cwd=writer)
        (writer / "other.txt").write_text("another writer\n")
        git("add", ".", cwd=writer)
        git("commit", "-m", "advance remote", cwd=writer)
        if relation != "diverged":
            git("push", "--no-force", "origin", "HEAD:main", cwd=writer)
    remote_before = git("for-each-ref", "--format=%(refname) %(objectname)", cwd=origin)
    if relation == "diverged":
        original_publish = PublishService.publish

        async def publish_then_replace_base(self, **kwargs):
            nonlocal remote_before
            published = await original_publish(self, **kwargs)
            assert published.status == "skipped"
            # The remote is rewritten after the real zero-commit comparison,
            # immediately before the no-change evidence is verified.
            git("push", "--force", "origin", "HEAD:main", cwd=writer)
            remote_before = git(
                "for-each-ref", "--format=%(refname) %(objectname)", cwd=origin
            )
            return published

        monkeypatch.setattr(PublishService, "publish", publish_then_replace_base)
    resolve_pr = AsyncMock()
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    arguments = dict(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        publication_identity="no-change",
        publish_mode=publish_mode,
        base_branch=base_branch,
        repository="example/repository",
        github_token="fixture-only",
    )
    if relation == "diverged":
        with pytest.raises((HarnessPlatformError, RuntimeError)):
            await publisher.publish_workspace(**arguments)
        assert (
            git("for-each-ref", "--format=%(refname) %(objectname)", cwd=origin)
            == remote_before
        )
        assert git("rev-parse", "HEAD", cwd=workspace) == original
        return
    evidence = await publisher.publish_workspace(**arguments)
    assert evidence["push_status"] == "no_commits"
    assert evidence["push_head_sha"] == original
    assert evidence["push_commit_count"] == 0
    assert evidence["remote_verified"] is True
    assert "pull_request_url" not in evidence
    resolve_pr.assert_not_awaited()
    assert git("rev-parse", "HEAD", cwd=workspace) == original
    assert git("status", "--porcelain", cwd=workspace) == ""
    assert (
        git("for-each-ref", "--format=%(refname) %(objectname)", cwd=origin)
        == remote_before
    )

    # Pass the real publication result through the production runtime projection
    # and parent consumer: no lost work does not mean a publishable candidate.
    bound_publisher = SimpleNamespace(
        publish_request_workspace=AsyncMock(return_value=evidence)
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": step_id,
            "parameters": {"publishMode": publish_mode},
        }
    )
    result = await GenericOmnigentHostRealizer._publish_repository(
        SimpleNamespace(_workspace_publisher=bound_publisher),
        request,
        AgentRunResult(summary="Prerequisite evidence remains outstanding."),
    )
    accepted = result.metadata["acceptedRepositoryEvidence"]
    assert accepted["headSha"] == original and accepted["repositoryChanged"] is False
    feasibility = MoonMindRunWorkflow()._step_publication_feasibility(
        {"outputs": result.metadata}
    )
    assert (
        feasibility["feasible"] is False
        and feasibility["reason"] == "no_candidate_change"
    )


async def test_owned_candidate_reuse_still_requires_the_exact_remote_head(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    workflow_id, step_id = "owned-publication", "reuse"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    git("init", "--initial-branch=main", cwd=workspace)
    (workspace / "original.txt").write_text("base\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "base", cwd=workspace)
    git("remote", "add", "origin", str(origin), cwd=workspace)
    git("push", "origin", "main", cwd=workspace)
    git("checkout", "-b", "candidate", cwd=workspace)
    (workspace / "candidate.txt").write_text("candidate\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "candidate", cwd=workspace)
    candidate = git("rev-parse", "HEAD", cwd=workspace)
    git("push", "origin", "candidate", cwd=workspace)
    writer = tmp_path / "writer"
    git("clone", "--branch", "candidate", str(origin), str(writer), cwd=tmp_path)
    (writer / "newer.txt").write_text("another writer\n")
    git("add", ".", cwd=writer)
    git("commit", "-m", "advance candidate", cwd=writer)
    git("push", "origin", "candidate", cwd=writer)
    remote_before = git("for-each-ref", "--format=%(refname) %(objectname)", cwd=origin)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    with pytest.raises(HarnessPlatformError) as caught:
        await OmnigentWorkspacePublicationService(tmp_path).publish_workspace(
            workspace_locator={
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
            publication_identity="owned-candidate",
            publish_mode="pr",
            base_branch="main",
            repository="example/repository",
            github_token="fixture-only",
            accepted_published_head={
                "workflowId": workflow_id,
                "repository": "example/repository",
                "branch": "candidate",
                "headSha": candidate,
            },
        )
    assert caught.value.code == "OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED"
    assert git("rev-parse", "HEAD", cwd=workspace) == candidate
    assert (
        git("for-each-ref", "--format=%(refname) %(objectname)", cwd=origin)
        == remote_before
    )
