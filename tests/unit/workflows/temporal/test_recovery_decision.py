from __future__ import annotations

import pytest

from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.recovery_decision import (
    decide_checkpoint_recovery,
    validate_recovery_contract,
)


def _decision(**overrides):
    values = {
        "checkpoint_ref": "artifact://checkpoint",
        "checkpoint_boundary": "before_execution",
        "checkpoint_kind": "external_state_ref",
        "capabilities": resolve_runtime_execution_capabilities("omnigent"),
        "restore_route_registered": True,
        "artifact_valid": True,
        "side_effect_safe": True,
    }
    values.update(overrides)
    return decide_checkpoint_recovery(**values)


def test_checkpoint_resume_requires_complete_restore_proof() -> None:
    decision = _decision()

    assert decision.eligible is True
    assert decision.resume_phase == "rerun_failed_step"
    assert decision.requested_action == "resume_from_workspace_checkpoint"
    assert decision.capability_digest


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"capabilities": resolve_runtime_execution_capabilities("codex_cli")},
         "CHECKPOINT_RESTORE_UNSUPPORTED"),
        ({"restore_route_registered": False}, "CHECKPOINT_RESTORE_ROUTE_MISSING"),
        ({"checkpoint_kind": "worktree_archive"}, "CHECKPOINT_KIND_INCOMPATIBLE"),
        ({"checkpoint_boundary": "after_prepare"}, "CHECKPOINT_BOUNDARY_INCOMPATIBLE"),
        ({"side_effect_safe": False}, "CHECKPOINT_SIDE_EFFECT_UNSAFE"),
        ({"capabilities": None}, "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING"),
    ],
)
def test_checkpoint_resume_fails_closed(overrides, reason) -> None:
    decision = _decision(**overrides)

    assert decision.eligible is False
    assert decision.default_action == "full_retry"
    assert decision.disabled_reason_code == reason


def test_restore_kinds_without_activity_are_rejected_by_capability_schema() -> None:
    with pytest.raises(ValueError, match="declared together"):
        RuntimeExecutionCapabilities(
            runtimeId="broken",
            workspaceAuthority="managed_runtime",
            checkpointRestoreKinds=("worktree_archive",),
            supportsSameSessionContinuation=False,
            postExecutionCheckpointCriticality="unsupported",
        )


def test_workflow_preflight_rejects_stale_phase() -> None:
    decision = _decision().model_dump(by_alias=True, mode="json")
    contract = {
        **decision,
        "recoveryAction": "resume_from_workspace_checkpoint",
        "selectedCheckpointBoundary": "before_execution",
        "resumePhase": "continue_after_gate",
        "checkpointRestoreActivity": decision["restoreActivity"],
        "recoveryCheckpointRef": decision["checkpointRef"],
    }

    with pytest.raises(ValueError, match="CHECKPOINT_BOUNDARY_INCOMPATIBLE"):
        validate_recovery_contract(contract)
