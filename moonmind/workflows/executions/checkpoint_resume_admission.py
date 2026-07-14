"""Fail-closed rollout admission for checkpoint-backed Resume.

Dynamic environment state is evaluated before workflow creation.  The returned
decision is immutable input evidence; workflows must never re-read this policy.
MoonLadderStudios/MoonMind#3278.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.executions.runtime_capabilities import RuntimeExecutionCapabilities

PromotionState = Literal[
    "disabled", "shadow_capture", "shadow_restore", "internal", "limited",
    "broad", "ga", "paused",
]


class CheckpointPromotionEvidence(BaseModel):
    """Recorded, generation-bound evidence used by the promotion gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_generation: str = Field(alias="deploymentGeneration")
    cold_resume_ci_passed: bool = Field(False, alias="coldResumeCiPassed")
    shadow_restore_samples: int = Field(0, ge=0, alias="shadowRestoreSamples")
    shadow_restore_successes: int = Field(0, ge=0, alias="shadowRestoreSuccesses")
    integrity_failures: int = Field(0, ge=0, alias="integrityFailures")
    duplicate_side_effects: int = Field(0, ge=0, alias="duplicateSideEffects")
    live_canary_passed: bool = Field(False, alias="liveCanaryPassed")
    recorded_at: datetime = Field(alias="recordedAt")

    def gates_pass(self, *, minimum_samples: int, minimum_success_ratio: float) -> bool:
        if self.shadow_restore_samples < minimum_samples:
            return False
        ratio = self.shadow_restore_successes / self.shadow_restore_samples
        return bool(
            self.cold_resume_ci_passed
            and ratio >= minimum_success_ratio
            and self.integrity_failures == 0
            and self.duplicate_side_effects == 0
        )


class CheckpointResumeRolloutPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    promotion_evidence: CheckpointPromotionEvidence | None = Field(
        None, alias="promotionEvidence"
    )
    minimum_shadow_samples: int = Field(1, ge=1, alias="minimumShadowSamples")
    minimum_shadow_success_ratio: float = Field(
        1.0, ge=0.0, le=1.0, alias="minimumShadowSuccessRatio"
    )
    reason: str = "not_promoted"


class CheckpointResumeReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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

    model_config = ConfigDict(frozen=True, extra="forbid")

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


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def rollout_policy_from_settings(feature_flags: object) -> CheckpointResumeRolloutPolicy:
    """Bind operator settings to the canonical policy; malformed evidence fails closed."""

    raw_evidence = str(
        getattr(feature_flags, "checkpoint_resume_promotion_evidence_json", "") or ""
    ).strip()
    evidence = None
    if raw_evidence:
        try:
            evidence = CheckpointPromotionEvidence.model_validate(json.loads(raw_evidence))
        except (ValueError, TypeError):
            evidence = None
    generation = str(
        getattr(feature_flags, "checkpoint_resume_deployment_generation", "") or ""
    ).strip()
    return CheckpointResumeRolloutPolicy(
        promotionState=getattr(feature_flags, "checkpoint_resume_promotion_state", "disabled"),
        captureEnabled=bool(getattr(feature_flags, "managed_checkpoint_capture_enabled", False)),
        shadowRestoreEnabled=bool(getattr(feature_flags, "checkpoint_shadow_restore_enabled", False)),
        actionExposureEnabled=bool(getattr(feature_flags, "checkpoint_resume_action_enabled", False)),
        executionAdmissionEnabled=bool(getattr(feature_flags, "checkpoint_resume_admission_enabled", False)),
        allowedRuntimeIds=_csv_set(str(getattr(feature_flags, "checkpoint_resume_allowed_runtime_ids", ""))),
        allowedOwnerIds=_csv_set(str(getattr(feature_flags, "checkpoint_resume_allowed_owner_ids", ""))),
        allowedRepositories=_csv_set(str(getattr(feature_flags, "checkpoint_resume_allowed_repositories", ""))),
        allowedDeploymentGenerations=frozenset({generation}) if generation else frozenset(),
        maxArchiveBytes=int(getattr(feature_flags, "checkpoint_resume_max_archive_bytes", 0)),
        requiredGatesPassed=evidence is not None,
        liveCanaryPassed=bool(evidence and evidence.live_canary_passed),
        promotionEvidence=evidence,
        minimumShadowSamples=int(getattr(feature_flags, "checkpoint_resume_minimum_shadow_samples", 10)),
        minimumShadowSuccessRatio=float(getattr(feature_flags, "checkpoint_resume_minimum_shadow_success_ratio", .99)),
        reason="operator_settings",
    )


def evaluate_checkpoint_resume_admission(
    *, capabilities: RuntimeExecutionCapabilities, policy: CheckpointResumeRolloutPolicy,
    readiness: CheckpointResumeReadiness, checkpoint_kind: str,
    checkpoint_boundary: str, resume_phase: str, archive_bytes: int,
    owner_id: str | None = None, repository: str | None = None,
) -> AdmittedCheckpointResumeDecision:
    """Return a bounded, stable decision; never substitute a full retry."""

    reason = "eligible"
    hidden_states = {"disabled", "shadow_capture", "shadow_restore", "paused"}
    evidence = policy.promotion_evidence
    evidence_valid = bool(
        evidence
        and evidence.deployment_generation == readiness.deployment_generation
        and evidence.gates_pass(
            minimum_samples=policy.minimum_shadow_samples,
            minimum_success_ratio=policy.minimum_shadow_success_ratio,
        )
        and evidence.live_canary_passed
    )
    if policy.promotion_state in hidden_states or not policy.action_exposure_enabled:
        reason = "rollout_action_hidden"
    elif not policy.capture_enabled or not policy.shadow_restore_enabled:
        reason = "checkpoint_routes_disabled"
    elif not policy.execution_admission_enabled:
        reason = "rollout_admission_disabled"
    elif not policy.required_gates_passed or not policy.live_canary_passed or not evidence_valid:
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
    elif (
        policy.max_archive_bytes <= 0
        or archive_bytes <= 0
        or archive_bytes > policy.max_archive_bytes
    ):
        reason = "checkpoint_archive_limit_exceeded"

    return AdmittedCheckpointResumeDecision(
        admitted=reason == "eligible", reasonCode=reason,
        promotionState=policy.promotion_state, runtimeCapabilities=capabilities,
        readiness=readiness, checkpointKind=checkpoint_kind,
        checkpointBoundary=checkpoint_boundary, resumePhase=resume_phase,
        captureActivity=capabilities.checkpoint_capture_activity,
        restoreActivity=capabilities.checkpoint_restore_activity,
    )


def unavailable_checkpoint_resume_decision(
    *, runtime_id: str, reason_code: str, checkpoint_kind: str,
    checkpoint_boundary: str, resume_phase: str,
) -> AdmittedCheckpointResumeDecision:
    """Create a stable fail-closed projection when readiness is unavailable."""

    from moonmind.workflows.executions.runtime_capabilities import (
        resolve_runtime_execution_capabilities,
    )

    capabilities = resolve_runtime_execution_capabilities(runtime_id)
    return AdmittedCheckpointResumeDecision(
        admitted=False,
        reasonCode=reason_code,
        promotionState="disabled",
        runtimeCapabilities=capabilities,
        readiness=CheckpointResumeReadiness(
            runtimeId=runtime_id,
            deploymentGeneration="unavailable",
            captureRouteReady=False,
            restoreRouteReady=False,
            artifactStoreReady=False,
            managedRunStoreReady=False,
            capabilitySetVersion=capabilities.capability_set_version,
            capabilityDigest=capabilities.capability_digest,
            checkedAt=datetime.now(UTC),
        ),
        checkpointKind=checkpoint_kind,
        checkpointBoundary=checkpoint_boundary,
        resumePhase=resume_phase,
        captureActivity=capabilities.checkpoint_capture_activity,
        restoreActivity=capabilities.checkpoint_restore_activity,
    )
