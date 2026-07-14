"""Fail-closed rollout admission for checkpoint-backed Resume.

Dynamic environment state is evaluated before workflow creation.  The returned
decision is immutable input evidence; workflows must never re-read this policy.
MoonLadderStudios/MoonMind#3278.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.executions.runtime_capabilities import RuntimeExecutionCapabilities

PromotionState = Literal[
    "disabled", "shadow_capture", "shadow_restore", "internal", "limited",
    "broad", "ga", "paused",
]


class CheckpointResumeRolloutPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    promotion_state: PromotionState = Field("disabled", alias="promotionState")
    capture_enabled: bool = Field(False, alias="captureEnabled")
    shadow_restore_enabled: bool = Field(False, alias="shadowRestoreEnabled")
    action_exposure_enabled: bool = Field(False, alias="actionExposureEnabled")
    execution_admission_enabled: bool = Field(False, alias="executionAdmissionEnabled")
    allowed_runtime_ids: frozenset[str] = Field(default_factory=frozenset, alias="allowedRuntimeIds")
    allowed_owner_ids: frozenset[str] = Field(default_factory=frozenset, alias="allowedOwnerIds")
    allowed_repositories: frozenset[str] = Field(default_factory=frozenset, alias="allowedRepositories")
    allowed_deployment_generations: frozenset[str] = Field(
        default_factory=frozenset, alias="allowedDeploymentGenerations"
    )
    max_archive_bytes: int = Field(0, ge=0, alias="maxArchiveBytes")
    required_gates_passed: bool = Field(False, alias="requiredGatesPassed")
    live_canary_passed: bool = Field(False, alias="liveCanaryPassed")
    reason: str = "not_promoted"


class CheckpointResumeReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    runtime_id: str = Field(alias="runtimeId")
    deployment_generation: str = Field(alias="deploymentGeneration")
    capture_route_ready: bool = Field(alias="captureRouteReady")
    restore_route_ready: bool = Field(alias="restoreRouteReady")
    artifact_store_ready: bool = Field(alias="artifactStoreReady")
    managed_run_store_ready: bool = Field(alias="managedRunStoreReady")
    capability_set_version: str = Field(alias="capabilitySetVersion")
    capability_digest: str = Field(alias="capabilityDigest")
    checked_at: datetime = Field(alias="checkedAt")


class AdmittedCheckpointResumeDecision(BaseModel):
    """Replay-safe snapshot passed to a recovery workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    admitted: bool
    reason_code: str = Field(alias="reasonCode")
    promotion_state: PromotionState = Field(alias="promotionState")
    runtime_capabilities: RuntimeExecutionCapabilities = Field(alias="runtimeCapabilities")
    readiness: CheckpointResumeReadiness
    checkpoint_kind: str = Field(alias="checkpointKind")
    checkpoint_boundary: str = Field(alias="checkpointBoundary")
    resume_phase: str = Field(alias="resumePhase")
    capture_activity: str | None = Field(alias="captureActivity")
    restore_activity: str | None = Field(alias="restoreActivity")


def evaluate_checkpoint_resume_admission(
    *, capabilities: RuntimeExecutionCapabilities, policy: CheckpointResumeRolloutPolicy,
    readiness: CheckpointResumeReadiness, checkpoint_kind: str,
    checkpoint_boundary: str, resume_phase: str, archive_bytes: int,
    owner_id: str | None = None, repository: str | None = None,
) -> AdmittedCheckpointResumeDecision:
    """Return a bounded, stable decision; never substitute a full retry."""

    reason = "eligible"
    hidden_states = {"disabled", "shadow_capture", "shadow_restore", "paused"}
    if policy.promotion_state in hidden_states or not policy.action_exposure_enabled:
        reason = "rollout_action_hidden"
    elif not policy.execution_admission_enabled:
        reason = "rollout_admission_disabled"
    elif not policy.required_gates_passed or not policy.live_canary_passed:
        reason = "promotion_evidence_missing"
    elif capabilities.runtime_id not in policy.allowed_runtime_ids:
        reason = "runtime_not_allowlisted"
    elif policy.allowed_owner_ids and owner_id not in policy.allowed_owner_ids:
        reason = "owner_not_allowlisted"
    elif policy.allowed_repositories and repository not in policy.allowed_repositories:
        reason = "repository_not_allowlisted"
    elif readiness.deployment_generation not in policy.allowed_deployment_generations:
        reason = "deployment_generation_not_allowlisted"
    elif readiness.runtime_id != capabilities.runtime_id:
        reason = "readiness_runtime_mismatch"
    elif (readiness.capability_set_version != capabilities.capability_set_version
          or readiness.capability_digest != capabilities.capability_digest):
        reason = "capability_snapshot_mismatch"
    elif not all((readiness.capture_route_ready, readiness.restore_route_ready,
                  readiness.artifact_store_ready, readiness.managed_run_store_ready)):
        reason = "deployment_not_ready"
    elif checkpoint_kind not in capabilities.checkpoint_restore_kinds:
        reason = "checkpoint_kind_unsupported"
    elif resume_phase not in capabilities.checkpoint_boundary_support.get(checkpoint_boundary, ()):
        reason = "checkpoint_boundary_unsupported"
    elif archive_bytes < 0:
        reason = "checkpoint_archive_size_unknown"
    elif policy.max_archive_bytes > 0 and archive_bytes > policy.max_archive_bytes:
        reason = "checkpoint_archive_limit_exceeded"

    return AdmittedCheckpointResumeDecision(
        admitted=reason == "eligible", reasonCode=reason,
        promotionState=policy.promotion_state, runtimeCapabilities=capabilities,
        readiness=readiness, checkpointKind=checkpoint_kind,
        checkpointBoundary=checkpoint_boundary, resumePhase=resume_phase,
        captureActivity=capabilities.checkpoint_capture_activity,
        restoreActivity=capabilities.checkpoint_restore_activity,
    )
