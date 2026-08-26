from __future__ import annotations

import pytest

from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.recovery_state import (
    CHECKPOINT_BOUNDARY_INCOMPATIBLE,
    CHECKPOINT_CAPABILITY_INVALID,
    CHECKPOINT_RESTORATION_NOT_READY,
    CHECKPOINT_SIDE_EFFECT_UNSAFE,
    CheckpointRecoveryContract,
    RecoveryContractError,
    deterministic_recovery_identity,
    validate_restore_result,
)


def _capabilities() -> dict[str, object]:
    return RuntimeExecutionCapabilities(
        runtimeId="codex_cli",
        runtimeFamily="managed_cli",
        workspaceAuthority="managed_runtime",
        checkpointRestoreKinds=("worktree_archive",),
        checkpointRestoreActivity="agent_runtime.restore_workspace_checkpoint",
        checkpointArtifactContractVersion="managed-worktree-archive-v1",
        supportsSameSessionContinuation=True,
        postExecutionCheckpointCriticality="recoverability_only",
    ).with_digest().model_dump(by_alias=True)


def _contract(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "recoveryAction": "resume_from_workspace_checkpoint",
        "resumePhase": "rerun_failed_step",
        "sourceCheckpointRef": "artifact://checkpoint/1",
        "sourceCheckpointBoundary": "before_execution",
        "sourceCheckpointKind": "worktree_archive",
        "workspacePolicy": "restore_pre_execution",
        "capabilitySnapshot": _capabilities(),
        "restoreActivity": "agent_runtime.restore_workspace_checkpoint",
    }
    payload.update(updates)
    return payload


def test_valid_contract_and_deterministic_destination_identity() -> None:
    contract = CheckpointRecoveryContract.model_validate(_contract())
    first = deterministic_recovery_identity(
        workflow_id="wf", run_id="run", logical_step_id="implement",
        execution_ordinal=1, checkpoint_ref=contract.source_checkpoint_ref,
    )
    second = deterministic_recovery_identity(
        workflow_id="wf", run_id="run", logical_step_id="implement",
        execution_ordinal=1, checkpoint_ref=contract.source_checkpoint_ref,
    )
    assert first == second
    assert first[0] == "wf:restore:implement:execution:1"


def test_v3_omnigent_snapshot_authorizes_workspace_boundary_without_registry_lookup() -> None:
    snapshot = resolve_runtime_execution_capabilities("omnigent").model_dump(by_alias=True)
    contract = CheckpointRecoveryContract.model_validate(
        _contract(
            resumePhase="continue_to_gate",
            sourceCheckpointBoundary="after_execution",
            capabilitySnapshot=snapshot,
            restoreActivity="workspace.apply_checkpoint",
        )
    )

    assert contract.capabilities.workspace_state.authority == "moonmind_sandbox"
    assert contract.source_checkpoint_kind == "worktree_archive"


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"resumePhase": "continue_to_gate"}, CHECKPOINT_BOUNDARY_INCOMPATIBLE),
        ({"restoreActivity": "workspace.apply_policy"}, CHECKPOINT_CAPABILITY_INVALID),
        ({"sourceCheckpointKind": "git_patch"}, CHECKPOINT_CAPABILITY_INVALID),
        ({"sideEffectDispositions": [{"disposition": "accepted", "idempotent": False}]}, CHECKPOINT_SIDE_EFFECT_UNSAFE),
        ({"sourceCheckpointBoundary": "before_publication", "resumePhase": "resume_publication"}, CHECKPOINT_BOUNDARY_INCOMPATIBLE),
    ],
)
def test_preflight_fails_closed(updates: dict[str, object], code: str) -> None:
    with pytest.raises(ValueError) as exc:
        CheckpointRecoveryContract.model_validate(_contract(**updates))
    assert code in str(exc.value)


def test_restore_result_requires_matching_locator_and_evidence() -> None:
    result = {
        "status": "succeeded",
        "destinationWorkspaceLocator": {
            "kind": "managed_runtime", "runtimeId": "codex_cli",
            "agentRunId": "destination", "relativePath": "repo",
        },
        "restorationEvidenceRef": "artifact://restore/evidence",
        "restorationEvidenceDigest": "sha256:abc",
    }
    locator, ref, digest = validate_restore_result(
        result, runtime_id="codex_cli", destination_agent_run_id="destination"
    )
    assert locator["agentRunId"] == "destination"
    assert (ref, digest) == ("artifact://restore/evidence", "sha256:abc")

    result.pop("restorationEvidenceRef")
    with pytest.raises(RecoveryContractError) as exc:
        validate_restore_result(
            result, runtime_id="codex_cli", destination_agent_run_id="destination"
        )
    assert exc.value.code == CHECKPOINT_RESTORATION_NOT_READY
