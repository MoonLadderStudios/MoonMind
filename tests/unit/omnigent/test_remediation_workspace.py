import hashlib
from pathlib import Path

import pytest

from moonmind.omnigent.remediation_workspace import (
    RemediationLiveWorkspace,
    RemediationLoopHead,
    RemediationWorkspaceBinding,
    RemediationWorkspaceError,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
)


_DIGEST = "sha256:" + "a" * 64


def _binding(step="step-2", ordinal=2, checkpoint="artifact://workspace/C1", version=2):
    return RemediationWorkspaceBinding.model_validate({
        "loopId": "loop-01", "branchRef": "checkpoint-branch:loop-01",
        "attemptOrdinal": ordinal, "workflowId": "workflow-1",
        "stepExecutionId": step, "baseCheckpointRef": checkpoint,
        "baseWorkspaceDigest": _DIGEST, "expectedHeadVersion": version,
        "headAuthorityRef": f"artifact://loop-head/{version}",
        "destinationWorkspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(f"workflow-1:{step}".encode()).hexdigest()[:24],
            "relativePath": "repo",
        },
        "workspacePolicy": "continue_from_loop_head",
        "executionProfileRef": "codex", "hostProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "workspaceCapabilitySnapshot": {"locatorKind": "sandbox", "restore": True},
    })


def _head(checkpoint="artifact://workspace/C1", version=2):
    return RemediationLoopHead(
        loop_id="loop-01", branch_ref="checkpoint-branch:loop-01",
        checkpoint_ref=checkpoint, workspace_digest=_DIGEST, head_version=version,
        base_commit="abc123", manifest_ref=f"artifact://manifest/{version}",
    )


class _Restorer:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    async def restore(self, *, head, destination: Path, idempotency_key):
        self.calls.append((head.checkpoint_ref, idempotency_key))
        destination.mkdir(parents=True, exist_ok=True)
        for name, value in self.snapshots[head.checkpoint_ref].items():
            (destination / name).write_text(value, encoding="utf-8")
        return {
            "checkpointRef": head.checkpoint_ref,
            "workspaceDigest": head.workspace_digest,
            "baseCommit": head.base_commit,
            "manifestRef": head.manifest_ref,
            "restoreEvidenceRef": f"artifact://restore/{head.head_version}",
        }


@pytest.mark.asyncio
async def test_owner_rejects_request_self_attestation_without_durable_head(tmp_path) -> None:
    owner = SandboxRemediationWorkspaceOwner(tmp_path, restorer=_Restorer({}))
    with pytest.raises(RemediationWorkspaceError, match="REMEDIATION_LOOP_HEAD_MISSING"):
        await owner.admit_and_resolve(
            binding=_binding(), workflow_id="workflow-1", step_execution_id="step-2"
        )


@pytest.mark.asyncio
async def test_owner_cold_restores_idempotent_attempt_destination(tmp_path) -> None:
    restorer = _Restorer({"artifact://workspace/C1": {"marker-a": "A"}})
    owner = SandboxRemediationWorkspaceOwner(tmp_path, restorer=restorer)
    owner.record_loop_head(_head())
    binding = _binding()
    result = await owner.admit_and_resolve(
        binding=binding, workflow_id="workflow-1", step_execution_id="step-2"
    )
    assert result["workspaceState"] == "cold_restored"
    assert (Path(result["workspacePath"]) / "marker-a").read_text() == "A"
    assert restorer.calls == [("artifact://workspace/C1", "workflow-1:step-2:restore")]


@pytest.mark.asyncio
async def test_owner_reuses_only_authoritative_uncontended_live_head(tmp_path) -> None:
    owner = SandboxRemediationWorkspaceOwner(tmp_path, restorer=_Restorer({}))
    owner.record_loop_head(_head())
    live_id = "prior-attempt-workspace"
    owner.records.ensure(SandboxWorkspaceRecord(
        workspace_id=live_id, workflow_id="workflow-1", step_execution_id="step-1",
        relative_path="repo",
    ))
    live_path = tmp_path / "temporal_sandbox" / live_id / "repo"
    live_path.mkdir(parents=True)
    (live_path / "marker-a").write_text("A")
    owner.record_live_workspace(RemediationLiveWorkspace(
        loop_id="loop-01", branch_ref="checkpoint-branch:loop-01",
        checkpoint_ref="artifact://workspace/C1", workspace_digest=_DIGEST,
        head_version=2, workspace_id=live_id, workflow_id="workflow-1",
        step_execution_id="step-1",
    ))
    binding = _binding()
    result = await owner.admit_and_resolve(
        binding=binding, workflow_id="workflow-1", step_execution_id="step-2"
    )
    assert result["workspaceState"] == "live_reused"
    assert result["workspaceLocator"]["workspaceId"] == binding.destination_workspace_locator.workspace_id
    assert (Path(result["workspacePath"]) / "marker-a").read_text() == "A"


@pytest.mark.asyncio
async def test_two_cold_attempts_preserve_cumulative_markers_after_source_destruction(tmp_path) -> None:
    snapshots = {
        "artifact://workspace/C0": {},
        "artifact://workspace/C1": {"marker-a": "A"},
    }
    restorer = _Restorer(snapshots)
    owner = SandboxRemediationWorkspaceOwner(tmp_path, restorer=restorer)

    owner.record_loop_head(_head("artifact://workspace/C0", 1))
    first = await owner.admit_and_resolve(
        binding=_binding("step-1", 1, "artifact://workspace/C0", 1),
        workflow_id="workflow-1", step_execution_id="step-1",
    )
    first_path = Path(first["workspacePath"])
    (first_path / "marker-a").write_text("A")
    # Host/session/process identity is attempt-local; deleting the materialized
    # source proves the next attempt relies on checkpoint authority, not its host.
    for item in first_path.iterdir():
        item.unlink()

    owner.record_loop_head(_head("artifact://workspace/C1", 2))
    second = await owner.admit_and_resolve(
        binding=_binding("step-2", 2, "artifact://workspace/C1", 2),
        workflow_id="workflow-1", step_execution_id="step-2",
    )
    second_path = Path(second["workspacePath"])
    assert (second_path / "marker-a").read_text() == "A"
    (second_path / "marker-b").write_text("B")
    assert {path.name for path in second_path.iterdir()} == {"marker-a", "marker-b"}
    assert restorer.calls[0][1] != restorer.calls[1][1]
