"""Portable promotion, shadow-validation, telemetry, and rollback contracts.
This module deliberately contains deterministic decisions only. Activities own
workspace creation/restoration and deployment controllers own worker draining.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShadowRestoreRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_ref: str = Field(alias="checkpointRef", min_length=1)
    checkpoint_digest: str = Field(alias="checkpointDigest", min_length=1)
    deployment_generation: str = Field(alias="deploymentGeneration", min_length=1)
    disposable_workspace: bool = Field(True, alias="disposableWorkspace")
    source_workspace_mounted: bool = Field(False, alias="sourceWorkspaceMounted")
    delete_workspace_after_validation: bool = Field(
        True, alias="deleteWorkspaceAfterValidation"
    )

    @model_validator(mode="after")
    def _require_source_independence(self) -> "ShadowRestoreRequest":
        if (
            not self.disposable_workspace
            or self.source_workspace_mounted
            or not self.delete_workspace_after_validation
        ):
            raise ValueError(
                "shadow restore must use a source-independent disposable workspace"
            )
        return self


class CheckpointPromotionHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    integrity_failures: int = Field(0, ge=0, alias="integrityFailures")
    duplicate_side_effects: int = Field(0, ge=0, alias="duplicateSideEffects")
    false_positive_eligibility: int = Field(0, ge=0, alias="falsePositiveEligibility")
    restore_attempts: int = Field(0, ge=0, alias="restoreAttempts")
    restore_failures: int = Field(0, ge=0, alias="restoreFailures")


class AutomaticPauseDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pause_new_admissions: bool = Field(alias="pauseNewAdmissions")
    reason_code: str = Field(alias="reasonCode")
    permit_in_flight_reconciliation: bool = Field(
        True, alias="permitInFlightReconciliation"
    )


def evaluate_automatic_pause(
    health: CheckpointPromotionHealth, *, maximum_restore_failure_ratio: float
) -> AutomaticPauseDecision:
    """Pause only new admissions for integrity, idempotency, or reliability risk."""

    failure_ratio = (
        health.restore_failures / health.restore_attempts
        if health.restore_attempts
        else 0.0
    )
    reason = "healthy"
    if health.integrity_failures:
        reason = "checkpoint_integrity_failure"
    elif health.duplicate_side_effects:
        reason = "duplicate_side_effect"
    elif health.false_positive_eligibility:
        reason = "false_positive_eligibility"
    elif failure_ratio > maximum_restore_failure_ratio:
        reason = "restore_failure_ratio_exceeded"
    return AutomaticPauseDecision(
        pauseNewAdmissions=reason != "healthy",
        reasonCode=reason,
        permitInFlightReconciliation=True,
    )


class FrozenGenerationUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_generation: str = Field(alias="deploymentGeneration", min_length=1)
    open_recovery_histories: int = Field(ge=0, alias="openRecoveryHistories")
    pending_restorations: int = Field(ge=0, alias="pendingRestorations")


class WorkerDrainDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generation: str
    may_remove_worker_routes: bool = Field(alias="mayRemoveWorkerRoutes")
    outstanding_recoveries: int = Field(alias="outstandingRecoveries")
    required_action: Literal["retain_routes", "safe_to_remove"] = Field(
        alias="requiredAction"
    )


def evaluate_worker_drain(usage: FrozenGenerationUsage) -> WorkerDrainDecision:
    outstanding = usage.open_recovery_histories + usage.pending_restorations
    return WorkerDrainDecision(
        generation=usage.deployment_generation,
        mayRemoveWorkerRoutes=outstanding == 0,
        outstandingRecoveries=outstanding,
        requiredAction="safe_to_remove" if outstanding == 0 else "retain_routes",
    )


CHECKPOINT_METRIC_NAMES = frozenset(
    {
        "checkpoint_resume.eligibility_total",
        "checkpoint_resume.admission_total",
        "checkpoint_resume.capture_total",
        "checkpoint_resume.shadow_restore_total",
        "checkpoint_resume.restore_total",
        "checkpoint_resume.outcome_total",
        "checkpoint_resume.automatic_pause_total",
    }
)


def bounded_checkpoint_metric_tags(
    *, runtime_id: str, generation: str, outcome: str
) -> dict[str, str]:
    """Return the only allowed low-cardinality labels for checkpoint metrics."""

    return {
        "runtime": runtime_id[:40],
        "generation": generation[:80],
        "outcome": outcome[:60],
    }
