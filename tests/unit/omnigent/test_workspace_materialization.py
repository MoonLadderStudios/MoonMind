import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


class _Artifacts:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    async def read(self, *, artifact_id: str, **_kwargs):
        return {}, self.payloads[artifact_id]


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
            "publishMode": "pr",
            "publishBaseBranch": "main",
            "commitMessage": "Implement issue 3507",
            "repositoryMutationRequired": True,
            "requiredCapabilities": ["gh"],
            "githubCredentialRequired": True,
        },
        input_refs=("artifact:attachment-1", "checkpoint:restore-1"),
        resolved_skillset_ref="artifact:skills-1",
        artifact_gateway=_Artifacts(
            {"attachment-1": b"attachment", "restore-1": b"checkpoint"}
        ),
    )

    assert _git("branch", "--show-current", cwd=workspace) == "issue-3507"
    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["sourceCommit"] == source_commit
    assert evidence["sourceIdentity"] == str(source)
    assert "issueRef" not in evidence
    assert evidence["targetBranch"] == "issue-3507"
    assert evidence["publishMode"] == "pr"
    assert evidence["publishBaseBranch"] == "main"
    assert evidence["commitMessage"] == "Implement issue 3507"
    assert evidence["repositoryMutationRequired"] is True
    assert evidence["requiredCapabilities"] == ["gh"]
    assert evidence["githubCredentialRequired"] is True
    assert evidence["inputRefs"] == [
        "artifact:attachment-1",
        "checkpoint:restore-1",
    ]
    assert [item["artifactRef"] for item in evidence["materializedInputRefs"]] == [
        "artifact:attachment-1",
        "checkpoint:restore-1",
    ]
    assert [
        (workspace / item["localPath"]).read_bytes()
        for item in evidence["materializedInputRefs"]
    ] == [b"attachment", b"checkpoint"]

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
            "publishMode": "pr",
            "publishBaseBranch": "main",
            "commitMessage": "Implement issue 3507",
            "repositoryMutationRequired": True,
            "requiredCapabilities": ["gh"],
            "githubCredentialRequired": True,
        },
        input_refs=("artifact:attachment-1", "checkpoint:restore-1"),
        resolved_skillset_ref="artifact:skills-1",
        artifact_gateway=_Artifacts(
            {"attachment-1": b"attachment", "restore-1": b"checkpoint"}
        ),
    )
    assert retry == workspace
    assert (retry / "dirty.txt").read_text(encoding="utf-8") == "preserve me"


@pytest.mark.asyncio
async def test_checkpoint_restored_managed_workspace_is_adopted_in_place(tmp_path) -> None:
    authority = tmp_path / "managed_runtime"
    workspace = authority / "runs" / "agent-run-1" / "repo"
    workspace.mkdir(parents=True)
    (workspace / "restored.txt").write_text("checkpoint state", encoding="utf-8")
    store = ManagedRunStore(authority / "records")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="workflow-1",
            agentId="omnigent",
            runtimeId="codex_cli",
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    runtime = OmnigentOAuthHostRuntime(
        client=None,
        workspace_root=tmp_path,
        managed_run_store=store,
    )

    resolved = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "managed_runtime",
            "runtimeId": "codex_cli",
            "agentRunId": "agent-run-1",
            "relativePath": ".",
        },
        current_workflow_id="workflow-1",
        current_step_execution_id="agent-run-1",
        workspace_spec={"sourceIdentity": "checkpoint:boundary-1"},
    )

    assert resolved == workspace
    assert (resolved / "restored.txt").read_text(encoding="utf-8") == "checkpoint state"
    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["sourceIdentity"] == "checkpoint:boundary-1"
    assert evidence["workspaceLocator"]["kind"] == "managed_runtime"


@pytest.mark.asyncio
async def test_external_state_locator_materializes_ref_without_using_it_as_path(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=tmp_path)

    workspace = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "external_state",
            "artifactRef": "artifact:external-state-1",
        },
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        artifact_gateway=_Artifacts({"external-state-1": b"restored state"}),
    )

    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["workspaceLocator"]["kind"] == "external_state"
    assert evidence["inputRefs"] == ["artifact:external-state-1"]
    local_path = evidence["materializedInputRefs"][0]["localPath"]
    assert (workspace / local_path).read_bytes() == b"restored state"
    assert "external-state-1" not in str(workspace)


@pytest.mark.asyncio
async def test_managed_workspace_rejects_workflow_identity_mismatch(tmp_path) -> None:
    authority = tmp_path / "managed_runtime"
    workspace = authority / "runs" / "agent-run-1" / "repo"
    workspace.mkdir(parents=True)
    store = ManagedRunStore(authority / "records")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="different-workflow",
            agentId="omnigent",
            runtimeId="codex_cli",
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    runtime = OmnigentOAuthHostRuntime(
        client=None,
        workspace_root=tmp_path,
        managed_run_store=store,
    )

    with pytest.raises(ValueError, match="WORKSPACE_IDENTITY_MISMATCH"):
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": "agent-run-1",
                "relativePath": ".",
            },
            current_workflow_id="workflow-1",
            current_step_execution_id="agent-run-1",
        )

    assert not (workspace.parent / ".moonmind-workspace.json").exists()


@pytest.mark.asyncio
async def test_managed_workspace_rejects_runtime_identity_mismatch(tmp_path) -> None:
    authority = tmp_path / "managed_runtime"
    workspace = authority / "runs" / "agent-run-1" / "repo"
    workspace.mkdir(parents=True)
    store = ManagedRunStore(authority / "records")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            agentId="omnigent",
            runtimeId="claude_code",
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    runtime = OmnigentOAuthHostRuntime(
        client=None,
        workspace_root=tmp_path,
        managed_run_store=store,
    )

    with pytest.raises(ValueError, match="WORKSPACE_IDENTITY_MISMATCH"):
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "managed_runtime",
                "runtimeId": "claude_code",
                "agentRunId": "agent-run-1",
                "relativePath": ".",
            },
            current_workflow_id="workflow-1",
            current_step_execution_id="agent-run-1",
        )

    assert not (workspace.parent / ".moonmind-workspace.json").exists()


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


@pytest.mark.asyncio
async def test_stale_owner_is_rejected_before_workspace_mutation(tmp_path) -> None:
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    store = SandboxWorkspaceRecordStore(tmp_path)
    store.ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id="different-workflow",
            step_execution_id=step_id,
            relative_path="repo",
            intent_digest="stale",
        )
    )
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=tmp_path)

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
            workspace_spec={},
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"
    assert not (tmp_path / "temporal_sandbox" / workspace_id / "repo").exists()
