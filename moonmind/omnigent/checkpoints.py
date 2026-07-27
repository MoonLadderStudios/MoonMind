"""Host-independent recovery decisions for Omnigent OAuth checkpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Sequence

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
    artifact_digests: dict[str, str] = Field(..., alias="artifactDigests")
    head_commit: str = Field(..., alias="headCommit", min_length=1)
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
        required_digest_refs = set(refs.values())
        required_digest_refs.update(self.instruction_refs)
        required_digest_refs.update(self.context_refs)
        if self.identity.terminal_ref:
            required_digest_refs.add(self.identity.terminal_ref)
        if self.identity.diagnostics_ref:
            required_digest_refs.add(self.identity.diagnostics_ref)
        if set(self.artifact_digests) != required_digest_refs:
            raise ValueError(
                "artifactDigests must pin every independently resolvable checkpoint artifact"
            )
        for ref, digest in self.artifact_digests.items():
            if not ref.startswith("artifact://"):
                raise ValueError("artifactDigests keys must be durable artifact references")
            if not _is_sha256_digest(digest):
                raise ValueError("artifactDigests values must be sha256 digests")
        expected_legacy_digests = {
            self.identity.external_state_ref: self.external_state_digest,
            self.head_ref: self.head_digest,
            self.checkpoint_ref: self.checkpoint_digest,
        }
        if self.diff_ref and self.diff_digest:
            expected_legacy_digests[self.diff_ref] = self.diff_digest
        if any(
            self.artifact_digests.get(ref) != digest
            for ref, digest in expected_legacy_digests.items()
        ):
            raise ValueError("named checkpoint digests must match artifactDigests")
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
    required_profile_id: str = Field(..., alias="requiredProfileId")
    required_launch_policy_ref: str = Field(..., alias="requiredLaunchPolicyRef")
    readiness_blocked: bool = Field(False, alias="readinessBlocked")
    capacity_blocked: bool = Field(False, alias="capacityBlocked")
    validated_refs: list[str] = Field(default_factory=list, alias="validatedRefs")
    validated_digests: dict[str, str] = Field(
        default_factory=dict, alias="validatedDigests"
    )


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
    repository_head: str | None = None,
    step_execution_id: str,
    attempt_ordinal: int,
    boundary: str,
    expected_first_message_identity: str | None = None,
    expected_first_message_digest: str | None = None,
    expected_bridge_event_cursor: str | None = None,
    current_provider_lease_ref: str | None = None,
    current_host_lease_ref: str | None = None,
    host_registered: bool = False,
    session_valid: bool = False,
    profile_ready: bool = True,
    capacity_available: bool = True,
    supported_patch_capabilities: Sequence[str] = ("git_patch_v1",),
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
            requiredProfileId=manifest.identity.provider_profile_id,
            requiredLaunchPolicyRef=manifest.launch_policy_ref,
            readinessBlocked=not profile_ready,
            capacityBlocked=not capacity_available,
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
    if repository_head and repository_head not in {
        manifest.baseline_commit,
        manifest.head_commit,
    }:
        return denied("head_mismatch", "repository head is incompatible")
    identity_expectations = (
        ("step_execution_mismatch", step_execution_id, manifest.step_execution_id),
        ("attempt_mismatch", attempt_ordinal, manifest.attempt_ordinal),
        ("boundary_mismatch", boundary, manifest.boundary),
        (
            "first_message_mismatch",
            expected_first_message_identity,
            manifest.first_message_identity,
        ),
        (
            "first_message_mismatch",
            expected_first_message_digest,
            manifest.first_message_digest,
        ),
        (
            "event_cursor_mismatch",
            expected_bridge_event_cursor,
            manifest.last_bridge_event_cursor,
        ),
    )
    for code, expected, actual in identity_expectations:
        if expected != actual:
            return denied(code, code.replace("_", " "))
    if manifest.patch_capability not in set(supported_patch_capabilities):
        return denied("unsupported_patch", "checkpoint patch capability is unsupported")

    required_refs = {
        manifest.identity.external_state_ref,
        manifest.execution_profile_ref,
        manifest.launch_policy_ref,
        manifest.resource_manifest_ref,
        manifest.capture_manifest_ref,
        manifest.head_ref,
        manifest.checkpoint_ref,
        *manifest.instruction_refs,
        *manifest.context_refs,
    }
    if manifest.diff_ref:
        required_refs.add(manifest.diff_ref)
    if manifest.identity.terminal_ref:
        required_refs.add(manifest.identity.terminal_ref)
    if manifest.identity.diagnostics_ref:
        required_refs.add(manifest.identity.diagnostics_ref)
    resolved: dict[str, bytes] = {}
    for ref in sorted(required_refs):
        try:
            resolved[ref] = artifact_reader(ref)
        except Exception:
            return denied(
                "artifact_unresolvable",
                f"required artifact is unavailable: {ref}",
            )
    validated_digests: dict[str, str] = {}
    for ref, expected_digest in manifest.artifact_digests.items():
        payload = resolved[ref]
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        validated_digests[ref] = actual
        if actual != expected_digest:
            return denied("digest_mismatch", f"artifact digest mismatch: {ref}")

    cold_denial = None
    if not profile_ready:
        cold_denial = "profile_not_ready"
    elif not capacity_available:
        cold_denial = "capacity_unavailable"
    cold = RecoveryCapability(
        available=cold_denial is None,
        reasonCode=cold_denial,
        message=None if cold_denial is None else cold_denial.replace("_", " "),
    )
    live_denial = None
    if current_provider_lease_ref != manifest.identity.provider_lease_ref:
        live_denial = "provider_lease_mismatch"
    elif current_host_lease_ref != manifest.identity.host_lease_ref:
        live_denial = "host_lease_mismatch"
    elif not host_registered:
        live_denial = "host_unavailable"
    elif not session_valid:
        live_denial = "session_unavailable"
    live = RecoveryCapability(
        available=live_denial is None,
        reasonCode=live_denial,
        message=None if live_denial is None else live_denial.replace("_", " "),
    )
    branch = RecoveryCapability(
        available=cold.available,
        reasonCode=cold.reason_code,
        message=cold.message,
    )
    valid = cold.available or live.available
    return OmnigentRestoreValidation(
        valid=valid,
        reasonCode=None if valid else "recovery_unavailable",
        message="restore material validated" if valid else "no recovery mode is available",
        liveReattach=live,
        workspaceColdRestore=cold,
        branchCreation=branch,
        requiredProfileId=manifest.identity.provider_profile_id,
        requiredLaunchPolicyRef=manifest.launch_policy_ref,
        readinessBlocked=not profile_ready,
        capacityBlocked=not capacity_available,
        validatedRefs=sorted(resolved),
        validatedDigests=validated_digests,
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
        "headCommit": manifest.head_commit,
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


def materialize_cold_restore(
    manifest: OmnigentCheckpointManifest,
    validation: OmnigentRestoreValidation,
    *,
    destination_workspace_locator: Mapping[str, Any],
    new_effective_launch_ref: str,
    checkout_baseline: Callable[[Mapping[str, Any], str], None],
    apply_workspace_artifact: Callable[[Mapping[str, Any], str, str], None],
    restore_immutable_refs: Callable[
        [Mapping[str, Any], Sequence[str], Sequence[str]], None
    ],
    launch_fresh_session: Callable[[Mapping[str, Any]], Any],
) -> Any:
    """Execute cold materialization through explicit workspace/launch boundaries."""

    inputs = build_cold_restore_inputs(
        manifest,
        validation,
        destination_workspace_locator=destination_workspace_locator,
        new_effective_launch_ref=new_effective_launch_ref,
    )
    destination = inputs["destinationWorkspaceLocator"]
    checkout_baseline(destination, inputs["baselineCommit"])
    apply_workspace_artifact(
        destination,
        inputs["checkpointRef"],
        inputs["patchCapability"],
    )
    if inputs["diffRef"]:
        apply_workspace_artifact(
            destination,
            inputs["diffRef"],
            inputs["patchCapability"],
        )
    restore_immutable_refs(
        destination,
        inputs["instructionRefs"],
        inputs["contextRefs"],
    )
    return launch_fresh_session(inputs)


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


def _is_sha256_digest(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


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
    "materialize_cold_restore",
    "recovery_mode",
    "validate_branch_identity",
    "validate_cold_restore_target",
    "validate_restore_material",
]
