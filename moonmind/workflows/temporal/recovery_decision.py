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


def decide_checkpoint_recovery(
    *,
    checkpoint_ref: str | None,
    checkpoint_boundary: str | None,
    checkpoint_kind: str | None,
    capabilities: RuntimeExecutionCapabilities | None,
    restore_route_registered: bool,
    artifact_valid: bool,
    side_effect_safe: bool,
    source_workflow_id: str | None = None,
    source_run_id: str | None = None,
    evidence: Sequence[Any] = (),
) -> RecoveryEligibilityDiagnosticModel:
    """Return the canonical decision without inferring capability from identity."""

    reason: str | None = None
    phase = BOUNDARY_RESUME_PHASE.get(str(checkpoint_boundary or "").strip())
    if not checkpoint_ref or not artifact_valid:
        reason = "CHECKPOINT_ARTIFACT_INVALID"
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
    if contract["checkpointKind"] not in contract["checkpointRestoreKinds"]:
        raise ValueError("CHECKPOINT_KIND_INCOMPATIBLE")
