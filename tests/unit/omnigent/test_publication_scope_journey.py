"""Local Git publication reaches the existing reviewer with original scope."""

import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.omnigent.test_workspace_publication_base import git


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["unexplained-removal", "authorized-removal", "rename", "no-code"]
)
async def test_concurrent_main_behavior_reaches_publication_and_review(
    tmp_path, monkeypatch, change
):
    for key, value in {
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _marker: True)
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.open_pull_request_heads",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService.resolve_pull_request_selector",
        AsyncMock(return_value=SimpleNamespace(resolved=False)),
    )
    remote, writer = tmp_path / "origin.git", tmp_path / "writer"
    git("init", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    git("clone", str(remote), str(writer), cwd=tmp_path)
    (writer / "credential.py").write_text("credential = 'ambient'\n")
    git("add", ".", cwd=writer)
    git("commit", "-m", "Initial application", cwd=writer)
    started_base = git("rev-parse", "HEAD", cwd=writer)
    git("branch", "task-start", cwd=writer)
    # A separate task lands its supported release behavior after this task starts.
    release = ".github/workflows/release.yml"
    assertion = "tests/test_release.py"
    for name, content in {
        release: "qualify-before-latest: true\n",
        assertion: "assert qualified(candidate)\n",
    }.items():
        path = writer / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git("add", ".", cwd=writer)
    git("commit", "-m", "Require release qualification before latest", cwd=writer)
    main = git("rev-parse", "HEAD", cwd=writer)
    assert main != started_base
    git("push", "origin", "main", cwd=writer)
    git("checkout", "task-start", cwd=writer)
    git("merge", "--no-edit", "main", cwd=writer)
    if change.endswith("removal"):
        git("rm", release, assertion, cwd=writer)
        (writer / "credential.py").write_text("credential = 'selected-B'\n")
    elif change == "rename":
        git("mv", "credential.py", "selected_credential.py", cwd=writer)
    if change != "no-code":
        git("add", ".", cwd=writer)
        git("commit", "-m", "Complete the separate task", cwd=writer)

    workflow_id, step_id = "workflow", "implement"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", "--no-local", str(writer), str(workspace), cwd=tmp_path)
    git("remote", "set-url", "origin", str(remote), cwd=workspace)
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
        publication_identity="fixture-publication",
        publish_mode="pr",
        base_branch="main",
        repository="example/repository",
        github_token="fixture-credential",
    )
    published = await publisher.publish_workspace(**args)
    if change == "no-code":
        assert published["push_status"] == "no_commits"
        assert (
            git("for-each-ref", "--format=%(refname)", cwd=remote) == "refs/heads/main"
        )
        return

    scope = (
        "Retire release qualification and its obsolete assertions."
        if change == "authorized-removal"
        else (
            "Rename the credential module without changing behavior."
            if change == "rename"
            else "Use selected credential B; preserve unrelated application behavior."
        )
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "example/repository"
    parent._assessment_context = {"assessmentVerdict": "PARTIALLY_IMPLEMENTED"}
    parent._trusted_issue_context = {
        "trustedSource": "moonmind.github.get_issue",
        "issue": {
            "repository": parent._repo,
            "number": 12,
            "title": "Complete the selected task",
            "url": "https://github.com/example/repository/issues/12",
            "body": scope,
        },
    }
    parent._record_accepted_published_head(
        {
            "acceptedRepositoryEvidence": {
                "pushStatus": published["push_status"],
                "branch": published["push_branch"],
                "headSha": published["push_head_sha"],
                "baseBranch": published["push_base_branch"],
            }
        }
    )
    create = AsyncMock(
        return_value={"url": "https://github.com/example/repository/pull/9"}
    )
    monkeypatch.setattr(run_module.workflow, "execute_activity", create)
    await parent._ensure_issue_implement_pr_before_status(
        node={"annotations": {"issueImplementRole": "code-review-handoff"}},
        parameters={
            "publishMode": "pr",
            "workflow": {"instructions": "Find a ready issue."},
        },
    )
    pr_body = create.await_args.args[1]["body"]
    assert scope in pr_body
    snapshot = runpy.run_path(
        str(
            Path(__file__).parents[3]
            / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py"
        )
    )
    collect = snapshot["fetch_candidate_changes"]

    def read(command, **_kwargs):
        if "/files?" in command[2]:
            entries = []
            for line in git(
                "diff", "--name-status", "--no-renames", f"{main}...HEAD", cwd=workspace
            ).splitlines():
                status, name = line.split("\t")
                entries.append(
                    {
                        "filename": name,
                        "status": {"D": "removed", "A": "added", "M": "modified"}[
                            status
                        ],
                        "patch": git(
                            "diff", f"{main}...HEAD", "--", name, cwd=workspace
                        ),
                    }
                )
            return entries
        return {"merge_base_commit": {"sha": main}, "commits": [], "total_commits": 0}

    monkeypatch.setitem(collect.__globals__, "run_command_optional", read)

    def inventory():
        files = read(["gh", "api", "/files?"])
        return collect(
            parent._repo,
            {
                "number": 9,
                "headRefOid": git("rev-parse", "HEAD", cwd=workspace),
                "baseRefOid": main,
                "changedFiles": len(files),
            },
        )

    changes = inventory()
    assert changes["filesComplete"]
    if change == "unexplained-removal":
        # Controlled reviewer response, based on the *actual* publication handoff
        # and Git diff, goes through the existing actionable-comment classifier.
        assert {
            entry["filename"]
            for entry in changes["files"]
            if entry["status"] == "removed"
        } == {release, assertion}
        assert "assert qualified(candidate)" in next(
            entry["patch"]
            for entry in changes["files"]
            if entry["filename"] == assertion
        )
        comments = [
            {
                "type": "review_comment",
                "user": "chatgpt-codex-connector[bot]",
                "body": "[P1] Restore the unrelated release qualification and its assertion; this task changes selected credentials.",
            }
        ]
        assert snapshot["summarize_comments"](comments)["actionableCommentCount"] == 1
        # Remediation restores the supported behavior, retaining the task fix.
        git("restore", "--source", main, "--", release, assertion, cwd=workspace)
        git("add", ".", cwd=workspace)
        git("commit", "-m", "Preserve unrelated release qualification", cwd=workspace)
        final = await publisher.publish_workspace(
            **args,
            accepted_published_head={
                "workflowId": workflow_id,
                "repository": parent._repo,
                "branch": published["push_branch"],
                "headSha": published["push_head_sha"],
                "baseBranch": "main",
            },
        )
        assert final["push_head_sha"] != published["push_head_sha"]
        changes = inventory()
        assert changes["headSha"] == final["push_head_sha"]
        assert [entry["filename"] for entry in changes["files"]] == ["credential.py"]
        assert git("show", f"HEAD:{release}", cwd=workspace) == git(
            "show", f"{main}:{release}", cwd=workspace
        )
    elif change == "authorized-removal":
        assert {
            entry["filename"]
            for entry in changes["files"]
            if entry["status"] == "removed"
        } == {release, assertion}
    else:
        assert git("show", "HEAD:selected_credential.py", cwd=workspace) == git(
            "show", f"{main}:credential.py", cwd=workspace
        )
    assert snapshot["summarize_comments"]([])["actionableCommentCount"] == 0
