"""Host-independent capture and recovery contracts for Omnigent checkpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ``WorkspaceLocator`` lives in ``moonmind.schemas``, whose package ``__init__``
# imports ``moonmind.schemas.temporal_models``, which in turn imports this module
# to type its ``omnigentCheckpoint`` field. Importing it at module top would make
# the resolution order-dependent (a plain ``import moonmind.omnigent.checkpoints``
# before ``moonmind.schemas`` raises a partial-initialization ImportError). Because
# this module uses ``from __future__ import annotations`` the field annotations are
# strings, so we can defer this import until after the models are declared and then
# rebuild them. See the bottom of the file.
if TYPE_CHECKING:
    from moonmind.schemas.workspace_locator_models import WorkspaceLocator

_DIGEST = r"^sha256:[0-9a-f]{64}$"
# Bound each manifest string so a structurally valid checkpoint cannot inflate the
# Temporal ``step_checkpoint.create`` activity payload past the compact-history policy.
_MAX_MANIFEST_FIELD_LENGTH = 4096
_ARTIFACT_FIELDS = {
    "externalStateRef",
    "terminalRef",
    "diagnosticsRef",
    "resourceManifestRef",
    "captureManifestRef",
    "headRef",
    "diffRef",
    "workspaceCheckpointRef",
    "instructionRef",
    "contextRef",
}


class OmnigentRecoveryMode(str, Enum):
    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"


class OmnigentCheckpointValidation(BaseModel):
    """Truthful, independently evaluated recovery projections."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    valid: bool
    live_reattach_available: bool = Field(alias="liveReattachAvailable")
    workspace_cold_restore_available: bool = Field(
        alias="workspaceColdRestoreAvailable"
    )
    branch_creation_available: bool = Field(alias="branchCreationAvailable")
    reasons: list[str] = Field(default_factory=list, max_length=20)
    capacity_blocked: bool = Field(False, alias="capacityBlocked")
    readiness_blocked: bool = Field(False, alias="readinessBlocked")


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

    schema_version: Literal["v2"] = Field("v2", alias="schemaVersion")
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    attempt_ordinal: int = Field(..., alias="attemptOrdinal", ge=1)
    boundary: str = Field(..., min_length=1)
    provider_profile_id: str = Field(..., alias="providerProfileId", min_length=1)
    credential_ref: str = Field(
        ...,
        alias="credentialRef",
        min_length=1,
        pattern=r"^(credential|secret)://\S+$",
    )
    credential_generation: int = Field(..., alias="credentialGeneration", ge=1)
    provider_lease_ref: str | None = Field(None, alias="providerLeaseRef")
    host_binding_ref: str = Field(..., alias="hostBindingRef", min_length=1)
    host_lease_ref: str | None = Field(None, alias="hostLeaseRef")
    endpoint_ref: str = Field(..., alias="endpointRef", min_length=1)
    omnigent_host_id: str | None = Field(None, alias="omnigentHostId")
    omnigent_session_id: str | None = Field(None, alias="omnigentSessionId")
    bridge_session_id: str = Field(..., alias="bridgeSessionId", min_length=1)
    external_state_ref: str = Field(..., alias="externalStateRef", min_length=1)
    external_state_digest: str = Field(..., alias="externalStateDigest", pattern=_DIGEST)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    terminal_ref: str | None = Field(None, alias="terminalRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    effective_launch_ref: str | None = Field(None, alias="effectiveLaunchRef", min_length=1)
    execution_profile_ref: str = Field(..., alias="executionProfileRef", min_length=1)
    launch_policy_ref: str = Field(..., alias="launchPolicyRef", min_length=1)
    last_bridge_event_cursor: str | None = Field(None, alias="lastBridgeEventCursor")
    first_message_id: str | None = Field(None, alias="firstMessageId")
    first_message_digest: str | None = Field(None, alias="firstMessageDigest", pattern=_DIGEST)
    resource_manifest_ref: str | None = Field(None, alias="resourceManifestRef")
    resource_manifest_digest: str | None = Field(
        None, alias="resourceManifestDigest", pattern=_DIGEST
    )
    capture_manifest_ref: str | None = Field(None, alias="captureManifestRef")
    capture_manifest_digest: str | None = Field(
        None, alias="captureManifestDigest", pattern=_DIGEST
    )
    patch_capable: bool = Field(False, alias="patchCapable")
    workspace_locator: WorkspaceLocator = Field(..., alias="workspaceLocator")
    baseline_commit: str = Field(..., alias="baselineCommit", min_length=1)
    head_commit: str = Field(..., alias="headCommit", min_length=1)
    head_ref: str = Field(..., alias="headRef", min_length=1)
    head_digest: str = Field(..., alias="headDigest", pattern=_DIGEST)
    diff_ref: str | None = Field(None, alias="diffRef")
    diff_digest: str | None = Field(None, alias="diffDigest", pattern=_DIGEST)
    workspace_checkpoint_ref: str = Field(..., alias="workspaceCheckpointRef", min_length=1)
    workspace_checkpoint_digest: str = Field(
        ..., alias="workspaceCheckpointDigest", pattern=_DIGEST
    )
    instruction_refs: list[str] = Field(
        default_factory=list, alias="instructionRefs", max_length=64
    )
    context_refs: list[str] = Field(
        default_factory=list, alias="contextRefs", max_length=64
    )
    source_branch: str = Field(..., alias="sourceBranch", min_length=1)
    output_branch: str | None = Field(None, alias="outputBranch")
    publication_state: str = Field(..., alias="publicationState", min_length=1)
    captured_at: datetime = Field(..., alias="capturedAt")
    producer_version: str = Field(..., alias="producerVersion", min_length=1)
    validation: OmnigentCheckpointValidation

    @model_validator(mode="after")
    def _reject_raw_credential_like_values(self) -> "OmnigentCheckpointIdentity":
        if self.effective_launch_ref is not None and not self.effective_launch_ref.startswith(
            "omnigent-launch:sha256:"
        ):
            raise ValueError("effectiveLaunchRef must identify an effective launch snapshot")
        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        for field, value in _string_leaves(dumped):
            if len(value) > _MAX_MANIFEST_FIELD_LENGTH:
                raise ValueError(
                    f"{field} exceeds the compact checkpoint field bound"
                )
            lowered = value.lower()
            if any(marker in lowered for marker in ("bearer ", "token=", "password=")):
                raise ValueError(f"{field} must be a reference, not credential data")
            if field in _ARTIFACT_FIELDS and not value.startswith("artifact://"):
                raise ValueError(f"{field} must be a durable artifact reference")
            if field in _ARTIFACT_FIELDS:
                parsed = urlparse(value)
                if parsed.scheme != "artifact" or not parsed.netloc:
                    raise ValueError(f"{field} must not be a local path or provider URL")
        for field, refs in (
            ("instructionRefs", self.instruction_refs),
            ("contextRefs", self.context_refs),
        ):
            if any(not ref.startswith("artifact://") for ref in refs):
                raise ValueError(f"{field} must contain durable artifact references")
        if (self.diff_ref is None) != (self.diff_digest is None):
            raise ValueError("diffRef and diffDigest must be supplied together")
        if (self.first_message_id is None) != (self.first_message_digest is None):
            raise ValueError(
                "firstMessageId and firstMessageDigest must be supplied together"
            )
        if self.validation.live_reattach_available and not all(
            (
                self.omnigent_host_id,
                self.omnigent_session_id,
                self.host_lease_ref,
                self.provider_lease_ref,
                self.first_message_id,
                self.last_bridge_event_cursor,
            )
        ):
            raise ValueError("live reattach capability is missing session authority")
        if self.validation.workspace_cold_restore_available and not all(
            (self.baseline_commit, self.head_ref, self.workspace_checkpoint_ref)
        ):
            raise ValueError("cold restore capability is missing workspace authority")
        return self


class OmnigentRestoreMaterial(BaseModel):
    """Validated inputs handed to the clean-workspace/new-host boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    baseline_commit: str = Field(alias="baselineCommit")
    head_commit: str = Field(alias="headCommit")
    workspace_locator: WorkspaceLocator = Field(alias="workspaceLocator")
    workspace_checkpoint_ref: str = Field(alias="workspaceCheckpointRef")
    workspace_checkpoint_digest: str = Field(alias="workspaceCheckpointDigest")
    head_ref: str = Field(alias="headRef")
    head_digest: str = Field(alias="headDigest")
    diff_ref: str | None = Field(None, alias="diffRef")
    diff_digest: str | None = Field(None, alias="diffDigest")
    immutable_input_refs: list[str] = Field(alias="immutableInputRefs")
    external_state_ref: str = Field(alias="externalStateRef")
    external_state_digest: str = Field(alias="externalStateDigest")
    provider_profile_id: str = Field(alias="providerProfileId")
    credential_generation: int = Field(alias="credentialGeneration")
    source_effective_launch_ref: str | None = Field(
        None, alias="sourceEffectiveLaunchRef"
    )
    launch_policy_ref: str = Field(alias="launchPolicyRef")


ArtifactReader = Callable[[str], bytes]


def _string_leaves(
    value: Mapping[str, Any], prefix: str = ""
) -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []
    for key, nested in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(nested, str):
            leaves.append((str(key), nested))
        elif isinstance(nested, Mapping):
            leaves.extend(_string_leaves(nested, path))
        elif isinstance(nested, list):
            for index, item in enumerate(nested):
                if isinstance(item, str):
                    leaves.append((f"{key}[{index}]", item))
                elif isinstance(item, Mapping):
                    leaves.extend(_string_leaves(item, f"{path}[{index}]"))
    return leaves


def validate_restore_material(
    checkpoint: OmnigentCheckpointIdentity,
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    step_execution_id: str,
    attempt_ordinal: int,
    boundary: str,
    provider_profile_id: str,
    credential_generation: int,
    repository_baseline: str,
    repository_head: str,
    artifact_reader: ArtifactReader,
) -> OmnigentCheckpointValidation:
    """Dereference and digest-check the complete authority set before restore."""

    reasons: list[str] = []
    if (checkpoint.workflow_id, checkpoint.run_id, checkpoint.logical_step_id) != (
        workflow_id,
        run_id,
        logical_step_id,
    ):
        reasons.append("lineage_mismatch")
    # A logical step can have multiple attempts and checkpoint boundaries; evidence
    # from a different attempt must not restore an obsolete workspace state, so the
    # complete Step Execution identity is validated, not just the logical lineage.
    if (
        checkpoint.step_execution_id,
        checkpoint.attempt_ordinal,
        checkpoint.boundary,
    ) != (step_execution_id, attempt_ordinal, boundary):
        reasons.append("step_execution_lineage_mismatch")
    if checkpoint.baseline_commit != repository_baseline:
        reasons.append("repository_baseline_mismatch")
    if checkpoint.head_commit != repository_head:
        reasons.append("repository_head_mismatch")
    if checkpoint.provider_profile_id != provider_profile_id:
        reasons.append("provider_profile_mismatch")
    if checkpoint.credential_generation != credential_generation:
        reasons.append("credential_generation_mismatch")
    digest_pairs = [
        (checkpoint.external_state_ref, checkpoint.external_state_digest),
        (checkpoint.head_ref, checkpoint.head_digest),
        (
            checkpoint.workspace_checkpoint_ref,
            checkpoint.workspace_checkpoint_digest,
        ),
        (checkpoint.diff_ref, checkpoint.diff_digest),
        (checkpoint.resource_manifest_ref, checkpoint.resource_manifest_digest),
        (checkpoint.capture_manifest_ref, checkpoint.capture_manifest_digest),
    ]
    for ref, expected_digest in digest_pairs:
        if ref is None and expected_digest is None:
            continue
        if ref is None or expected_digest is None:
            reasons.append("artifact_evidence_incomplete")
            continue
        try:
            payload = artifact_reader(ref)
        except Exception:
            reasons.append("artifact_unavailable")
            continue
        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
        if actual != expected_digest:
            reasons.append("artifact_digest_mismatch")
    # The immutable instruction/context inputs carry no digests in the manifest, so
    # confirm each one is at least dereferenceable before declaring the checkpoint
    # restorable; otherwise the new session launches with missing immutable inputs.
    for ref in (*checkpoint.instruction_refs, *checkpoint.context_refs):
        try:
            artifact_reader(ref)
        except Exception:
            reasons.append("immutable_input_unavailable")
    reasons = list(dict.fromkeys(reasons))[:20]
    cold = not reasons
    live = cold and all(
        (
            checkpoint.omnigent_host_id,
            checkpoint.omnigent_session_id,
            checkpoint.host_lease_ref,
            checkpoint.provider_lease_ref,
            checkpoint.first_message_id,
            checkpoint.last_bridge_event_cursor,
        )
    )
    return OmnigentCheckpointValidation(
        valid=cold,
        liveReattachAvailable=bool(live),
        workspaceColdRestoreAvailable=cold,
        branchCreationAvailable=cold,
        reasons=reasons,
    )


def materialize_cold_restore_inputs(
    checkpoint: OmnigentCheckpointIdentity,
    validation: OmnigentCheckpointValidation,
) -> OmnigentRestoreMaterial:
    """Compile authority-only restore inputs; host selection remains current policy."""

    if not validation.workspace_cold_restore_available:
        reason = validation.reasons[0] if validation.reasons else "restore_unavailable"
        raise ValueError(f"cold restore unavailable: {reason}")
    return OmnigentRestoreMaterial(
        baselineCommit=checkpoint.baseline_commit,
        headCommit=checkpoint.head_commit,
        workspaceLocator=checkpoint.workspace_locator,
        workspaceCheckpointRef=checkpoint.workspace_checkpoint_ref,
        workspaceCheckpointDigest=checkpoint.workspace_checkpoint_digest,
        headRef=checkpoint.head_ref,
        headDigest=checkpoint.head_digest,
        diffRef=checkpoint.diff_ref,
        diffDigest=checkpoint.diff_digest,
        immutableInputRefs=[*checkpoint.instruction_refs, *checkpoint.context_refs],
        externalStateRef=checkpoint.external_state_ref,
        externalStateDigest=checkpoint.external_state_digest,
        providerProfileId=checkpoint.provider_profile_id,
        credentialGeneration=checkpoint.credential_generation,
        sourceEffectiveLaunchRef=checkpoint.effective_launch_ref,
        launchPolicyRef=checkpoint.launch_policy_ref,
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


# Resolve the deferred ``WorkspaceLocator`` reference now that the models exist.
# By the time this runs, ``OmnigentCheckpointIdentity`` is already defined, so the
# reverse import performed by ``moonmind.schemas.temporal_models`` succeeds under
# either import order.
from moonmind.schemas.workspace_locator_models import (  # noqa: E402
    WorkspaceLocator as WorkspaceLocator,
)

OmnigentCheckpointIdentity.model_rebuild()
OmnigentRestoreMaterial.model_rebuild()


__all__ = [
    "CandidateWorkspaceAuthority",
    "OmnigentCheckpointIdentity",
    "OmnigentCheckpointValidation",
    "OmnigentRecoveryMode",
    "OmnigentRestoreMaterial",
    "materialize_cold_restore_inputs",
    "recovery_mode",
    "validate_restore_material",
    "validate_branch_identity",
    "validate_cold_restore_target",
]
