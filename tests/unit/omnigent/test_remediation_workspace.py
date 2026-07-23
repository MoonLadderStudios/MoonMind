import hashlib
import json

import pytest
from pydantic import ValidationError

from moonmind.omnigent.remediation_workspace import (
    RemediationWorkspaceBinding,
    RemediationWorkspaceError,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
)


_DIGEST = "sha256:" + "a" * 64


def _binding(**overrides):
    payload = {
        "loopId": "loop-01",
        "branchRef": "checkpoint-branch:loop-01",
        "attemptOrdinal": 2,
        "workflowId": "workflow-1",
        "stepExecutionId": "step-2",
        "baseCheckpointRef": "artifact://workspace/C1",
        "baseWorkspaceDigest": _DIGEST,
        "expectedHeadVersion": 2,
        "currentHeadCheckpointRef": "artifact://workspace/C1",
        "currentHeadWorkspaceDigest": _DIGEST,
        "currentHeadVersion": 2,
        "destinationWorkspaceLocator": {
            "kind": "sandbox",
            "workspaceId": hashlib.sha256(b"workflow-1:step-2").hexdigest()[:24],
            "relativePath": "repo",
        },
        "workspacePolicy": "continue_from_loop_head",
        "restoreEvidenceRef": "artifact://restore/R2",
        "restoreManifestRef": "artifact://restore/M2",
        "restoreBaseCommit": "abc123",
    }
    payload.update(overrides)
    return RemediationWorkspaceBinding.model_validate(payload)


@pytest.mark.parametrize(
    "override",
    [
        {"currentHeadCheckpointRef": "artifact://workspace/stale"},
        {"currentHeadWorkspaceDigest": "sha256:" + "b" * 64},
        {"currentHeadVersion": 3},
    ],
)
def test_binding_rejects_stale_loop_head(override) -> None:
    with pytest.raises(ValidationError, match="current loop head"):
        _binding(**override)


@pytest.mark.asyncio
async def test_owner_resolves_only_matching_restored_attempt(tmp_path) -> None:
    owner = SandboxRemediationWorkspaceOwner(tmp_path)
    binding = _binding()
    locator = binding.destination_workspace_locator
    owner.records.ensure(
        SandboxWorkspaceRecord(
            workspace_id=locator.workspace_id,
            workflow_id="workflow-1",
            step_execution_id="step-2",
            relative_path="repo",
        )
    )
    repo = tmp_path / "temporal_sandbox" / locator.workspace_id / "repo"
    repo.mkdir(parents=True)
    (repo / "marker-a").write_text("A", encoding="utf-8")
    evidence = {
        "loopId": binding.loop_id,
        "branchRef": binding.branch_ref,
        "stepExecutionId": "step-2",
        "checkpointRef": binding.base_checkpoint_ref,
        "workspaceDigest": binding.base_workspace_digest,
        "headVersion": binding.expected_head_version,
        "restoreEvidenceRef": binding.restore_evidence_ref,
        "restoreManifestRef": binding.restore_manifest_ref,
        "baseCommit": binding.restore_base_commit,
    }
    evidence_path = owner.records.store_root / f"{locator.workspace_id}.restore.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    resolved = await owner.admit_and_resolve(
        binding=binding,
        workflow_id="workflow-1",
        step_execution_id="step-2",
    )

    assert resolved["workspacePath"] == str(repo)
    assert resolved["workspaceState"] == "cold_restored"
    assert (repo / "marker-a").read_text(encoding="utf-8") == "A"


@pytest.mark.asyncio
async def test_owner_fails_closed_without_restore_evidence(tmp_path) -> None:
    owner = SandboxRemediationWorkspaceOwner(tmp_path)
    binding = _binding()
    locator = binding.destination_workspace_locator
    owner.records.ensure(
        SandboxWorkspaceRecord(
            workspace_id=locator.workspace_id,
            workflow_id="workflow-1",
            step_execution_id="step-2",
            relative_path="repo",
        )
    )
    (tmp_path / "temporal_sandbox" / locator.workspace_id / "repo").mkdir(
        parents=True
    )

    with pytest.raises(
        RemediationWorkspaceError,
        match="REMEDIATION_WORKSPACE_RESTORE_UNVERIFIED",
    ):
        await owner.admit_and_resolve(
            binding=binding,
            workflow_id="workflow-1",
            step_execution_id="step-2",
        )
