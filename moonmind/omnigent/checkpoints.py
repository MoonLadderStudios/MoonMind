"""Host-independent recovery decisions for Omnigent OAuth checkpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OmnigentRecoveryMode(str, Enum):
    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"


class RecoveryCapability(BaseModel):
    """One independently evaluated checkpoint recovery capability."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    available: bool
    reason_code: str | None = Field(None, alias="reasonCode", max_length=80)
    message: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _truthful_projection(self) -> "RecoveryCapability":
        if self.available and self.reason_code is not None:
            raise ValueError("available capability cannot include reasonCode")
        if not self.available and not self.reason_code:
            raise ValueError("unavailable capability requires reasonCode")
        return self


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


class OmnigentCheckpointManifest(BaseModel):
    """Complete, host-independent Omnigent checkpoint authority (v2)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v2"] = Field("v2", alias="schemaVersion")
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    attempt_ordinal: int = Field(..., alias="attemptOrdinal", ge=1)
    boundary: str = Field(..., min_length=1)
    identity: OmnigentCheckpointIdentity
    external_state_digest: str = Field(
        ..., alias="externalStateDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    execution_profile_ref: str = Field(..., alias="executionProfileRef", min_length=1)
    launch_policy_ref: str = Field(..., alias="launchPolicyRef", min_length=1)
    source_effective_launch_ref: str = Field(
        ..., alias="sourceEffectiveLaunchRef", min_length=1
    )
    last_bridge_event_cursor: str = Field(
        ..., alias="lastBridgeEventCursor", min_length=1
    )
    first_message_identity: str = Field(
        ..., alias="firstMessageIdentity", min_length=1
    )
    first_message_digest: str = Field(
        ..., alias="firstMessageDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    resource_manifest_ref: str = Field(..., alias="resourceManifestRef", min_length=1)
    capture_manifest_ref: str = Field(..., alias="captureManifestRef", min_length=1)
    patch_capability: str = Field(..., alias="patchCapability", min_length=1)
    workspace_locator: dict[str, Any] = Field(..., alias="workspaceLocator")
    baseline_commit: str = Field(..., alias="baselineCommit", min_length=1)
    head_ref: str = Field(..., alias="headRef", min_length=1)
    head_digest: str = Field(
        ..., alias="headDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    checkpoint_digest: str = Field(
        ..., alias="checkpointDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    diff_ref: str | None = Field(None, alias="diffRef")
    diff_digest: str | None = Field(
        None, alias="diffDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    instruction_refs: list[str] = Field(default_factory=list, alias="instructionRefs")
    context_refs: list[str] = Field(default_factory=list, alias="contextRefs")
    source_branch: str = Field(..., alias="sourceBranch", min_length=1)
    output_branch: str | None = Field(None, alias="outputBranch")
    publication_state: str = Field(..., alias="publicationState", min_length=1)
    captured_at: datetime = Field(..., alias="capturedAt")
    producer_version: str = Field(..., alias="producerVersion", min_length=1)
    validation_status: Literal["valid", "degraded", "invalid"] = Field(
        ..., alias="validationStatus"
    )
    live_reattach: RecoveryCapability = Field(..., alias="liveReattach")
    workspace_cold_restore: RecoveryCapability = Field(
        ..., alias="workspaceColdRestore"
    )
    branch_creation: RecoveryCapability = Field(..., alias="branchCreation")

    @model_validator(mode="after")
    def _validate_authority(self) -> "OmnigentCheckpointManifest":
        refs = {
            "externalStateRef": self.identity.external_state_ref,
            "executionProfileRef": self.execution_profile_ref,
            "launchPolicyRef": self.launch_policy_ref,
            "resourceManifestRef": self.resource_manifest_ref,
            "captureManifestRef": self.capture_manifest_ref,
            "headRef": self.head_ref,
            "checkpointRef": self.checkpoint_ref,
            **({"diffRef": self.diff_ref} if self.diff_ref else {}),
        }
        refs.update(
            {
                f"instructionRefs[{i}]": ref
                for i, ref in enumerate(self.instruction_refs)
            }
        )
        refs.update({f"contextRefs[{i}]": ref for i, ref in enumerate(self.context_refs)})
        for name, ref in refs.items():
            if not str(ref).startswith("artifact://"):
                raise ValueError(f"{name} must be a durable artifact reference")
        locator = self.workspace_locator
        if locator.get("kind") not in {"sandbox", "managed_runtime"}:
            raise ValueError("workspaceLocator must name MoonMind-owned workspace authority")
        if any(key in locator for key in ("path", "workspacePath", "root", "hostPath")):
            raise ValueError("workspaceLocator cannot contain a raw host path")
        if bool(self.diff_ref) != bool(self.diff_digest):
            raise ValueError("diffRef and diffDigest must be provided together")
        if self.identity.endpoint_ref.startswith(("http://", "https://", "file://", "/")):
            raise ValueError("endpointRef cannot use provider-native URL or raw path authority")
        if self.validation_status == "valid" and not (
            self.workspace_cold_restore.available
            or self.live_reattach.available
        ):
            raise ValueError("valid checkpoint must support at least one recovery mode")
        if self.validation_status == "valid" and not (
            self.identity.terminal_ref and self.identity.diagnostics_ref
        ):
            raise ValueError("valid checkpoint requires terminal and diagnostics refs")
        if self.live_reattach.available and not (
            self.identity.provider_lease_ref
            and self.identity.host_lease_ref
            and self.identity.omnigent_host_id
            and self.identity.omnigent_session_id
        ):
            raise ValueError("live reattach requires current host and session authority")
        _reject_credential_material(self.model_dump(by_alias=True, mode="json"))
        return self


class OmnigentRestoreValidation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    valid: bool
    reason_code: str | None = Field(None, alias="reasonCode")
    message: str
    live_reattach: RecoveryCapability = Field(..., alias="liveReattach")
    workspace_cold_restore: RecoveryCapability = Field(
        ..., alias="workspaceColdRestore"
    )
    branch_creation: RecoveryCapability = Field(..., alias="branchCreation")


def validate_restore_material(
    manifest: OmnigentCheckpointManifest,
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    provider_profile_id: str,
    credential_generation: int,
    artifact_reader: Callable[[str], bytes],
    repository_baseline: str | None = None,
) -> OmnigentRestoreValidation:
    """Dereference and digest-check authority before any recovery side effect."""

    def denied(code: str, message: str) -> OmnigentRestoreValidation:
        capability = RecoveryCapability(available=False, reasonCode=code, message=message)
        return OmnigentRestoreValidation(
            valid=False,
            reasonCode=code,
            message=message,
            liveReattach=capability,
            workspaceColdRestore=capability,
            branchCreation=capability,
        )

    if (manifest.workflow_id, manifest.run_id, manifest.logical_step_id) != (
        workflow_id,
        run_id,
        logical_step_id,
    ):
        return denied("lineage_mismatch", "checkpoint does not belong to requested lineage")
    if manifest.identity.provider_profile_id != provider_profile_id:
        return denied("profile_mismatch", "Provider Profile identity is stale or mismatched")
    if manifest.identity.credential_generation != credential_generation:
        return denied(
            "credential_generation_mismatch",
            "credential generation is stale or mismatched",
        )
    if repository_baseline and manifest.baseline_commit != repository_baseline:
        return denied("baseline_mismatch", "repository baseline is incompatible")

    digest_refs: list[tuple[str | None, str | None]] = [
        (manifest.identity.external_state_ref, manifest.external_state_digest),
        (manifest.head_ref, manifest.head_digest),
        (manifest.checkpoint_ref, manifest.checkpoint_digest),
    ]
    if manifest.diff_ref:
        digest_refs.append((manifest.diff_ref, manifest.diff_digest))
    for ref, expected_digest in digest_refs:
        try:
            payload = artifact_reader(str(ref))
        except Exception:
            return denied(
                "artifact_unresolvable",
                f"required artifact is unavailable: {ref}",
            )
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if actual != expected_digest:
            return denied("digest_mismatch", f"artifact digest mismatch: {ref}")

    live = manifest.live_reattach
    cold = manifest.workspace_cold_restore
    branch = manifest.branch_creation
    valid = cold.available or live.available
    return OmnigentRestoreValidation(
        valid=valid,
        reasonCode=None if valid else "recovery_unavailable",
        message="restore material validated" if valid else "no recovery mode is available",
        liveReattach=live,
        workspaceColdRestore=cold,
        branchCreation=branch,
    )


def build_cold_restore_inputs(
    manifest: OmnigentCheckpointManifest,
    validation: OmnigentRestoreValidation,
    *,
    destination_workspace_locator: Mapping[str, Any],
    new_effective_launch_ref: str,
) -> dict[str, Any]:
    """Build the clean-workspace/new-host materialization contract."""

    if not validation.valid or not validation.workspace_cold_restore.available:
        raise ValueError("workspace cold restore is unavailable")
    destination = dict(destination_workspace_locator)
    if destination.get("kind") not in {"sandbox", "managed_runtime"}:
        raise ValueError("cold restore requires a newly authorized MoonMind workspace")
    if destination == manifest.workspace_locator:
        raise ValueError("cold restore destination must be a clean replacement workspace")
    return {
        "destinationWorkspaceLocator": destination,
        "baselineCommit": manifest.baseline_commit,
        "headRef": manifest.head_ref,
        "checkpointRef": manifest.checkpoint_ref,
        "diffRef": manifest.diff_ref,
        "patchCapability": manifest.patch_capability,
        "instructionRefs": list(manifest.instruction_refs),
        "contextRefs": list(manifest.context_refs),
        "externalStateRef": manifest.identity.external_state_ref,
        "providerProfileId": manifest.identity.provider_profile_id,
        "credentialGeneration": manifest.identity.credential_generation,
        "sourceEffectiveLaunchRef": manifest.source_effective_launch_ref,
        "effectiveLaunchRef": new_effective_launch_ref,
        "sourceCheckpointId": (
            f"{manifest.workflow_id}:{manifest.run_id}:{manifest.step_execution_id}:"
            f"{manifest.boundary}"
        ),
    }


def _reject_credential_material(value: Any, path: str = "checkpoint") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered_key = str(key).lower()
            if lowered_key in {
                "token",
                "password",
                "secret",
                "credentialbody",
                "oauthhome",
            }:
                raise ValueError(f"{path}.{key} cannot contain credential material")
            _reject_credential_material(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_credential_material(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in ("bearer ", "token=", "password=", "oauth/")
        ):
            raise ValueError(f"{path} must contain references, not credential material")


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
    "OmnigentCheckpointManifest",
    "OmnigentRecoveryMode",
    "OmnigentRestoreValidation",
    "RecoveryCapability",
    "build_cold_restore_inputs",
    "recovery_mode",
    "validate_branch_identity",
    "validate_cold_restore_target",
    "validate_restore_material",
]
