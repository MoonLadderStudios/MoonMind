"""Replay a clean remediation checkout through real Git publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.publish.service import PublishService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    ("diagnostic", "retryable"),
    [
        ("Could not resolve host: github.com", True),
        ("Could not resolve proxy: proxy.internal", True),
        (
            "Failed to connect to github.com port 443 after 10 ms: Couldn't connect to server",
            True,
        ),
        ("The requested URL returned error: 403", False),
        ("SSL certificate problem: unable to get local issuer certificate", False),
        ("new transport failure", False),
        ("", False),
    ],
)
def test_publication_transport_diagnostics_fail_closed(diagnostic, retryable):
    from moonmind.omnigent.workspace_publication import _PRE_CONNECTION_FAILURE

    error = f"fatal: unable to access 'https://github.com/example/repo.git/': {diagnostic}\n"
    assert bool(_PRE_CONNECTION_FAILURE.fullmatch(error.strip())) is retryable
    # A marker inside an unrelated diagnostic is not transport authority.
    assert not _PRE_CONNECTION_FAILURE.fullmatch(f"remote: {error}")


@pytest.mark.parametrize(
    ("stderr", "transient"),
    [
        (
            "error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\n"
            "send-pack: unexpected disconnect while reading sideband packet\n"
            "fatal: the remote end hung up unexpectedly",
            True,
        ),
        (
            "fatal: unable to access 'https://github.com/o/r.git/': "
            "The requested URL returned error: 502",
            True,
        ),
        ("remote: Internal Server Error\nfatal: the remote end hung up unexpectedly", True),
        ("error: RPC failed; curl 56 Recv failure: Connection reset by peer", True),
        (
            "fatal: unable to access 'https://github.com/o/r.git/': "
            "Could not resolve host: github.com",
            True,
        ),
        (
            "fatal: unable to access 'https://github.com/o/r.git/': "
            "Failed to connect to github.com port 443 after 75003 ms: Connection timed out",
            True,
        ),
        (
            "fatal: unable to access 'https://github.com/o/r.git/': "
            "The requested URL returned error: 403",
            False,
        ),
        ("fatal: Authentication failed for 'https://github.com/o/r.git/'", False),
        (
            "remote: error: GH013: Repository rule violations found for refs/heads/x.\n"
            "! [remote rejected] x -> x (push declined due to repository rule violations)",
            False,
        ),
        (
            "! [rejected]        x -> x (stale info)\nerror: failed to push some refs",
            False,
        ),
        ("publication requires destination credential authority", False),
        ("", False),
    ],
)
def test_publication_failure_transience_is_classified_from_git_diagnostics(
    stderr, transient
):
    from moonmind.omnigent.workspace_publication import (
        publication_failure_is_transient,
    )

    message = f"repository publication command failed: {stderr}"
    assert publication_failure_is_transient(message) is transient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "transient"),
    [
        (
            b"error: RPC failed; HTTP 503 curl 22 The requested URL returned error: 503\n"
            b"fatal: the remote end hung up unexpectedly\n",
            True,
        ),
        (b"fatal: Authentication failed for 'https://github.com/o/r.git/'\n", False),
    ],
)
async def test_failed_git_command_reports_remote_outage_as_transient(
    monkeypatch, stderr, transient
):
    from moonmind.omnigent import workspace_publication
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    async def failed_push(args, **_kwargs):
        return 128, b"", stderr

    monkeypatch.setattr(workspace_publication, "run_runtime_command", failed_push)
    with pytest.raises(HarnessPlatformError) as raised:
        await workspace_publication.OmnigentWorkspacePublicationService._run(
            "git", "push", "origin", "candidate"
        )
    assert raised.value.code == "OMNIGENT_REPOSITORY_PUBLICATION_FAILED"
    assert raised.value.transient is transient
    assert stderr.decode().splitlines()[0] in str(raised.value)


@pytest.mark.asyncio
async def test_checkpoint_restore_preserves_accepted_pr_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay capture -> restore -> exact remote PR discovery, including retry."""
    replay = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint-executable-publication.json")
        .read_text()
    )
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    source = tmp_path / "source"
    source.mkdir()
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    git("init", "--initial-branch=main", cwd=source)
    (source / ".gitignore").write_text("artifacts/\n.moonmind/\n")
    (source / "run.sh").write_text("#!/bin/sh\nprintf 'candidate works\\n'\n")
    (source / "run.sh").chmod(0o755)
    (source / "work.txt").write_text("base\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "base", cwd=source)
    git("remote", "add", "origin", str(origin), cwd=source)
    git("push", "origin", "main", cwd=source)
    candidate = replay["pullRequest"]["head"]["ref"]
    git("checkout", "-b", candidate, cwd=source)
    (source / "work.txt").write_text("completed work\n")
    git("commit", "-am", "candidate", cwd=source)
    head_sha = git("rev-parse", "HEAD", cwd=source)
    git("push", "origin", candidate, cwd=source)
    archive, _ = TemporalSandboxActivities(workspace_root=tmp_path)._build_worktree_archive(
        source
    )

    class Artifacts:
        async def get_metadata(self, **_kwargs):
            return SimpleNamespace(size_bytes=len(archive)), []

        async def read_chunks(self, **_kwargs):
            return SimpleNamespace(), iter((archive,))

    workflow_id, step_id = replay["sourceWorkflowId"], replay["stepExecutionId"]
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", "--branch", candidate, str(origin), str(workspace), cwd=tmp_path)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    payload = replay["request"]
    payload.update(correlationId=workflow_id, idempotencyKey=step_id)
    payload["parameters"]["acceptedPublishedHead"]["headSha"] = head_sha
    payload["workspaceSpec"]["workspaceLocator"] = {
        "kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"
    }
    request = AgentExecutionRequest.model_validate(payload)
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=AsyncMock(side_effect=AssertionError("must reuse checkout")),
        workspace_root=tmp_path,
        artifact_service=Artifacts(),
    )
    await materializer.materialize(
        request, runtime_uid=os.getuid(), runtime_gid=os.getgid()
    )
    assert git("status", "--porcelain", cwd=workspace) == ""
    assert subprocess.run(
        [str(workspace / "run.sh")], check=True, capture_output=True, text=True
    ).stdout == "candidate works\n"
    assert not (workspace / "work.txt").stat().st_mode & 0o111

    # The agent created this PR before the trusted publisher inspected the
    # restored workspace. Exercise the real selector against its HTTP contract.
    pull_request = replay["pullRequest"]
    pull_request["head"]["sha"] = head_sha
    lookups = []

    def github(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/MoonLadderStudios/MoonMind/pulls"
        lookups.append(request.url.params["head"])
        return httpx.Response(200, json=[pull_request])

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(github), **kwargs),
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    for _attempt in range(2):
        evidence = await publisher.publish_request_workspace(
            request=request,
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
        )
        assert evidence["pull_request_url"] == pull_request["html_url"]
        assert evidence["push_branch"] == candidate
        assert evidence["push_head_sha"] == head_sha
        assert evidence["push_commit_count"] == 1
        assert evidence["remote_verified"] is True
        assert git("status", "--porcelain", cwd=workspace) == ""
        assert git("rev-parse", "HEAD", cwd=workspace) == head_sha
    assert lookups == [f"MoonLadderStudios:{candidate}"] * 2
    assert git("branch", "--format=%(refname:short)", cwd=origin).splitlines() == [
        "main", candidate
    ]


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
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
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
    if base_branch == "missing" or (
        candidate_authority == "other_branch" and publish_mode == "pr"
    ):
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
    if publish_mode == "pr" and candidate_authority == "accepted" and not no_new_commits:
        assert evidence["push_branch"] == "candidate"
        assert "moonmind-job-" not in git("branch", cwd=origin)
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
    git("init", "--bare", f"--initial-branch={branch}", str(origin), cwd=tmp_path)
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
@pytest.mark.parametrize(
    "change",
    [
        "uncommitted",
        "committed",
        "unchanged",
        "diverged",
        "foreign_before_clone",
        "foreign_before_lease",
        "foreign_before_push",
        "integrated",
        "base_advanced",
        "missing_accepted_sha",
    ],
)
async def test_owned_candidate_is_extended_not_stacked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """A later PR step on the workflow's own candidate keeps the authored base.

    PR #4743 stacked a new job branch on the earlier candidate and opened its
    PR against that candidate, which CI never runs for.
    """
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    candidate = "moonmind-job-b1e7bebf"
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git("init", "--initial-branch=main", cwd=source)
    (source / "base.txt").write_text("base\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "base", cwd=source)
    git("remote", "add", "origin", str(origin), cwd=source)
    git("push", "origin", "main", cwd=source)
    git("checkout", "-b", candidate, cwd=source)
    (source / "candidate.txt").write_text("first step\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "first step", cwd=source)
    candidate_sha = git("rev-parse", "HEAD", cwd=source)
    git("push", "origin", candidate, cwd=source)

    if change in {
        "foreign_before_clone",
        "foreign_before_lease",
        "foreign_before_push",
    }:
        (source / "foreign.txt").write_text("foreign actor\n")
        git("add", ".", cwd=source)
        git("commit", "-m", "foreign actor", cwd=source)
        foreign_sha = git("rev-parse", "HEAD", cwd=source)
        if change == "foreign_before_clone":
            git("push", "origin", candidate, cwd=source)
    if change in {"integrated", "base_advanced"}:
        git("push", "origin", "HEAD:refs/heads/main", cwd=source)
        if change == "base_advanced":
            git("checkout", "main", cwd=source)
            git("merge", "--ff-only", candidate, cwd=source)
            (source / "new-main.txt").write_text("unrelated later change\n")
            git("add", ".", cwd=source)
            git("commit", "-m", "advance authored base", cwd=source)
            git("push", "origin", "main", cwd=source)

    workflow_id, step_id = "workflow", "publish-09"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git(
        "clone",
        "--single-branch",
        "--branch",
        candidate,
        str(origin),
        str(workspace),
        cwd=tmp_path,
    )
    if change in {"foreign_before_lease", "foreign_before_push"}:
        # The foreign commit is locally available, but its remote publication
        # races after admission and before PublishService takes its lease.
        git("fetch", str(source), candidate, cwd=workspace)
        git("merge", "--ff-only", "FETCH_HEAD", cwd=workspace)
    if change in {
        "uncommitted",
        "committed",
        "foreign_before_clone",
        "foreign_before_lease",
        "foreign_before_push",
        "missing_accepted_sha",
    }:
        (workspace / "later.txt").write_text("later step\n")
    if change == "committed":
        git("add", ".", cwd=workspace)
        git("commit", "-m", "later step", cwd=workspace)
    if change == "diverged":
        # Another actor moved the candidate; the step must not overwrite it.
        (source / "other.txt").write_text("other actor\n")
        git("add", ".", cwd=source)
        git("commit", "-m", "other actor", cwd=source)
        git("push", "origin", candidate, cwd=source)
        (workspace / "later.txt").write_text("later step\n")
        git("add", ".", cwd=workspace)
        git("commit", "-m", "later step", cwd=workspace)
    remote_before = git("rev-parse", f"refs/heads/{candidate}", cwd=origin)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True,
            pr_url="https://github.com/example/repository/pull/4742",
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
        publication_identity="publish-09-publication",
        publish_mode="pr",
        base_branch=candidate,
        repository="example/repository",
        github_token="fixture-credential",
        accepted_published_head={
            "workflowId": workflow_id,
            "repository": "example/repository",
            "branch": candidate,
            "headSha": candidate_sha,
            "baseBranch": "main",
        },
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)

    if change == "missing_accepted_sha":
        args["accepted_published_head"].pop("headSha")
    if change in {"foreign_before_lease", "foreign_before_push"}:
        original_run = publisher._run

        async def race_before_lease(*command, **kwargs):
            if (
                change == "foreign_before_lease"
                and "ls-remote" in command
                and command[-1] == f"refs/heads/{candidate}"
            ) or (change == "foreign_before_push" and "push" in command):
                git("push", "origin", candidate, cwd=source)
            return await original_run(*command, **kwargs)

        monkeypatch.setattr(publisher, "_run", race_before_lease)
        remote_before = foreign_sha

    if change in {
        "diverged",
        "foreign_before_clone",
        "foreign_before_lease",
        "foreign_before_push",
        "missing_accepted_sha",
    }:
        with pytest.raises((HarnessPlatformError, RuntimeError)):
            await publisher.publish_workspace(**args)
        assert git("rev-parse", f"refs/heads/{candidate}", cwd=origin) == remote_before
        resolve_pr.assert_not_awaited()
        assert (workspace / "later.txt").read_text() == "later step\n"
    elif change in {"integrated", "base_advanced"}:
        evidence = await publisher.publish_workspace(**args)
        assert evidence == {
            "push_status": "no_commits",
            "push_branch": "main",
            "push_base_branch": "main",
            "push_head_sha": candidate_sha,
            "push_commit_count": 0,
            "remote_verified": change == "integrated",
        }
        assert git("rev-parse", "HEAD", cwd=workspace) == candidate_sha
        assert git("rev-parse", f"refs/heads/{candidate}", cwd=origin) == candidate_sha
        resolve_pr.assert_not_awaited()
    else:
        evidence = await publisher.publish_workspace(**args)
        head_sha = git("rev-parse", "HEAD", cwd=workspace)
        assert evidence["push_status"] == "pushed"
        assert evidence["push_branch"] == candidate
        assert evidence["push_base_branch"] == "main"
        assert evidence["push_head_sha"] == head_sha
        assert evidence["push_commit_count"] == (1 if change == "unchanged" else 2)
        assert evidence["remote_verified"] is True
        assert git("rev-parse", f"refs/heads/{candidate}", cwd=origin) == head_sha
        assert git("status", "--porcelain", cwd=workspace) == ""
        assert evidence["pull_request_url"].endswith("/pull/4742")
        resolve_pr.assert_awaited_once_with(
            repo="example/repository",
            selector=candidate,
            github_token="fixture-credential",
            expected_head_sha=head_sha,
            expected_base_branch="main",
            expected_draft=False,
        )
    assert git("branch", "--format=%(refname:short)", cwd=origin).splitlines() == [
        "main",
        candidate,
    ]


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
    git("init", "--bare", f"--initial-branch={branch}", str(origin), cwd=tmp_path)
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
@pytest.mark.parametrize("remote_change", ["diverge", "delete"])
async def test_selected_branch_rejects_remote_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_during_push: bool,
    remote_change: str,
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

    def change_remote():
        if remote_change == "diverge":
            git("push", "origin", "selected", cwd=peer)
        else:
            git("push", "origin", "--delete", "selected", cwd=peer)

    if not race_during_push:
        change_remote()
    (workspace / "work.txt").write_text("agent work\n")
    git("commit", "-am", "agent work", cwd=workspace)
    agent_sha = git("rev-parse", "HEAD", cwd=workspace)

    async def run_command(command, *, cwd, check=True, **_kwargs):
        if command[:2] == ["git", "push"] and race_during_push:
            change_remote()
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
    if remote_change == "diverge":
        assert git("rev-parse", "selected", cwd=origin) == peer_sha
    else:
        assert git("for-each-ref", "refs/heads/selected", cwd=origin) == ""
    assert git("rev-parse", "HEAD", cwd=workspace) == agent_sha


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_remote", ["lost_ack", "competing"])
async def test_pr_candidate_reconciles_lost_ack_and_never_overwrites_competing_tip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_remote: str,
) -> None:
    """Immediate PR publication reconciles its own push before mutating again.

    The generated candidate branch already exists remotely: either holding this
    exact candidate (the first push succeeded but its acknowledgment was lost)
    or holding someone else's commit. The retry reuses the selected base and the
    same candidate; a freshly observed foreign tip is never force-replaced.
    """
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True, pr_url="https://github.com/example/repository/pull/7"
        )
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "--initial-branch=main", cwd=seed)
    (seed / "base.txt").write_text("base\n")
    git("add", ".", cwd=seed)
    git("commit", "-m", "base", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("checkout", "-b", "release", cwd=seed)
    (seed / "release.txt").write_text("release\n")
    git("add", ".", cwd=seed)
    git("commit", "-m", "release", cwd=seed)
    git("push", "origin", "release", cwd=seed)

    workflow_id, step_id = "workflow-4003", "publish-step"
    identity = "workflow-4003-publication"
    candidate_branch = f"moonmind-job-{str(uuid5(NAMESPACE_URL, identity))[:8]}"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", "--branch", "release", str(origin), str(workspace), cwd=tmp_path)
    (workspace / "work.txt").write_text("agent work\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "agent work", cwd=workspace)
    candidate_sha = git("rev-parse", "HEAD", cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    if prior_remote == "lost_ack":
        git("push", "origin", f"HEAD:refs/heads/{candidate_branch}", cwd=workspace)
        expected_remote = candidate_sha
    else:
        git("checkout", "-b", "peer", "main", cwd=seed)
        (seed / "peer.txt").write_text("someone else's work\n")
        git("add", ".", cwd=seed)
        git("commit", "-m", "peer work", cwd=seed)
        git("push", "origin", f"HEAD:refs/heads/{candidate_branch}", cwd=seed)
        expected_remote = git("rev-parse", "HEAD", cwd=seed)

    args = dict(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        publication_identity=identity,
        publish_mode="pr",
        base_branch="release",
        repository="example/repository",
        github_token="fixture-credential",
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    if prior_remote == "competing":
        with pytest.raises(RuntimeError, match="not a fast-forward"):
            await publisher.publish_workspace(**args)
        resolve_pr.assert_not_awaited()
    else:
        evidence = await publisher.publish_workspace(**args)
        assert evidence["push_status"] == "pushed"
        assert evidence["push_branch"] == candidate_branch
        assert evidence["push_base_branch"] == "release"
        assert evidence["push_head_sha"] == candidate_sha
        assert evidence["push_commit_count"] == 1
        assert evidence["remote_verified"] is True
        assert evidence["pull_request_url"] == (
            "https://github.com/example/repository/pull/7"
        )
        resolve_pr.assert_awaited_once_with(
            repo="example/repository",
            selector=candidate_branch,
            github_token="fixture-credential",
            expected_head_sha=candidate_sha,
            expected_base_branch="release",
            expected_draft=False,
        )
    assert git("rev-parse", f"refs/heads/{candidate_branch}", cwd=origin) == (
        expected_remote
    )
    assert git("rev-parse", "HEAD", cwd=workspace) == candidate_sha
    assert sorted(
        git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin)
        .splitlines()
    ) == sorted([candidate_branch, "main", "release"])


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_mode", ["branch", "pr"])
@pytest.mark.parametrize("main_exists", [False, True])
@pytest.mark.parametrize("default_source", ["clone", "remote", "unavailable"])
async def test_omitted_branch_uses_repository_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publish_mode: str,
    main_exists: bool,
    default_source: str,
) -> None:
    """An omitted branch follows the cloned default, including across retries."""
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=develop", str(origin), cwd=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git("init", "--initial-branch=develop", cwd=source)
    (source / "work.txt").write_text("base\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "base", cwd=source)
    original_sha = git("rev-parse", "HEAD", cwd=source)
    git("remote", "add", "origin", str(origin), cwd=source)
    git("push", "origin", "develop", cwd=source)
    if main_exists:
        git("push", "origin", "HEAD:refs/heads/main", cwd=source)

    workflow_id, step_id = "default-branch", "execution-1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", str(origin), str(workspace), cwd=tmp_path)
    assert git("branch", "--show-current", cwd=workspace) == "develop"
    if default_source != "clone":
        git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=workspace)
    if default_source == "unavailable":
        git("symbolic-ref", "HEAD", "refs/heads/missing", cwd=origin)
    (workspace / "work.txt").write_text("agent work\n")
    git("commit", "-am", "agent work", cwd=workspace)
    head_sha = git("rev-parse", "HEAD", cwd=workspace)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    args = dict(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        publication_identity="default-branch-replay",
        publish_mode=publish_mode,
        base_branch=None,
        repository="",
        github_token=None,
    )
    if default_source == "unavailable":
        with pytest.raises(
            HarnessPlatformError, match="default branch could not be resolved"
        ):
            await publisher.publish_workspace(**args)
        assert git("rev-parse", "develop", cwd=origin) == original_sha
        assert git("rev-parse", "HEAD", cwd=workspace) == head_sha
        assert "moonmind-job-" not in git("branch", cwd=origin)
        return
    result = await publisher.publish_workspace(**args)
    assert result["push_status"] == "pushed"
    assert result["push_base_branch"] == "develop"
    assert result["push_head_sha"] == head_sha
    assert result["remote_verified"] is True
    assert (result["push_branch"] == "develop") == (publish_mode == "branch")
    assert (
        git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=workspace)
        == "refs/remotes/origin/develop"
    )
    if main_exists:
        assert git("rev-parse", "main", cwd=origin) == original_sha

    # A change to the remote default cannot retarget publication on retry.
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
    retry = await publisher.publish_workspace(**args)
    assert retry["push_base_branch"] == "develop"
    assert retry["push_branch"] == result["push_branch"]
    assert retry["push_head_sha"] == head_sha
    assert retry["remote_verified"] is True


def _attempt_request(
    tmp_path: Path, *, ordinal: int, logical_step_id: str = "implement"
) -> tuple[AgentExecutionRequest, Path]:
    workflow_id, run_id = "workflow-4627", "run-1"
    step_id = f"{workflow_id}:{run_id}:{logical_step_id}:execution:{ordinal}"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=workflow_id,
        idempotencyKey=f"{step_id}:agent_execute",
        stepExecution={
            "schemaVersion": "v1",
            "workflowId": workflow_id,
            "runId": run_id,
            "logicalStepId": logical_step_id,
            "executionOrdinal": ordinal,
            "stepExecutionId": step_id,
            "reason": "initial_execution" if ordinal == 1 else "retry",
            "runtimeContextPolicy": "fresh_agent_run",
        },
        parameters={"publishMode": "pr"},
        workspaceSpec={
            "repository": "example/repository",
            "startingBranch": "main",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
        },
    )
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    return request, tmp_path / "temporal_sandbox" / workspace_id / "repo"


@pytest.mark.asyncio
@pytest.mark.parametrize("successor_second", [0, 1])
@pytest.mark.parametrize("deployment_identity_changed", [False, True])
@pytest.mark.parametrize(
    "remote_change", [None, "descendant", "foreign_author", "foreign_committer"]
)
async def test_successor_attempt_reconciles_predecessor_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    successor_second: int,
    remote_change: str | None,
    deployment_identity_changed: bool,
) -> None:
    """A successor Step Execution republishes the same logical candidate."""
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True, pr_url="https://github.com/example/repository/pull/7"
        )
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        resolve_pr,
    )
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git("init", "--initial-branch=main", cwd=source)
    (source / "work.txt").write_text("base\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "base", cwd=source)
    git("remote", "add", "origin", str(origin), cwd=source)
    git("push", "origin", "main", cwd=source)

    admitted_identity = {
        "name": settings.workflow.git_user_name or "MoonMind Worker",
        "email": settings.workflow.git_user_email
        or "moonmind-worker@users.noreply.github.com",
    }
    first, first_workspace = _attempt_request(tmp_path, ordinal=1)
    first.parameters["publicationGitIdentityAdmission"] = {
        "schemaVersion": "v1",
        "identity": dict(admitted_identity),
    }
    first_workspace.parent.mkdir(parents=True)
    git("clone", str(origin), str(first_workspace), cwd=tmp_path)
    (first_workspace / "work.txt").write_text("completed work\n")
    # The verified after_execution save precedes publication, so the successor
    # restores the agent's uncommitted result rather than the publisher commit.
    second, second_workspace = _attempt_request(tmp_path, ordinal=2)
    second.parameters["publicationGitIdentityAdmission"] = {
        "schemaVersion": "v1",
        "identity": dict(admitted_identity),
    }
    second_workspace.parent.mkdir(parents=True)
    shutil.copytree(first_workspace, second_workspace, symlinks=True)

    publisher = OmnigentWorkspacePublicationService(tmp_path)
    run = publisher._run
    pushes: list[tuple[str, ...]] = []

    async def record(*command, **kwargs):
        if "push" in command:
            pushes.append(command)
        return await run(*command, **kwargs)

    publisher._run = record  # type: ignore[method-assign]
    # The predecessor's push lands, then its host is lost before the
    # acknowledgment reaches the workflow.
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_DATE", "2026-01-01T00:01:00+00:00")
    lost = await publisher.publish_request_workspace(
        request=first,
        current_workflow_id="workflow-4627",
        current_step_execution_id=first.step_execution.step_execution_id,
    )
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(
            f"GIT_{prefix}_DATE",
            f"2026-01-01T00:01:{successor_second:02d}+00:00",
        )
    if deployment_identity_changed:
        monkeypatch.setattr(settings.workflow, "git_user_name", "Changed default")
        monkeypatch.setattr(
            settings.workflow, "git_user_email", "changed@example.invalid"
        )
    if remote_change == "descendant":
        # Even identical bytes do not authorize replacing another actor's
        # later commit. Its distinct parent must remain on the remote branch.
        peer = tmp_path / "peer"
        git("clone", str(origin), str(peer), cwd=tmp_path)
        git("checkout", lost["push_branch"], cwd=peer)
        git("commit", "--allow-empty", "-m", "peer progress", cwd=peer)
        git("push", "origin", lost["push_branch"], cwd=peer)
        peer_head = git("rev-parse", "HEAD", cwd=peer)
    elif remote_change is not None:
        # Simulate a rewrite only in this test-owned bare origin. Everything
        # except the original author's or committer's identity is preserved.
        with monkeypatch.context() as foreign:
            for prefix, formats in (
                ("AUTHOR", ("%an", "%ae")),
                ("COMMITTER", ("%cn", "%ce")),
            ):
                for field, fmt in zip(("NAME", "EMAIL"), formats):
                    foreign.setenv(
                        f"GIT_{prefix}_{field}",
                        git(
                            "show",
                            "--no-patch",
                            f"--format={fmt}",
                            lost["push_head_sha"],
                            cwd=origin,
                        ),
                    )
            prefix = "AUTHOR" if remote_change == "foreign_author" else "COMMITTER"
            foreign.setenv(f"GIT_{prefix}_NAME", "Other actor")
            foreign.setenv(f"GIT_{prefix}_EMAIL", "other@example.invalid")
            peer_head = git(
                "commit-tree",
                git("rev-parse", f"{lost['push_head_sha']}^{{tree}}", cwd=origin),
                "-p",
                git("rev-parse", f"{lost['push_head_sha']}^", cwd=origin),
                "-m",
                git(
                    "show",
                    "--no-patch",
                    "--format=%B",
                    lost["push_head_sha"],
                    cwd=origin,
                ),
                cwd=origin,
            )
        git(
            "update-ref",
            f"refs/heads/{lost['push_branch']}",
            peer_head,
            lost["push_head_sha"],
            cwd=origin,
        )
    if remote_change is not None:
        with pytest.raises(RuntimeError, match="not a fast-forward"):
            await publisher.publish_request_workspace(
                request=second,
                current_workflow_id="workflow-4627",
                current_step_execution_id=second.step_execution.step_execution_id,
            )
        assert git("rev-parse", lost["push_branch"], cwd=origin) == peer_head
        assert (second_workspace / "work.txt").read_text() == "completed work\n"
        assert len(pushes) == 1
        return
    successor = await publisher.publish_request_workspace(
        request=second,
        current_workflow_id="workflow-4627",
        current_step_execution_id=second.step_execution.step_execution_id,
    )

    assert successor["push_branch"] == lost["push_branch"]
    assert successor["push_head_sha"] == lost["push_head_sha"]
    assert (
        successor["pull_request_url"] == "https://github.com/example/repository/pull/7"
    )
    assert git("branch", "--format=%(refname:short)", cwd=origin).splitlines() == [
        "main",
        lost["push_branch"],
    ]
    assert (
        git("rev-parse", f"refs/heads/{lost['push_branch']}", cwd=origin)
        == successor["push_head_sha"]
    )
    assert (
        git("show", f"{lost['push_branch']}:work.txt", cwd=origin) == "completed work"
    )
    assert len(pushes) == 2
    assert [call.kwargs["selector"] for call in resolve_pr.await_args_list] == [
        lost["push_branch"]
    ] * 2

    # Another logical step remains a distinct publication effect.
    other, other_workspace = _attempt_request(
        tmp_path, ordinal=1, logical_step_id="document"
    )
    other_workspace.parent.mkdir(parents=True)
    shutil.copytree(first_workspace, other_workspace, symlinks=True)
    (other_workspace / "notes.txt").write_text("other step\n")
    distinct = await publisher.publish_request_workspace(
        request=other,
        current_workflow_id="workflow-4627",
        current_step_execution_id=other.step_execution.step_execution_id,
    )
    assert distinct["push_branch"] != lost["push_branch"]
