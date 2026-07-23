"""Deterministic checkpoint-backed recovery contract and state projection."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas.workspace_locator_models import ManagedWorkspaceLocator
from moonmind.workflows.executions.runtime_capabilities import (
    CAPABILITY_SET_VERSION,
    RuntimeExecutionCapabilities,
)


CHECKPOINT_BOUNDARY_INCOMPATIBLE = "CHECKPOINT_BOUNDARY_INCOMPATIBLE"
CHECKPOINT_CAPABILITY_INVALID = "CHECKPOINT_CAPABILITY_INVALID"
CHECKPOINT_SIDE_EFFECT_UNSAFE = "CHECKPOINT_SIDE_EFFECT_UNSAFE"
CHECKPOINT_RESTORATION_NOT_READY = "CHECKPOINT_RESTORATION_NOT_READY"

BOUNDARY_PHASES = {
    "before_execution": "rerun_failed_step",
    "after_execution": "continue_to_gate",
    "after_gate": "continue_after_gate",
    "before_publication": "resume_publication",
    "before_recovery_restoration": "retry_restoration",
}


class RecoveryContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class CheckpointRecoveryContract(BaseModel):
    """Compact frozen input used by the workflow recovery state machine."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    recovery_action: str = Field(..., alias="recoveryAction")
    resume_phase: str = Field(..., alias="resumePhase")
    source_checkpoint_ref: str = Field(..., alias="sourceCheckpointRef", min_length=1)
    source_checkpoint_boundary: str = Field(..., alias="sourceCheckpointBoundary")
    source_checkpoint_kind: str = Field(..., alias="sourceCheckpointKind", min_length=1)
    workspace_policy: str = Field(..., alias="workspacePolicy", min_length=1)
    capabilities: RuntimeExecutionCapabilities = Field(..., alias="capabilitySnapshot")
    restore_activity: str = Field(..., alias="restoreActivity", min_length=1)
    side_effect_dispositions: tuple[dict[str, Any], ...] = Field(
        default=(), alias="sideEffectDispositions"
    )
    publication_idempotency_key: str | None = Field(
        None, alias="publicationIdempotencyKey"
    )

    @model_validator(mode="after")
    def validate_frozen_contract(self) -> "CheckpointRecoveryContract":
        if self.recovery_action != "resume_from_workspace_checkpoint":
            raise RecoveryContractError(
                CHECKPOINT_BOUNDARY_INCOMPATIBLE, "unsupported recovery action"
            )
        expected_phase = BOUNDARY_PHASES.get(self.source_checkpoint_boundary)
        if expected_phase != self.resume_phase:
            raise RecoveryContractError(
                CHECKPOINT_BOUNDARY_INCOMPATIBLE,
                "checkpoint boundary does not authorize the requested resume phase",
            )
        if self.capabilities.capability_set_version not in {
            "runtime-execution-capabilities-v2", CAPABILITY_SET_VERSION
        }:
            raise RecoveryContractError(
                CHECKPOINT_CAPABILITY_INVALID, "unsupported capability set version"
            )
        expected_digest = self.capabilities.with_digest().capability_digest
        if self.capabilities.capability_digest != expected_digest:
            raise RecoveryContractError(
                CHECKPOINT_CAPABILITY_INVALID, "capability snapshot digest mismatch"
            )
        if self.source_checkpoint_kind not in self.capabilities.checkpoint_restore_kinds:
            raise RecoveryContractError(
                CHECKPOINT_CAPABILITY_INVALID, "checkpoint kind is not restorable"
            )
        supported_phases = (
            self.capabilities.checkpoint_boundary_support.get(
                self.source_checkpoint_boundary, ()
            )
            if self.capabilities.capability_set_version == CAPABILITY_SET_VERSION
            else (
                ("rerun_failed_step",)
                if self.source_checkpoint_boundary == "before_execution"
                else ()
            )
        )
        if self.resume_phase not in supported_phases:
            raise RecoveryContractError(
                CHECKPOINT_BOUNDARY_INCOMPATIBLE,
                "workspace capability does not authorize the requested boundary",
            )
        if self.restore_activity != self.capabilities.checkpoint_restore_activity:
            raise RecoveryContractError(
                CHECKPOINT_CAPABILITY_INVALID, "restore activity contradicts snapshot"
            )
        for disposition in self.side_effect_dispositions:
            accepted = str(disposition.get("disposition") or "").lower() == "accepted"
            idempotent = bool(disposition.get("idempotent"))
            compensated = bool(disposition.get("compensationRef"))
            if accepted and not idempotent and not compensated:
                raise RecoveryContractError(
                    CHECKPOINT_SIDE_EFFECT_UNSAFE,
                    "accepted non-idempotent side effect lacks compensation evidence",
                )
        if self.resume_phase == "resume_publication" and not self.publication_idempotency_key:
            raise RecoveryContractError(
                CHECKPOINT_SIDE_EFFECT_UNSAFE,
                "publication recovery requires its stable idempotency key",
            )
        return self


def deterministic_recovery_identity(
    *, workflow_id: str, run_id: str, logical_step_id: str,
    execution_ordinal: int, checkpoint_ref: str,
) -> tuple[str, str]:
    checkpoint_digest = hashlib.sha256(checkpoint_ref.encode()).hexdigest()[:20]
    destination_id = (
        f"{workflow_id}:restore:{logical_step_id}:execution:{execution_ordinal}"
    )[:300]
    idempotency_key = f"{workflow_id}:{run_id}:{checkpoint_digest}:restore"
    return destination_id, idempotency_key


def validate_restore_result(
    result: Mapping[str, Any], *, runtime_id: str, destination_agent_run_id: str
) -> tuple[dict[str, Any], str, str]:
    if str(result.get("status") or "") != "succeeded":
        raise RecoveryContractError(
            CHECKPOINT_RESTORATION_NOT_READY, "restore activity did not succeed"
        )
    locator = ManagedWorkspaceLocator.model_validate(
        result.get("destinationWorkspaceLocator")
    )
    if locator.runtime_id != runtime_id or locator.agent_run_id != destination_agent_run_id:
        raise RecoveryContractError(
            CHECKPOINT_RESTORATION_NOT_READY, "destination locator identity mismatch"
        )
    evidence_ref = str(result.get("restorationEvidenceRef") or "").strip()
    evidence_digest = str(result.get("restorationEvidenceDigest") or "").strip()
    if not evidence_ref or not evidence_digest:
        raise RecoveryContractError(
            CHECKPOINT_RESTORATION_NOT_READY, "verified restoration evidence is required"
        )
    return locator.model_dump(by_alias=True), evidence_ref, evidence_digest


__all__ = [
    "BOUNDARY_PHASES", "CHECKPOINT_BOUNDARY_INCOMPATIBLE",
    "CHECKPOINT_CAPABILITY_INVALID", "CHECKPOINT_RESTORATION_NOT_READY",
    "CHECKPOINT_SIDE_EFFECT_UNSAFE", "CheckpointRecoveryContract",
    "RecoveryContractError", "deterministic_recovery_identity",
    "validate_restore_result",
]
