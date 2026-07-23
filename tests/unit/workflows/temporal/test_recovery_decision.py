from __future__ import annotations

import pytest

from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    resolve_runtime_execution_capabilities,
)


def test_typed_recovery_admission_reports_all_dimensions() -> None:
    payload = _payload("control_stop")
    payload["continuation"]["newBudgetRef"] = "artifact://new-budget"

    dimensions = admit_recovery_target(payload)

    assert [item.dimension for item in dimensions] == [
        "checkpoint",
        "target",
        "phase",
        "capability",
        "workspace",
        "side_effect",
        "budget",
        "destination",
    ]
    assert all(item.admitted for item in dimensions)


def test_typed_recovery_admission_fails_before_side_effects_with_stable_reasons() -> None:
    payload = _payload("control_stop")
    payload["sideEffectSafe"] = False

    dimensions = admit_recovery_target(payload)

    denied = {
        item.reason_code for item in dimensions if not item.admitted
    }
    assert denied == {
        "RECOVERY_SIDE_EFFECT_UNSAFE",
        "RECOVERY_BUDGET_GRANT_REQUIRED",
    }
    with pytest.raises(
        ValueError,
        match="RECOVERY_SIDE_EFFECT_UNSAFE,RECOVERY_BUDGET_GRANT_REQUIRED",
    ):
        require_admitted_recovery_target(payload)
from moonmind.workflows.temporal.recovery_decision import (
    admit_recovery_target,
    decide_checkpoint_recovery,
    decide_same_session_recovery,
    require_admitted_recovery_target,
    validate_recovery_contract,
)
from tests.unit.schemas.test_workflow_recovery_models import _payload


def test_same_session_continuation_requires_live_capable_session() -> None:
    capabilities = resolve_runtime_execution_capabilities("codex_cli")
    eligible = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=capabilities,
    )
    dead = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=False,
        capabilities=capabilities,
    )
    unsupported = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=resolve_runtime_execution_capabilities("claude_code"),
    )

    assert eligible.eligible is True
    assert eligible.default_action == "continue_same_session"
    assert eligible.checkpoint_ref is None
    assert dead.disabled_reason_code == "SAME_SESSION_UNREACHABLE"
    assert unsupported.disabled_reason_code == "SAME_SESSION_CONTINUATION_UNSUPPORTED"


def _decision(**overrides):
    values = {
        "checkpoint_ref": "artifact://checkpoint",
        "checkpoint_boundary": "before_execution",
        "checkpoint_kind": "worktree_archive",
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
        ({"restore_route_registered": False}, "CHECKPOINT_RESTORE_ROUTE_MISSING"),
        ({"checkpoint_kind": "external_state_ref"}, "CHECKPOINT_KIND_INCOMPATIBLE"),
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


def test_valid_checkpoint_without_recovery_target_has_target_reason() -> None:
    decision = _decision(recovery_target_available=False)

    assert decision.eligible is False
    assert decision.disabled_reason_code == "RECOVERY_TARGET_UNAVAILABLE"
    assert decision.checkpoint_ref == "artifact://checkpoint"


def test_codex_worktree_archive_restore_is_eligible() -> None:
    decision = _decision(
        capabilities=resolve_runtime_execution_capabilities("codex_cli"),
        checkpoint_kind="worktree_archive",
    )

    assert decision.eligible is True
    assert (
        decision.restore_activity
        == "agent_runtime.restore_workspace_checkpoint"
    )


def test_omnigent_partial_evidence_distinguishes_session_from_workspace() -> None:
    decision = _decision(
        checkpoint_kind="external_state_ref",
        live_session_id="session-1",
        session_reachable=True,
    )

    assert decision.eligible is False
    assert decision.session_recoverable is True
    assert decision.workspace_recoverable is False
    assert decision.authoritative_workspace_checkpoint_kind is None
    assert "workspace checkpoint evidence" in decision.partial_recovery_reason


def test_workspace_decision_does_not_infer_session_evidence_from_capability() -> None:
    decision = _decision()

    assert decision.workspace_recoverable is True
    assert decision.session_recoverable is False


def test_same_session_decision_does_not_infer_workspace_evidence_from_capability() -> None:
    decision = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=resolve_runtime_execution_capabilities("omnigent"),
    )

    assert decision.session_recoverable is True
    assert decision.workspace_recoverable is False


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
        "selectedTargetRuntimeId": decision["targetRuntimeId"],
        "selectedCapabilityDigest": decision["capabilityDigest"],
        "registeredRestoreActivity": decision["restoreActivity"],
        "sourceWorkflowId": "workflow-1",
        "sourceRunId": "run-1",
        "checkpointSourceWorkflowId": "workflow-1",
        "checkpointSourceRunId": "run-1",
        "sideEffectSafe": True,
    }

    with pytest.raises(ValueError, match="CHECKPOINT_BOUNDARY_INCOMPATIBLE"):
        validate_recovery_contract(contract)


def test_workflow_preflight_rejects_string_restore_kinds() -> None:
    decision = _decision().model_dump(by_alias=True, mode="json")
    contract = {
        **decision,
        "recoveryAction": "resume_from_workspace_checkpoint",
        "selectedCheckpointBoundary": "before_execution",
        "checkpointRestoreKinds": "worktree_archive",
        "checkpointRestoreActivity": decision["restoreActivity"],
        "recoveryCheckpointRef": decision["checkpointRef"],
        "selectedTargetRuntimeId": decision["targetRuntimeId"],
        "selectedCapabilityDigest": decision["capabilityDigest"],
        "registeredRestoreActivity": decision["restoreActivity"],
        "sourceWorkflowId": "workflow-1",
        "sourceRunId": "run-1",
        "checkpointSourceWorkflowId": "workflow-1",
        "checkpointSourceRunId": "run-1",
        "sideEffectSafe": True,
    }

    with pytest.raises(ValueError, match="CHECKPOINT_KIND_INCOMPATIBLE"):
        validate_recovery_contract(contract)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("selectedTargetRuntimeId", "stale", "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH"),
        ("selectedCapabilityDigest", "stale", "CHECKPOINT_CAPABILITY_DIGEST_MISMATCH"),
        ("registeredRestoreActivity", "missing.route", "CHECKPOINT_RESTORE_ROUTE_MISSING"),
        ("checkpointSourceRunId", "stale", "CHECKPOINT_ARTIFACT_INVALID"),
        ("sideEffectSafe", False, "CHECKPOINT_SIDE_EFFECT_UNSAFE"),
    ],
)
def test_workflow_preflight_rejects_contradictory_restore_proof(field, value, reason) -> None:
    decision = _decision().model_dump(by_alias=True, mode="json")
    contract = {
        **decision,
        "recoveryAction": "resume_from_workspace_checkpoint",
        "selectedCheckpointBoundary": "before_execution",
        "checkpointRestoreActivity": decision["restoreActivity"],
        "recoveryCheckpointRef": decision["checkpointRef"],
        "selectedTargetRuntimeId": decision["targetRuntimeId"],
        "selectedCapabilityDigest": decision["capabilityDigest"],
        "registeredRestoreActivity": decision["restoreActivity"],
        "sourceWorkflowId": "workflow-1",
        "sourceRunId": "run-1",
        "checkpointSourceWorkflowId": "workflow-1",
        "checkpointSourceRunId": "run-1",
        "sideEffectSafe": True,
    }
    contract[field] = value

    with pytest.raises(ValueError, match=reason):
        validate_recovery_contract(contract)
