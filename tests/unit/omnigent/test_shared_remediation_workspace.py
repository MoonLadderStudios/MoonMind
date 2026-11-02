"""The generic workspace port must consume controller-owned remediation heads."""

import json
import os
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.remediation_workspace import (
    RemediationLiveWorkspace,
    RemediationWorkspaceError,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    resolve_sandbox_workspace_locator,
)
from tests.unit.omnigent.test_remediation_workspace import _binding, _head
from tests.unit.omnigent.test_workspace_restore_convergence import (
    FakeArtifactService,
    _digest,
    _tar_bytes,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing-head",
        "different-profile",
        "stale-head",
        "different-host",
        "different-policy",
        "different-run",
        "different-locator",
        "raw-path",
        "read-only",
    ],
)
@pytest.mark.parametrize("latest_attempt_save", [False, "snapshot", "archive"])
@pytest.mark.parametrize("different_runtime_uid", [False, True])
async def test_shared_workspace_port_uses_the_existing_remediation_owner(
    tmp_path,
    fault,
    latest_attempt_save,
    different_runtime_uid,
    monkeypatch,
):
    runtime_uid = (os.getuid() or 1000) + int(different_runtime_uid)
    runtime_gid = (os.getgid() or 1000) + int(different_runtime_uid)
    assigned_roots, assigned_files = [], []
    if different_runtime_uid:
        # Record the actual POSIX ownership boundary without requiring a
        # privileged developer process. The Linux journey checks real owners.
        monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: assigned_roots.append((os.fstat(fd).st_ino, uid, gid)))
        def record_chown(path, uid, gid, **kwargs):
            inode = os.stat(path, dir_fd=kwargs.get("dir_fd"), follow_symlinks=False).st_ino
            assigned_files.append((inode, uid, gid, kwargs.get("follow_symlinks")))
        monkeypatch.setattr(os, "chown", record_chown)
    step_id = "workflow-1:run-1:remediate:execution:2"
    binding, head = _binding(step=step_id), _head()
    owner = SandboxRemediationWorkspaceOwner(tmp_path)
    previous = SandboxWorkspaceRecord(
        workspace_id="previous-attempt",
        workflow_id="workflow-1",
        step_execution_id="step-1",
        relative_path="repo",
    )
    owner.records.ensure(previous)
    from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator

    source = resolve_sandbox_workspace_locator(
        SandboxWorkspaceLocator(
            kind="sandbox", workspaceId=previous.workspace_id, relativePath="repo"
        ),
        workspace_root=tmp_path,
        expected_workspace_id=previous.workspace_id,
        owner_record=previous,
        expected_workflow_id="workflow-1",
        expected_step_execution_id="step-1",
        must_exist=False,
    )
    source.mkdir(parents=True)
    source.joinpath("accepted-work.txt").write_text("Preserve the verified loop head.")
    source.joinpath("obsolete.txt").write_text(
        "Removed by the subsequent verified save."
    )
    owner.record_live_workspace(
        RemediationLiveWorkspace(
            loop_id=head.loop_id,
            branch_ref=head.branch_ref,
            checkpoint_ref=head.checkpoint_ref,
            workspace_digest=head.workspace_digest,
            head_version=head.head_version,
            workspace_id=previous.workspace_id,
            workflow_id=previous.workflow_id,
            step_execution_id=previous.step_execution_id,
        )
    )
    head_body = asdict(head)
    if fault == "stale-head":
        head_body["head_version"] += 1
    if fault == "different-profile":
        binding.execution_profile_ref = "another-account"
    if fault == "different-host":
        binding.host_profile_ref = "another-host@1"
    if fault == "different-policy":
        binding.launch_policy_ref = "another-policy@1"
    if fault == "different-run":
        binding.run_id = "another-run"

    archive = _tar_bytes(
        [("accepted-work.txt", b"Later verified attempt work.", "file")]
    )

    class Artifacts(FakeArtifactService):
        def __init__(self):
            super().__init__({"latest-attempt": archive})
            self.reads = []

        async def read(self, *, artifact_id, access):
            assert access.execution_principal == "workflow:workflow-1"
            assert not access.is_operator
            self.reads.append(artifact_id)
            if artifact_id == "profile":
                return object(), json.dumps(
                    {
                        "providerProfileRef": "codex",
                        "executionProfileRef": "omnigent-codex@1",
                    }
                ).encode()
            assert artifact_id == "loop-head/2"
            if fault == "missing-head":
                raise FileNotFoundError("controller head was not admitted")
            return object(), json.dumps(head_body).encode()

    artifacts = Artifacts()
    commands = []

    async def runner(argv, input_bytes=None):
        commands.append(argv)
        return 0, "", ""

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="step-2",
        stepExecution={
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "remediate",
            "executionOrdinal": 2,
            "stepExecutionId": step_id,
            "runtimeContextPolicy": "fresh_agent_run",
        },
        workspaceSpec={
            **({"workspaceSource": {
                    "kind": "checkpoint",
                    "checkpointRef": "artifact://latest-attempt",
                    "checkpointDigest": _digest(archive),
                    "restoreContract": "moonmind.workspace-snapshot.v1",
                }} if latest_attempt_save == "snapshot" else
               {"workspaceCheckpointRestoreRef": "artifact://latest-attempt"} if latest_attempt_save == "archive" else
               {"workspaceSource": {"kind": "scratch"}}),
            "workspaceLocator": binding.destination_workspace_locator.model_dump(
                mode="json", by_alias=True
            ),
        },
        remediationWorkspace=binding.model_dump(mode="json", by_alias=True),
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            agentProfileSnapshotRef="artifact:profile",
            launchPolicyRef="codex-on-demand@1",
            workspaceMutation="allowed",
        )
    )
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner,
        workspace_root=tmp_path,
        artifact_service=artifacts,
    )
    if fault == "different-locator":
        request.workspace_spec["workspaceLocator"]["relativePath"] = "sibling"
    if fault == "raw-path":
        request.workspace_spec["workspacePath"] = str(source)
    if fault:
        with pytest.raises(
            HarnessPlatformError if fault == "raw-path" else RemediationWorkspaceError
        ):
            await materializer.materialize(
                request,
                plan=plan,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                mutation="read_only" if fault == "read-only" else "allowed",
            )
        assert (
            source.joinpath("accepted-work.txt").read_text()
            == "Preserve the verified loop head."
        )
    else:
        attachment = await materializer.materialize(
            request,
            plan=plan,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        destination = Path(attachment["sourceRef"])
        assert destination.joinpath("accepted-work.txt").read_text() == (
            "Later verified attempt work."
            if latest_attempt_save
            else "Preserve the verified loop head."
        )
        assert destination.joinpath("obsolete.txt").exists() is (latest_attempt_save != "snapshot")
        assert not source.exists()
        assert artifacts.reads == ["profile", "loop-head/2"]
        assert attachment["accessMode"] == "read-write"
        if different_runtime_uid:
            assert (destination.stat().st_ino, runtime_uid, runtime_gid) in assigned_roots
            assert (destination.joinpath("accepted-work.txt").stat().st_ino,
                    runtime_uid, runtime_gid, False) in assigned_files
        # Redelivery may refresh inputs, but cannot overwrite new candidate edits.
        destination.joinpath("accepted-work.txt").write_text(
            "Candidate edit after admission."
        )
        repeated = await materializer.materialize(
            request,
            plan=plan,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        assert repeated["sourceRef"] == attachment["sourceRef"]
        assert (
            destination.joinpath("accepted-work.txt").read_text()
            == "Candidate edit after admission."
        )
    assert commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["workflow", "input-snapshot", "runtime-context", "pre-execution"])
async def test_canonical_step_input_is_rejected_before_workspace_transfer(tmp_path, fault):
    from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
    from moonmind.schemas.temporal_models import STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE, StepExecutionIdentityModel
    from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload

    step_id = "workflow-1:run-1:remediate:execution:2"
    binding = _binding(step=step_id)
    payload = build_step_checkpoint_payload(
        identity=StepExecutionIdentityModel(workflowId="different-workflow" if fault == "workflow" else "workflow-1", runId="run-1", logicalStepId="remediate", executionOrdinal=1),
        boundary="before_execution" if fault == "pre-execution" else "after_execution",
        task_input_snapshot_ref="artifact:different-input" if fault == "input-snapshot" else "artifact:original-input",
        workspace={"kind": "worktree_archive", "baseCommit": "a" * 40, "archiveRef": "artifact:archive", "manifestRef": "artifact:manifest"},
        created_at=datetime.now(timezone.utc), plan_ref="admitted-source-plan",
    )
    body = json.dumps(payload).encode()
    reads = []

    class Artifacts:
        async def get_metadata(self, *, artifact_id, access):
            assert artifact_id == "canonical"
            assert access.execution_principal == "workflow:workflow-1" and not access.is_operator
            return SimpleNamespace(content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE, size_bytes=len(body)), [], False, None

        async def read(self, *, artifact_id, access):
            reads.append(artifact_id)
            assert artifact_id == "canonical", "invalid input must not reach the controller head"
            return object(), body

    identity = {"workflowId": "workflow-1", "runId": "run-1", "logicalStepId": "remediate", "executionOrdinal": 2, "stepExecutionId": step_id, "runtimeContextPolicy": "fresh_agent_run"}
    if fault == "runtime-context":
        identity.update(runtimeContextPolicy="reuse_session_same_epoch", runtimeSessionContext={
            "workflowId": "workflow-1", "runId": "run-1", "stepExecutionId": "wrong-source-step",
            "epoch": "accepted-epoch", "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "a" * 64,
        })
    request = AgentExecutionRequest(agentKind="external", agentId="omnigent", executionProfileRef="codex", correlationId="workflow-1", idempotencyKey="attempt-2", stepExecution=identity,
        workspaceSpec={"workspaceCheckpointRestoreRef": "artifact://canonical", "workspaceLocator": binding.destination_workspace_locator.model_dump(mode="json", by_alias=True)},
        remediationWorkspace=binding.model_dump(mode="json", by_alias=True))
    async def no_command(*args, **kwargs):
        raise AssertionError("invalid checkpoint input cannot launch commands")
    port = OmnigentWorkspaceMaterializer(command_runner=no_command, workspace_root=tmp_path, artifact_service=Artifacts())
    plan = SimpleNamespace(payload=SimpleNamespace(authority=SimpleNamespace(taskInputSnapshotRef="artifact:original-input")))
    with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_SOURCE_IDENTITY_MISMATCH"):
        await port.materialize(request, plan=plan, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    assert reads == ["canonical"]
    assert not (tmp_path / "temporal_sandbox").exists()
