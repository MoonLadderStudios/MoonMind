"""Pure, fail-closed recovery operation decisions (MoonMind#3272)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from moonmind.schemas.temporal_models import RecoveryEligibilityDiagnosticModel
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
)

BOUNDARY_RESUME_PHASE = {
    "before_execution": "rerun_failed_step",
    "after_execution": "continue_to_gate",
    "after_gate": "continue_after_gate",
    "before_publication": "resume_publication",
    "before_recovery_restoration": "retry_restoration",
}


def decide_same_session_recovery(
    *,
    live_session_id: str | None,
    session_reachable: bool,
    workspace_checkpoint_valid: bool = False,
    capabilities: RuntimeExecutionCapabilities | None,
    evidence: Sequence[Any] = (),
) -> RecoveryEligibilityDiagnosticModel:
    """Decide live continuation without using cold-checkpoint evidence."""

    supported = bool(
        capabilities and (
            capabilities.session_state.supports_live_reattach
            if capabilities.session_state else capabilities.supports_same_session_continuation
        )
    )
    reason = None
    if not supported:
        reason = "SAME_SESSION_CONTINUATION_UNSUPPORTED"
    elif not live_session_id or not session_reachable:
        reason = "SAME_SESSION_UNREACHABLE"
    eligible = reason is None
    capability_dump = (
        capabilities.model_dump(by_alias=True, mode="json") if capabilities else {}
    )
    return RecoveryEligibilityDiagnosticModel(
        eligible=eligible,
        requestedAction="continue_same_session",
        defaultAction="continue_same_session" if eligible else "full_retry",
        disabledReasonCode=reason,
        targetRuntimeId=capability_dump.get("runtimeId"),
        capabilitySetVersion=capability_dump.get("capabilitySetVersion"),
        capabilityDigest=capability_dump.get("capabilityDigest"),
        workspaceAuthority=capability_dump.get("workspaceAuthority"),
        sessionRecoverable=eligible,
        workspaceRecoverable=workspace_checkpoint_valid,
        authoritativeWorkspaceCheckpointKind=(
            capabilities.checkpoint_restore_kinds[0]
            if workspace_checkpoint_valid and capabilities
            and capabilities.checkpoint_restore_kinds else None
        ),
        partialRecoveryReason=(
            "Session continuity is unavailable; workspace recovery is independent."
            if not eligible and workspace_checkpoint_valid else None
        ),
        liveSessionId=live_session_id,
        supportsSameSessionContinuation=supported,
        operatorGuidance="continue_same_session" if eligible else "full_retry",
        evidence=list(evidence),
    )


def decide_checkpoint_recovery(
    *,
    checkpoint_ref: str | None,
    checkpoint_boundary: str | None,
    checkpoint_kind: str | None,
    capabilities: RuntimeExecutionCapabilities | None,
    restore_route_registered: bool,
    artifact_valid: bool,
    side_effect_safe: bool,
    recovery_target_available: bool = True,
    live_session_id: str | None = None,
    session_reachable: bool = False,
    source_workflow_id: str | None = None,
    source_run_id: str | None = None,
    evidence: Sequence[Any] = (),
) -> RecoveryEligibilityDiagnosticModel:
    """Return the canonical decision without inferring capability from identity."""

    reason: str | None = None
    phase = BOUNDARY_RESUME_PHASE.get(str(checkpoint_boundary or "").strip())
    if not checkpoint_ref or not artifact_valid:
        reason = "CHECKPOINT_ARTIFACT_INVALID"
    elif not recovery_target_available:
        reason = "RECOVERY_TARGET_UNAVAILABLE"
    elif not phase:
        reason = "CHECKPOINT_BOUNDARY_INCOMPATIBLE"
    elif capabilities is None:
        reason = "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING"
    elif not capabilities.capability_digest:
        reason = "CHECKPOINT_CAPABILITY_DIGEST_MISMATCH"
    elif not capabilities.checkpoint_restore_kinds:
        reason = "CHECKPOINT_RESTORE_UNSUPPORTED"
    elif not checkpoint_kind or checkpoint_kind not in capabilities.checkpoint_restore_kinds:
        reason = "CHECKPOINT_KIND_INCOMPATIBLE"
    elif not capabilities.checkpoint_restore_activity or not restore_route_registered:
        reason = "CHECKPOINT_RESTORE_ROUTE_MISSING"
    elif not side_effect_safe:
        reason = "CHECKPOINT_SIDE_EFFECT_UNSAFE"

    capability_dump: Mapping[str, Any] = (
        capabilities.model_dump(by_alias=True, mode="json") if capabilities else {}
    )
    eligible = reason is None
    return RecoveryEligibilityDiagnosticModel(
        eligible=eligible,
        requestedAction="resume_from_workspace_checkpoint",
        defaultAction="resume_from_workspace_checkpoint" if eligible else "full_retry",
        disabledReasonCode=reason,
        checkpointRef=checkpoint_ref,
        checkpointBoundary=checkpoint_boundary,
        resumePhase=phase,
        checkpointKind=checkpoint_kind,
        targetRuntimeId=capability_dump.get("runtimeId"),
        capabilitySetVersion=capability_dump.get("capabilitySetVersion"),
        capabilityDigest=capability_dump.get("capabilityDigest"),
        checkpointRestoreKinds=capability_dump.get("checkpointRestoreKinds", ()),
        restoreActivity=capability_dump.get("checkpointRestoreActivity"),
        workspaceAuthority=capability_dump.get("workspaceAuthority"),
        sessionRecoverable=bool(
            live_session_id and session_reachable and capabilities
            and capabilities.session_state
            and capabilities.session_state.supports_live_reattach
        ),
        workspaceRecoverable=eligible,
        authoritativeWorkspaceCheckpointKind=(
            checkpoint_kind if checkpoint_kind in tuple(capability_dump.get("checkpointRestoreKinds", ())) else None
        ),
        partialRecoveryReason=(
            "Session state is recoverable, but authoritative workspace checkpoint evidence is unavailable."
            if not eligible and live_session_id and session_reachable
            and capabilities and capabilities.session_state
            and capabilities.session_state.supports_live_reattach else None
        ),
        sourceWorkflowId=source_workflow_id,
        sourceRunId=source_run_id,
        operatorGuidance=(
            "resume_from_workspace_checkpoint" if eligible else "full_retry"
        ),
        evidence=list(evidence),
    )


def validate_recovery_contract(contract: Mapping[str, Any]) -> None:
    """Workflow-safe defense-in-depth check before workspace mutation."""

    if contract.get("recoveryAction") != "resume_from_workspace_checkpoint":
        raise ValueError("Recovery action must be resume_from_workspace_checkpoint.")
    boundary = str(contract.get("selectedCheckpointBoundary") or "").strip()
    phase = str(contract.get("resumePhase") or "").strip()
    if BOUNDARY_RESUME_PHASE.get(boundary) != phase:
        raise ValueError("CHECKPOINT_BOUNDARY_INCOMPATIBLE")
    required = (
        "targetRuntimeId", "capabilitySetVersion", "capabilityDigest",
        "checkpointKind", "checkpointRestoreKinds", "checkpointRestoreActivity",
        "workspaceAuthority", "recoveryCheckpointRef",
    )
    if any(not contract.get(field) for field in required):
        raise ValueError("CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING")
    restore_kinds = contract["checkpointRestoreKinds"]
    if not isinstance(restore_kinds, (list, tuple)) or (
        contract["checkpointKind"] not in restore_kinds
    ):
        raise ValueError("CHECKPOINT_KIND_INCOMPATIBLE")
    if contract.get("targetRuntimeId") != contract.get("selectedTargetRuntimeId"):
        raise ValueError("CHECKPOINT_DESTINATION_IDENTITY_MISMATCH")
    if contract.get("capabilityDigest") != contract.get("selectedCapabilityDigest"):
        raise ValueError("CHECKPOINT_CAPABILITY_DIGEST_MISMATCH")
    if contract.get("checkpointRestoreActivity") != contract.get("registeredRestoreActivity"):
        raise ValueError("CHECKPOINT_RESTORE_ROUTE_MISSING")
    if (
        contract.get("sourceWorkflowId") != contract.get("checkpointSourceWorkflowId")
        or contract.get("sourceRunId") != contract.get("checkpointSourceRunId")
    ):
        raise ValueError("CHECKPOINT_ARTIFACT_INVALID")
    if contract.get("sideEffectSafe") is not True:
        raise ValueError("CHECKPOINT_SIDE_EFFECT_UNSAFE")
