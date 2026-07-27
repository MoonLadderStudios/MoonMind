"""Host-independent recovery decisions for Omnigent OAuth checkpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OmnigentRecoveryMode(str, Enum):
    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"


class OmnigentCheckpointExecutionKind(str, Enum):
    RECOVERY = "recovery"
    BRANCH = "branch"


class OmnigentRecoveryOutcome(str, Enum):
    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"
    BRANCH_REQUIRED = "branch_required"
    RESUME_UNAVAILABLE = "resume_unavailable"


class CandidateWorkspaceAuthority(BaseModel):
    """MoonMind-owned repository checkpoint selected for continuation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    loop_id: str = Field(..., alias="loopId", min_length=1)
    attempt_ordinal: int = Field(..., alias="attemptOrdinal", ge=0)
    head_ref: str = Field(..., alias="headRef", min_length=1)
    head_digest: str = Field(
        ..., alias="headDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    checkpoint_digest: str = Field(
        ..., alias="checkpointDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def _refs_are_artifact_authority(self) -> "CandidateWorkspaceAuthority":
        for name, value in (
            ("headRef", self.head_ref),
            ("checkpointRef", self.checkpoint_ref),
        ):
            if not value.startswith("artifact://"):
                raise ValueError(f"{name} must be a durable artifact reference")
        return self


class OmnigentCheckpointIdentity(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider_profile_id: str = Field(..., alias="providerProfileId", min_length=1)
    credential_generation: int = Field(..., alias="credentialGeneration", ge=1)
    provider_lease_ref: str | None = Field(None, alias="providerLeaseRef")
    host_binding_ref: str = Field(..., alias="hostBindingRef", min_length=1)
    host_lease_ref: str | None = Field(None, alias="hostLeaseRef")
    endpoint_ref: str = Field(..., alias="endpointRef", min_length=1)
    omnigent_host_id: str | None = Field(None, alias="omnigentHostId")
    omnigent_session_id: str | None = Field(None, alias="omnigentSessionId")
    bridge_session_id: str = Field(..., alias="bridgeSessionId", min_length=1)
    external_state_ref: str = Field(..., alias="externalStateRef", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    terminal_ref: str | None = Field(None, alias="terminalRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    effective_launch_ref: str | None = Field(None, alias="effectiveLaunchRef", min_length=1)

    @model_validator(mode="after")
    def _reject_raw_credential_like_values(self) -> "OmnigentCheckpointIdentity":
        if self.effective_launch_ref is not None and not self.effective_launch_ref.startswith(
            "omnigent-launch:sha256:"
        ):
            raise ValueError("effectiveLaunchRef must identify an effective launch snapshot")
        for field, value in self.model_dump(mode="json").items():
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            if any(marker in lowered for marker in ("bearer ", "token=", "password=")):
                raise ValueError(f"{field} must be a reference, not credential data")
        return self


class OmnigentCheckpointExecutionEvidence(BaseModel):
    """Activity-bound checkpoint execution contract.

    The workflow owns this evidence.  The Omnigent Activity validates the whole
    contract before it invokes either coordinator checkpoint entrypoint.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: OmnigentCheckpointExecutionKind
    checkpoint: OmnigentCheckpointIdentity
    candidate_workspace: CandidateWorkspaceAuthority = Field(
        ..., alias="candidateWorkspace"
    )
    current_credential_generation: int = Field(
        ..., alias="currentCredentialGeneration", ge=1
    )
    provider_lease: dict[str, Any] | None = Field(None, alias="providerLease")
    host_lease: dict[str, Any] | None = Field(None, alias="hostLease")
    host_registered: bool = Field(False, alias="hostRegistered")
    session_valid: bool = Field(False, alias="sessionValid")
    first_message_consistent: bool = Field(
        False, alias="firstMessageConsistent"
    )
    immutable_input_matches: bool = Field(True, alias="immutableInputMatches")
    changed_fields: tuple[str, ...] = Field(default=(), alias="changedFields")
    validation_passed: bool = Field(True, alias="validationPassed")
    validation_reason: str | None = Field(None, alias="validationReason")

    @model_validator(mode="after")
    def _validate_kind_specific_evidence(
        self,
    ) -> "OmnigentCheckpointExecutionEvidence":
        if self.kind == OmnigentCheckpointExecutionKind.BRANCH:
            if self.immutable_input_matches or not self.changed_fields:
                raise ValueError(
                    "checkpoint branch requires explicit changed immutable input"
                )
        elif self.changed_fields or not self.immutable_input_matches:
            raise ValueError(
                "checkpoint recovery cannot change immutable workflow input"
            )
        return self


class OmnigentRecoveryDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    outcome: OmnigentRecoveryOutcome
    reason: str
    blocking_reason: str | None = Field(None, alias="blockingReason")


def decide_checkpoint_execution(
    evidence: OmnigentCheckpointExecutionEvidence,
) -> OmnigentRecoveryDecision:
    """Choose one of the four operator-visible recovery outcomes."""

    if (
        evidence.current_credential_generation
        != evidence.checkpoint.credential_generation
    ):
        return OmnigentRecoveryDecision(
            outcome=OmnigentRecoveryOutcome.RESUME_UNAVAILABLE,
            reason="credential_generation_stale",
            blockingReason="credential_generation_mismatch",
        )
    if evidence.kind == OmnigentCheckpointExecutionKind.BRANCH:
        if not evidence.validation_passed:
            return OmnigentRecoveryDecision(
                outcome=OmnigentRecoveryOutcome.RESUME_UNAVAILABLE,
                reason="checkpoint_validation_failed",
                blockingReason=evidence.validation_reason
                or "checkpoint_evidence_invalid",
            )
        return OmnigentRecoveryDecision(
            outcome=OmnigentRecoveryOutcome.BRANCH_REQUIRED,
            reason="immutable_input_changed",
        )
    if not evidence.validation_passed:
        return OmnigentRecoveryDecision(
            outcome=OmnigentRecoveryOutcome.RESUME_UNAVAILABLE,
            reason="checkpoint_validation_failed",
            blockingReason=evidence.validation_reason
            or "checkpoint_evidence_invalid",
        )
    mode = recovery_mode(
        evidence.checkpoint,
        provider_lease=evidence.provider_lease,
        host_lease=evidence.host_lease,
        host_registered=evidence.host_registered,
        session_valid=evidence.session_valid,
        first_message_consistent=evidence.first_message_consistent,
    )
    outcome = (
        OmnigentRecoveryOutcome.LIVE_REATTACH
        if mode == OmnigentRecoveryMode.LIVE_REATTACH
        else OmnigentRecoveryOutcome.COLD_RESTORE
    )
    return OmnigentRecoveryDecision(
        outcome=outcome,
        reason=(
            "all_original_authorities_valid"
            if outcome == OmnigentRecoveryOutcome.LIVE_REATTACH
            else "replacement_host_required"
        ),
    )


def recovery_mode(
    checkpoint: OmnigentCheckpointIdentity,
    *,
    provider_lease: Mapping[str, Any] | None,
    host_lease: Mapping[str, Any] | None,
    host_registered: bool,
    session_valid: bool,
    first_message_consistent: bool,
) -> OmnigentRecoveryMode:
    """Select live reattach only when every original authority is still valid."""

    provider_active = bool(provider_lease and provider_lease.get("active"))
    provider_ref_matches = bool(
        provider_lease
        and str(provider_lease.get("lease_id") or provider_lease.get("leaseId"))
        == str(checkpoint.provider_lease_ref or "")
    )
    host_active = bool(
        host_lease and str(host_lease.get("status") or "") in {"ready", "assigned"}
    )
    host_ref_matches = bool(
        host_lease
        and str(host_lease.get("lease_id") or host_lease.get("leaseId"))
        == str(checkpoint.host_lease_ref or "")
    )
    generation_matches = bool(
        host_lease
        and int(
            host_lease.get("credential_generation")
            or host_lease.get("credentialGeneration")
            or 0
        )
        == checkpoint.credential_generation
    )
    if all(
        (
            provider_active,
            provider_ref_matches,
            host_active,
            host_ref_matches,
            generation_matches,
            host_registered,
            session_valid,
            first_message_consistent,
            checkpoint.omnigent_host_id,
            checkpoint.omnigent_session_id,
        )
    ):
        return OmnigentRecoveryMode.LIVE_REATTACH
    return OmnigentRecoveryMode.COLD_RESTORE


def validate_cold_restore_target(
    checkpoint: OmnigentCheckpointIdentity,
    *,
    provider_profile_id: str,
    credential_generation: int,
) -> None:
    if provider_profile_id != checkpoint.provider_profile_id:
        raise ValueError("cold restore must reacquire the checkpoint Provider Profile")
    if credential_generation != checkpoint.credential_generation:
        raise ValueError("cold restore credential generation does not match checkpoint")


def validate_branch_identity(
    checkpoint: OmnigentCheckpointIdentity,
    *,
    new_host_lease_ref: str,
    new_session_id: str,
) -> None:
    if new_host_lease_ref == checkpoint.host_lease_ref:
        raise ValueError("checkpoint branch requires a new host lease")
    if new_session_id == checkpoint.omnigent_session_id:
        raise ValueError("checkpoint branch requires a new Omnigent session")


__all__ = [
    "CandidateWorkspaceAuthority",
    "OmnigentCheckpointIdentity",
    "OmnigentCheckpointExecutionEvidence",
    "OmnigentCheckpointExecutionKind",
    "OmnigentRecoveryDecision",
    "OmnigentRecoveryMode",
    "OmnigentRecoveryOutcome",
    "decide_checkpoint_execution",
    "recovery_mode",
    "validate_branch_identity",
    "validate_cold_restore_target",
]
