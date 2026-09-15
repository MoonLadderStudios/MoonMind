"""Native checkpoint preservation supplies no-work evidence from real Git."""

import hashlib
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _exact_plan,
    _generic_publication_harness,
)
from tests.unit.omnigent.test_workspace_cleanup_receipt import workspace_request
from tests.unit.omnigent.test_workspace_publication_base import git


def comparison_from_git(workspace):
    """Serve the GitHub comparison shape using an actual isolated Git graph."""

    def compare(refs):
        head, base = refs.split("...", 1)
        try:
            git("fetch", "origin", base, cwd=workspace)
            tip = git("rev-parse", "FETCH_HEAD", cwd=workspace)
            merge_base = git("merge-base", head, tip, cwd=workspace)
        except subprocess.CalledProcessError:
            return {"message": "ref unavailable"}, 404
        status = (
            "identical"
            if head == tip
            else "ahead"
            if merge_base == head
            else "behind"
            if merge_base == tip
            else "diverged"
        )
        return {
            "status": status,
            "base_commit": {"sha": head},
            "merge_base_commit": {"sha": merge_base},
        }, 200

    return compare


async def capture_saved_workspace(
    tmp_path, monkeypatch, change="none", *, workflow_id=None, run_id=None
):
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-only")),
    )
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    harness = await _generic_publication_harness({})
    request = workspace_request(
        harness.publish_request, _exact_plan("opencode-go/model")
    )
    if workflow_id:
        payload = request.model_dump(by_alias=True, mode="json")
        payload["stepExecution"].update(
            workflowId=workflow_id,
            runId=run_id,
            stepExecutionId=f"{workflow_id}:{run_id}:assess:execution:1",
        )
        payload["correlationId"] = workflow_id
        request = AgentExecutionRequest.model_validate(payload)
    step = request.step_execution
    workspace_id = hashlib.sha256(
        f"{step.workflow_id}:{step.step_execution_id}".encode()
    ).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", str(origin), str(workspace), cwd=tmp_path)
    (workspace / "work.txt").write_text("original\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "source", cwd=workspace)
    git("push", "origin", "main", cwd=workspace)
    head = git("rev-parse", "HEAD", cwd=workspace)
    if change == "tracked":
        (workspace / "work.txt").write_text("valuable unfinished work\n")
    if change == "untracked":
        (workspace / "new.txt").write_text("valuable new work\n")
    if change == "commit":
        (workspace / "work.txt").write_text("unpublished commit\n")
        git("commit", "-am", "unfinished work", cwd=workspace)
    if change == "remote_deleted":
        git("update-ref", "-d", "refs/heads/main", cwd=origin)
    if change == "advanced_base":
        other = tmp_path / "other"
        git("clone", str(origin), str(other), cwd=tmp_path)
        (other / "other.txt").write_text("concurrent work\n")
        git("add", ".", cwd=other)
        git("commit", "-m", "advance", cwd=other)
        git("push", "origin", "main", cwd=other)
    git(
        "remote",
        "set-url",
        "origin",
        "https://github.com/example/repo.git",
        cwd=workspace,
    )
    git(
        "config",
        f"url.{origin}.insteadOf",
        "https://github.com/example/repo.git",
        cwd=workspace,
    )
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id, step.workflow_id, step.step_execution_id, "repo"
        )
    )
    payload = request.model_dump(by_alias=True, mode="json")
    payload["workspaceSpec"] = {
        "repository": "example/repo",
        "startingBranch": "main",
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
    }
    request = AgentExecutionRequest.model_validate(payload)
    objects = {}

    class Artifacts:
        async def write_bytes(self, *, payload, **kwargs):
            ref = "artifact:art_" + hashlib.sha256(payload).hexdigest()[:26]
            objects[ref] = payload
            return ref

        async def read_bytes(self, ref):
            return objects[ref]

    saved = await OmnigentWorkspacePublicationService(
        tmp_path, artifact_gateway=Artifacts()
    ).save_request_workspace(request)
    return saved, head, workspace, objects


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["none", "advanced_base", "tracked", "untracked", "commit", "remote_deleted"],
)
async def test_checkpoint_distinguishes_no_work_from_preserved_or_unknown_work(
    tmp_path, monkeypatch, change
):
    saved, _head, workspace, objects = await capture_saved_workspace(
        tmp_path, monkeypatch, change
    )
    assert saved["checkpointRef"] and saved["archiveRef"] and objects
    proof = saved["recoveryEvidence"]
    if change not in {"tracked", "untracked"}:
        assert proof["worktreeClean"] is True, proof
        assert proof["headSha"] == git("rev-parse", "HEAD", cwd=workspace)
        assert proof["checkpointArchiveDigest"] == saved["archiveDigest"]
    else:
        assert "worktreeClean" not in proof
    if change == "tracked":
        assert (workspace / "work.txt").read_text() == "valuable unfinished work\n"
    if change == "untracked":
        assert (workspace / "new.txt").read_text() == "valuable new work\n"
