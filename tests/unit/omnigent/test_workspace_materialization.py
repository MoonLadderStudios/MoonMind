import hashlib
import json
import subprocess

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError


def _git(*args: str, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_normal_workflow_materializes_owned_repository_once(tmp_path) -> None:
    jobs = tmp_path / "jobs"
    source = jobs / "sources" / "source"
    source.parent.mkdir(parents=True)
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    _git("config", "user.name", "MoonMind Test", cwd=source)
    (source / "README.md").write_text("source\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    source_commit = _git("rev-parse", "HEAD", cwd=source)

    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=jobs)
    workspace = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={
            "repository": str(source),
            "startingBranch": "main",
            "targetBranch": "issue-3507",
        },
        input_refs=("artifact:attachment-1", "checkpoint:restore-1"),
        resolved_skillset_ref="artifact:skills-1",
    )

    assert _git("branch", "--show-current", cwd=workspace) == "issue-3507"
    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["sourceCommit"] == source_commit
    assert evidence["inputRefs"] == [
        "artifact:attachment-1",
        "checkpoint:restore-1",
    ]
    assert all(item["localPath"] is None for item in evidence["materializedInputRefs"])

    (workspace / "dirty.txt").write_text("preserve me", encoding="utf-8")
    retry = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={
            "repository": str(source),
            "startingBranch": "main",
            "targetBranch": "issue-3507",
        },
        input_refs=("artifact:attachment-1", "checkpoint:restore-1"),
        resolved_skillset_ref="artifact:skills-1",
    )
    assert retry == workspace
    assert (retry / "dirty.txt").read_text(encoding="utf-8") == "preserve me"


@pytest.mark.asyncio
async def test_retry_rejects_changed_workspace_intent_before_mutation(tmp_path) -> None:
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=tmp_path)
    locator = {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "relativePath": "repo",
    }
    await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={},
    )

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator=locator,
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
            workspace_spec={"targetBranch": "different"},
        )
    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"
