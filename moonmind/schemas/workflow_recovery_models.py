"""Canonical typed workflow recovery target contract (MoonMind#3478).

The contract is deliberately ref-only and immutable.  It is validated before
any destination workspace is reserved or any recovery activity is scheduled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecoveryTargetKind = Literal[
    "failed_step", "control_stop", "publication", "restoration_failure"
]
RecoveryContinuationPhase = Literal[
    "rerun_failed_step",
    "continue_to_gate",
    "continue_after_gate",
    "continue_to_remediation",
    "resume_publication",
    "retry_restoration",
]

RECOVERY_ADMISSION_REASONS = (
    "RECOVERY_TARGET_MISSING",
    "RECOVERY_TARGET_UNSUPPORTED",
    "RECOVERY_TARGET_IDENTITY_MISMATCH",
    "RECOVERY_PHASE_UNSUPPORTED",
    "RECOVERY_CHECKPOINT_INVALID",
    "RECOVERY_CHECKPOINT_BOUNDARY_INCOMPATIBLE",
    "RECOVERY_CAPABILITY_SNAPSHOT_MISSING",
    "RECOVERY_CAPABILITY_DIGEST_MISMATCH",
    "RECOVERY_WORKSPACE_RESTORE_UNSUPPORTED",
    "RECOVERY_SIDE_EFFECT_UNSAFE",
    "RECOVERY_BUDGET_GRANT_REQUIRED",
    "RECOVERY_DESTINATION_IDENTITY_MISMATCH",
    "RECOVERY_PUBLICATION_RECONCILIATION_REQUIRED",
)

TARGET_PHASE_BOUNDARIES: dict[str, dict[str, tuple[str, ...]]] = {
    "failed_step": {"rerun_failed_step": ("before_execution",)},
    "control_stop": {
        "continue_to_gate": ("after_execution",),
        "continue_after_gate": ("after_gate",),
        "continue_to_remediation": ("after_gate",),
    },
    "publication": {"resume_publication": ("before_publication",)},
    "restoration_failure": {
        "retry_restoration": ("before_recovery_restoration",)
    },
}


class RecoveryTargetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    kind: RecoveryTargetKind
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    source_step_execution_id: str | None = Field(
        None, alias="sourceStepExecutionId"
    )
    control_stop_kind: str | None = Field(None, alias="controlStopKind")
    reason_code: str | None = Field(None, alias="reasonCode")
    gate_result_ref: str | None = Field(None, alias="gateResultRef")
    accepted_candidate_ref: str | None = Field(None, alias="acceptedCandidateRef")
    publication_observation_ref: str | None = Field(
        None, alias="publicationObservationRef"
    )
    publication_idempotency_key: str | None = Field(
        None, alias="publicationIdempotencyKey"
    )
    restore_operation_id: str | None = Field(None, alias="restoreOperationId")
    restore_idempotency_key: str | None = Field(
        None, alias="restoreIdempotencyKey"
    )
    partial_restoration_ref: str | None = Field(
        None, alias="partialRestorationRef"
    )


class RecoverySourceIdentityModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    plan_ref: str | None = Field(None, alias="planRef")
    plan_digest: str | None = Field(None, alias="planDigest")
    task_input_snapshot_ref: str = Field(
        ..., alias="taskInputSnapshotRef", min_length=1
    )


class RecoveryCheckpointTargetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    ref: str = Field(..., min_length=1)
    boundary: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    digest: str = Field(..., min_length=1)
    validation_ref: str = Field(..., alias="validationRef", min_length=1)
    source_workspace_ref: str | None = Field(None, alias="sourceWorkspaceRef")


class RecoveryContinuationModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    phase: RecoveryContinuationPhase
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")
    workspace_head_ref: str | None = Field(None, alias="workspaceHeadRef")
    prior_budget_ref: str | None = Field(None, alias="priorBudgetRef")
    new_budget_ref: str | None = Field(None, alias="newBudgetRef")


class RecoveryDestinationModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str | None = Field(None, alias="runId")
    creation_key: str = Field(..., alias="creationKey", min_length=1)
    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    execution_profile_ref: str = Field(
        ..., alias="executionProfileRef", min_length=1
    )
    workspace_reservation_id: str = Field(
        ..., alias="workspaceReservationId", min_length=1
    )


class RecoveryAdmissionDimensionModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    dimension: Literal[
        "checkpoint", "target", "phase", "capability", "workspace",
        "side_effect", "budget", "destination",
    ]
    admitted: bool
    reason_code: str | None = Field(None, alias="reasonCode")


class WorkflowRecoveryTargetModel(BaseModel):
    """Frozen, bounded input shared by the API and recovery workflow."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["workflow-recovery-target/v1"] = Field(
        "workflow-recovery-target/v1", alias="schemaVersion"
    )
    recovery_action: Literal["recover"] = Field("recover", alias="recoveryAction")
    target: RecoveryTargetModel
    source: RecoverySourceIdentityModel
    checkpoint: RecoveryCheckpointTargetModel
    continuation: RecoveryContinuationModel
    capability_snapshot: dict[str, Any] = Field(alias="capabilitySnapshot")
    preserved_step_refs: tuple[str, ...] = Field(
        default=(), alias="preservedStepRefs"
    )
    side_effect_disposition_ref: str = Field(
        ..., alias="sideEffectDispositionRef", min_length=1
    )
    side_effect_safe: bool = Field(..., alias="sideEffectSafe")
    side_effect_reconciliation_ref: str | None = Field(
        None, alias="sideEffectReconciliationRef"
    )
    destination: RecoveryDestinationModel

    @model_validator(mode="after")
    def _validate_ref_only_contract(self) -> "WorkflowRecoveryTargetModel":
        payload = self.model_dump(by_alias=True, mode="json")
        forbidden = {"path", "archive", "logs", "providerpayload", "credentials"}
        for key, value in _walk(payload):
            normalized = key.replace("_", "").lower()
            if normalized in forbidden or (
                normalized.endswith("ref")
                and value
                and not isinstance(value, str)
            ):
                raise ValueError("recovery input must contain bounded refs, not raw data")
            if (
                normalized.endswith("ref") or normalized.endswith("refs")
            ) and isinstance(value, str):
                if not value.strip() or value.startswith(("/", "./", "../", "~")):
                    raise ValueError("recovery refs must not be raw filesystem paths")
        return self

    def admission(self) -> tuple[RecoveryAdmissionDimensionModel, ...]:
        """Report every admission dimension independently, without mutation."""

        target = self.target
        phase = self.continuation.phase
        target_phases = TARGET_PHASE_BOUNDARIES.get(target.kind, {})
        expected_boundaries = target_phases.get(phase, ())
        capability = self.capability_snapshot
        supported = tuple(
            capability.get("checkpointBoundarySupport", {}).get(
                self.checkpoint.boundary, ()
            )
        )
        expected_digest = capability.get("capabilityDigest")
        computed_digest = _capability_digest(capability) if capability else None
        target_reason = _target_reason(self)
        checkpoint_ok = bool(
            self.checkpoint.ref
            and self.checkpoint.digest
            and self.checkpoint.validation_ref
        )
        phase_ok = bool(expected_boundaries)
        boundary_ok = self.checkpoint.boundary in expected_boundaries
        capability_ok = bool(capability)
        digest_ok = bool(expected_digest and expected_digest == computed_digest)
        restore_ok = bool(
            self.checkpoint.kind
            in tuple(capability.get("checkpointRestoreKinds", ()))
            and capability.get("checkpointRestoreActivity")
        )
        side_effect_ok = self.side_effect_safe
        budget_ok = not (
            target.kind == "control_stop"
            and phase == "continue_to_remediation"
            and not self.continuation.new_budget_ref
        )
        creation_key = deterministic_recovery_creation_key(
            self.source.workflow_id,
            self.source.run_id,
            target.kind,
            self.checkpoint.digest,
            phase,
        )
        destination_ok = self.destination.creation_key == creation_key
        destination_ok = destination_ok and bool(
            self.destination.workflow_id != self.source.workflow_id
            and self.destination.run_id is None
            and self.destination.runtime_id
            == capability.get("runtimeId")
            and self.destination.workspace_reservation_id
            != self.checkpoint.source_workspace_ref
        )
        return (
            _dimension("checkpoint", checkpoint_ok, "RECOVERY_CHECKPOINT_INVALID"),
            _dimension("target", target_reason is None, target_reason),
            _dimension(
                "phase",
                phase_ok and boundary_ok and phase in supported,
                "RECOVERY_PHASE_UNSUPPORTED" if not phase_ok or phase not in supported
                else "RECOVERY_CHECKPOINT_BOUNDARY_INCOMPATIBLE",
            ),
            _dimension(
                "capability",
                capability_ok and digest_ok,
                "RECOVERY_CAPABILITY_SNAPSHOT_MISSING" if not capability_ok
                else "RECOVERY_CAPABILITY_DIGEST_MISMATCH",
            ),
            _dimension(
                "workspace", restore_ok, "RECOVERY_WORKSPACE_RESTORE_UNSUPPORTED"
            ),
            _dimension(
                "side_effect", side_effect_ok, "RECOVERY_SIDE_EFFECT_UNSAFE"
            ),
            _dimension("budget", budget_ok, "RECOVERY_BUDGET_GRANT_REQUIRED"),
            _dimension(
                "destination",
                destination_ok,
                "RECOVERY_DESTINATION_IDENTITY_MISMATCH",
            ),
        )

    def require_admitted(self) -> None:
        denied = [item.reason_code for item in self.admission() if not item.admitted]
        if denied:
            raise ValueError(",".join(reason for reason in denied if reason))


def deterministic_recovery_creation_key(
    workflow_id: str,
    run_id: str,
    target_kind: str,
    checkpoint_digest: str,
    continuation_phase: str,
) -> str:
    canonical = (
        f"{workflow_id}\0{run_id}\0{target_kind}\0{checkpoint_digest}"
        f"\0{continuation_phase}"
    )
    return "recovery:" + hashlib.sha256(canonical.encode()).hexdigest()


def _target_reason(contract: WorkflowRecoveryTargetModel) -> str | None:
    target = contract.target
    if target.kind == "failed_step":
        ok = bool(
            target.logical_step_id
            and target.source_step_execution_id
        )
        return None if ok else "RECOVERY_TARGET_IDENTITY_MISMATCH"
    if target.kind == "control_stop":
        ok = bool(
            target.control_stop_kind
            and target.reason_code
            and target.source_step_execution_id
            and target.gate_result_ref
            and contract.continuation.remaining_work_ref
            and contract.continuation.workspace_head_ref
            and contract.continuation.prior_budget_ref
        )
        return None if ok else "RECOVERY_TARGET_IDENTITY_MISMATCH"
    if target.kind == "publication":
        if not (
            target.accepted_candidate_ref and target.publication_idempotency_key
        ):
            return "RECOVERY_TARGET_IDENTITY_MISMATCH"
        if not target.publication_observation_ref:
            return "RECOVERY_PUBLICATION_RECONCILIATION_REQUIRED"
        return None
    ok = bool(
        target.restore_operation_id
        and target.restore_idempotency_key
        and target.partial_restoration_ref
        and contract.destination.workspace_reservation_id
    )
    return None if ok else "RECOVERY_TARGET_IDENTITY_MISMATCH"


def _dimension(
    name: str, admitted: bool, reason: str | None
) -> RecoveryAdmissionDimensionModel:
    return RecoveryAdmissionDimensionModel(
        dimension=name, admitted=admitted, reasonCode=None if admitted else reason
    )


def _capability_digest(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("capabilityDigest", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _walk(value: Any, parent_key: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child, str(key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            if isinstance(child, str):
                yield parent_key, child
            else:
                yield from _walk(child, parent_key)


__all__ = [
    "RECOVERY_ADMISSION_REASONS",
    "TARGET_PHASE_BOUNDARIES",
    "RecoveryAdmissionDimensionModel",
    "RecoveryCheckpointTargetModel",
    "RecoveryContinuationModel",
    "RecoveryDestinationModel",
    "RecoverySourceIdentityModel",
    "RecoveryTargetModel",
    "WorkflowRecoveryTargetModel",
    "deterministic_recovery_creation_key",
]
