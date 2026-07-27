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


OMNIGENT_CHECKPOINT_CONTENT_TYPE = (
    "application/vnd.moonmind.omnigent-checkpoint+json;version=1"
)


class RecoveryCapability(BaseModel):
    """One independently evaluated checkpoint recovery capability."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    available: bool
    reason: str | None = Field(None, max_length=160)

    @model_validator(mode="after")
    def _reason_matches_availability(self) -> "RecoveryCapability":
        if self.available and self.reason is not None:
            raise ValueError("available capability cannot include a denial reason")
        if not self.available and not self.reason:
            raise ValueError("unavailable capability requires a denial reason")
        return self


class OmnigentRestoreValidation(BaseModel):
    """Bounded, API-safe validation and recovery projection."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: Literal["valid", "degraded", "invalid"]
    live_reattach: RecoveryCapability = Field(alias="liveReattach")
    workspace_cold_restore: RecoveryCapability = Field(alias="workspaceColdRestore")
    branch_creation: RecoveryCapability = Field(alias="branchCreation")
    checked_refs: list[str] = Field(default_factory=list, alias="checkedRefs")


class OmnigentCheckpointManifest(BaseModel):
    """Complete host-independent evidence captured at a Step boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    content_type: Literal[OMNIGENT_CHECKPOINT_CONTENT_TYPE] = Field(
        OMNIGENT_CHECKPOINT_CONTENT_TYPE, alias="contentType"
    )
    workflow_id: str = Field(alias="workflowId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    logical_step_id: str = Field(alias="logicalStepId", min_length=1)
    step_execution_id: str = Field(alias="stepExecutionId", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    boundary: str = Field(min_length=1)
    session: dict[str, Any]
    workspace: dict[str, Any]
    host: dict[str, Any]
    credentials: dict[str, Any]
    source: dict[str, Any] = Field(default_factory=dict)
    capture_time: datetime = Field(alias="captureTime")
    producer_version: str = Field(alias="producerVersion", min_length=1)
    validation: OmnigentRestoreValidation

    @model_validator(mode="after")
    def _validate_authority_planes(self) -> "OmnigentCheckpointManifest":
        required_session = {
            "externalStateRef",
            "externalStateDigest",
            "bridgeSessionId",
            "omnigentSessionId",
            "omnigentHostId",
            "idempotencyKey",
            "lastCommittedEventCursor",
            "firstMessageDigest",
            "terminalRef",
            "terminalDigest",
            "diagnosticsRef",
            "diagnosticsDigest",
            "resourceManifestRef",
            "resourceManifestDigest",
            "captureManifestRef",
            "captureManifestDigest",
        }
        required_workspace = {
            "workspaceLocator",
            "baselineCommit",
            "checkpointRef",
            "checkpointDigest",
            "patchCapability",
            "sourceBranch",
            "outputBranch",
            "publicationState",
        }
        required_host = {
            "executionProfile",
            "launchPolicyRef",
            "launchPolicyDigest",
            "effectiveLaunchRef",
            "providerProfileId",
            "providerProfileRef",
            "providerProfileDigest",
            "providerLeaseRef",
            "providerLeaseDigest",
            "hostBindingRef",
            "hostBindingDigest",
            "hostLeaseRef",
            "hostLeaseDigest",
            "endpointRef",
            "endpointDigest",
        }
        required_credentials = {"credentialGeneration"}
        for plane, required in (
            (self.session, required_session),
            (self.workspace, required_workspace),
            (self.host, required_host),
            (self.credentials, required_credentials),
        ):
            if self.validation.status == "valid":
                missing = sorted(key for key in required if not plane.get(key))
                if missing:
                    raise ValueError(
                        "valid checkpoint missing required evidence: "
                        + ", ".join(missing)
                    )
        payload = self.model_dump(by_alias=True, mode="json")
        _reject_unsafe_restore_authority(payload)
        return self


def _reject_unsafe_restore_authority(value: Any, path: str = "checkpoint") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {
                "credentialbody",
                "oauthhome",
                "oauthvolume",
                "workspacepath",
                "containerpath",
            }:
                raise ValueError(f"{path}.{key} is not durable checkpoint authority")
            _reject_unsafe_restore_authority(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_unsafe_restore_authority(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "bearer ",
                "token=",
                "password=",
                "ghp_",
                "github_pat_",
                "-----begin private key-----",
            )
        ):
            raise ValueError(f"{path} contains credential data")
        if lowered.startswith(
            (
                "file://",
                "/",
                "http://",
                "https://",
                "ssh://",
                "docker://",
                "podman://",
            )
        ):
            raise ValueError(f"{path} contains provider-native or host-local authority")


def artifact_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


_REF_DIGEST_PAIRS: tuple[tuple[str, str], ...] = (
    ("externalStateRef", "externalStateDigest"),
    ("checkpointRef", "checkpointDigest"),
    ("headRef", "headDigest"),
    ("diffRef", "diffDigest"),
    ("resourceManifestRef", "resourceManifestDigest"),
    ("captureManifestRef", "captureManifestDigest"),
    ("terminalRef", "terminalDigest"),
    ("diagnosticsRef", "diagnosticsDigest"),
    ("launchPolicyRef", "launchPolicyDigest"),
    ("providerProfileRef", "providerProfileDigest"),
    ("providerLeaseRef", "providerLeaseDigest"),
    ("hostBindingRef", "hostBindingDigest"),
    ("hostLeaseRef", "hostLeaseDigest"),
    ("endpointRef", "endpointDigest"),
)

_SESSION_REQUIRED = frozenset(
    {
        "externalStateRef",
        "externalStateDigest",
        "bridgeSessionId",
        "idempotencyKey",
        "lastCommittedEventCursor",
        "firstMessageDigest",
        "resourceManifestRef",
        "resourceManifestDigest",
        "captureManifestRef",
        "captureManifestDigest",
        "terminalRef",
        "terminalDigest",
        "diagnosticsRef",
        "diagnosticsDigest",
    }
)
_WORKSPACE_REQUIRED = frozenset(
    {
        "workspaceLocator",
        "baselineCommit",
        "checkpointRef",
        "checkpointDigest",
        "patchCapability",
        "sourceBranch",
        "outputBranch",
        "publicationState",
    }
)
_HOST_REQUIRED = frozenset(
    {
        "executionProfile",
        "launchPolicyRef",
        "launchPolicyDigest",
        "effectiveLaunchRef",
        "providerProfileId",
        "providerProfileRef",
        "providerProfileDigest",
        "providerLeaseRef",
        "providerLeaseDigest",
        "hostBindingRef",
        "hostBindingDigest",
        "hostLeaseRef",
        "hostLeaseDigest",
        "endpointRef",
        "endpointDigest",
    }
)


def _first_missing(plane: Mapping[str, Any], required: frozenset[str]) -> str | None:
    missing = sorted(key for key in required if not plane.get(key))
    return f"evidence_missing:{missing[0]}" if missing else None


def build_omnigent_checkpoint_manifest(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    step_execution_id: str,
    attempt_ordinal: int,
    boundary: str,
    session: Mapping[str, Any],
    workspace: Mapping[str, Any],
    host: Mapping[str, Any],
    credentials: Mapping[str, Any],
    captured_at: datetime,
    producer_version: str,
    source: Mapping[str, Any] | None = None,
) -> OmnigentCheckpointManifest:
    """Construct a truthful manifest from authoritative boundary evidence.

    Capture is allowed to persist partial evidence, but a partial capture never
    advertises a recovery capability. The restore validator remains the authority
    that promotes complete, resolvable evidence to an available capability.
    """

    session_payload = dict(session)
    workspace_payload = dict(workspace)
    host_payload = dict(host)
    credentials_payload = dict(credentials)
    session_reason = _first_missing(session_payload, _SESSION_REQUIRED)
    workspace_reason = _first_missing(workspace_payload, _WORKSPACE_REQUIRED)
    host_reason = _first_missing(host_payload, _HOST_REQUIRED)
    credential_reason = (
        None
        if credentials_payload.get("credentialGeneration")
        else "evidence_missing:credentialGeneration"
    )
    shared_reason = host_reason or credential_reason
    live_reason = session_reason or shared_reason or "restore_material_not_validated"
    cold_reason = workspace_reason or shared_reason or "restore_material_not_validated"
    branch_reason = workspace_reason or session_reason or shared_reason or (
        "restore_material_not_validated"
    )
    validation = OmnigentRestoreValidation(
        status="invalid" if session_reason and workspace_reason else "degraded",
        liveReattach=RecoveryCapability(available=False, reason=live_reason),
        workspaceColdRestore=RecoveryCapability(available=False, reason=cold_reason),
        branchCreation=RecoveryCapability(available=False, reason=branch_reason),
    )
    return OmnigentCheckpointManifest(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        stepExecutionId=step_execution_id,
        attemptOrdinal=attempt_ordinal,
        boundary=boundary,
        session=session_payload,
        workspace=workspace_payload,
        host=host_payload,
        credentials=credentials_payload,
        source=dict(source or {}),
        captureTime=captured_at,
        producerVersion=producer_version,
        validation=validation,
    )


def validate_restore_material(
    manifest: OmnigentCheckpointManifest,
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    step_execution_id: str | None = None,
    attempt_ordinal: int | None = None,
    boundary: str | None = None,
    provider_profile_id: str,
    credential_generation: int,
    artifacts: Mapping[str, bytes],
    host_available: bool = False,
    session_valid: bool = False,
    first_message_consistent: bool = True,
    event_cursor_consistent: bool = True,
    repository_compatible: Callable[[str, str | None], bool] | None = None,
) -> OmnigentRestoreValidation:
    """Digest-check all declared evidence and evaluate modes independently."""

    reasons: list[str] = []
    if (
        manifest.workflow_id != workflow_id
        or manifest.run_id != run_id
        or manifest.logical_step_id != logical_step_id
    ):
        reasons.append("lineage_mismatch")
    if (
        step_execution_id is not None
        and manifest.step_execution_id != step_execution_id
    ):
        reasons.append("step_execution_mismatch")
    if attempt_ordinal is not None and manifest.attempt_ordinal != attempt_ordinal:
        reasons.append("attempt_mismatch")
    if boundary is not None and manifest.boundary != boundary:
        reasons.append("boundary_mismatch")
    if manifest.host.get("providerProfileId") != provider_profile_id:
        reasons.append("provider_profile_mismatch")
    if manifest.credentials.get("credentialGeneration") != credential_generation:
        reasons.append("credential_generation_stale")

    session_missing = _first_missing(manifest.session, _SESSION_REQUIRED)
    workspace_missing = _first_missing(manifest.workspace, _WORKSPACE_REQUIRED)
    host_missing = _first_missing(manifest.host, _HOST_REQUIRED)
    credential_missing = (
        None
        if manifest.credentials.get("credentialGeneration")
        else "evidence_missing:credentialGeneration"
    )

    checked: list[str] = []
    for plane in (
        manifest.session,
        manifest.workspace,
        manifest.host,
        manifest.source,
    ):
        for ref_key, digest_key in _REF_DIGEST_PAIRS:
            ref = plane.get(ref_key)
            expected_digest = plane.get(digest_key)
            if not ref:
                continue
            if not str(ref).startswith("artifact://"):
                reasons.append("non_artifact_authority")
                continue
            payload = artifacts.get(str(ref))
            if payload is None:
                reasons.append("artifact_unresolved")
                continue
            checked.append(str(ref))
            if not expected_digest or artifact_digest(payload) != expected_digest:
                reasons.append("artifact_digest_mismatch")

    baseline = manifest.workspace.get("baselineCommit")
    head = manifest.workspace.get("headCommit")
    if head and baseline and not manifest.workspace.get("checkpointRef"):
        reasons.append("repository_evidence_incomplete")
    if (
        repository_compatible is not None
        and baseline
        and not repository_compatible(str(baseline), str(head) if head else None)
    ):
        reasons.append("repository_incompatible")
    if manifest.workspace.get("patchCapability") not in {
        None,
        "git_patch_v1",
        "worktree_archive_v1",
        "git_commit_v1",
    }:
        reasons.append("patch_format_unsupported")
    if not first_message_consistent:
        reasons.append("first_message_mismatch")
    if not event_cursor_consistent:
        reasons.append("event_cursor_mismatch")

    shared_reason = reasons[0] if reasons else host_missing or credential_missing
    workspace_reason_base = shared_reason or workspace_missing
    session_reason_base = shared_reason or session_missing
    workspace_available = workspace_reason_base is None
    session_material_available = session_reason_base is None
    live_available = session_material_available and host_available and session_valid
    live_reason = (
        None
        if live_available
        else session_reason_base
        or ("host_unavailable" if not host_available else "session_unavailable")
    )
    workspace_reason = (
        None
        if workspace_available
        else workspace_reason_base or "workspace_evidence_missing"
    )
    branch_available = workspace_available and session_material_available
    branch_reason = (
        None
        if branch_available
        else shared_reason
        or (
            session_reason_base or "session_evidence_missing"
            if workspace_available
            else workspace_reason_base or "workspace_evidence_missing"
        )
    )
    status: Literal["valid", "degraded", "invalid"] = (
        "valid"
        if live_available and workspace_available
        else "degraded"
        if workspace_available or session_material_available
        else "invalid"
    )
    return OmnigentRestoreValidation(
        status=status,
        liveReattach=RecoveryCapability(available=live_available, reason=live_reason),
        workspaceColdRestore=RecoveryCapability(
            available=workspace_available, reason=workspace_reason
        ),
        branchCreation=RecoveryCapability(
            available=branch_available, reason=branch_reason
        ),
        checkedRefs=list(dict.fromkeys(checked)),
    )


def materialize_cold_restore_inputs(
    manifest: OmnigentCheckpointManifest,
    validation: OmnigentRestoreValidation,
    *,
    replacement_workspace_locator: Mapping[str, Any],
    effective_launch_ref: str,
) -> dict[str, Any]:
    """Build path-free inputs for the canonical #3507 workspace boundary."""

    if not validation.workspace_cold_restore.available:
        raise ValueError(
            validation.workspace_cold_restore.reason or "workspace_restore_unavailable"
        )
    return {
        "schemaVersion": "v1",
        "sourceCheckpoint": {
            "workflowId": manifest.workflow_id,
            "runId": manifest.run_id,
            "logicalStepId": manifest.logical_step_id,
            "stepExecutionId": manifest.step_execution_id,
            "boundary": manifest.boundary,
        },
        "workspaceLocator": dict(replacement_workspace_locator),
        "baselineCommit": manifest.workspace["baselineCommit"],
        "headRef": manifest.workspace.get("headRef"),
        "diffRef": manifest.workspace.get("diffRef"),
        "checkpointRef": manifest.workspace["checkpointRef"],
        "instructionRefs": list(manifest.workspace.get("instructionRefs") or []),
        "contextRefs": list(manifest.workspace.get("contextRefs") or []),
        "externalStateRef": manifest.session.get("externalStateRef"),
        "providerProfileId": manifest.host["providerProfileId"],
        "credentialGeneration": manifest.credentials["credentialGeneration"],
        "sourceLaunchPolicyRef": manifest.host["launchPolicyRef"],
        "effectiveLaunchRef": effective_launch_ref,
    }


def recovery_capability_projection(
    manifest: OmnigentCheckpointManifest,
    validation: OmnigentRestoreValidation,
    *,
    capacity_ready: bool,
    readiness_reason: str | None = None,
) -> dict[str, Any]:
    """Return the bounded Workflow Detail/API checkpoint projection."""

    capacity_block = None
    if not capacity_ready:
        capacity_block = (readiness_reason or "capacity_unavailable")[:160]
    return {
        "validationStatus": validation.status,
        "liveSessionReattach": validation.live_reattach.model_dump(
            by_alias=True, mode="json"
        ),
        "workspaceColdRestore": validation.workspace_cold_restore.model_dump(
            by_alias=True, mode="json"
        ),
        "branchCreation": validation.branch_creation.model_dump(
            by_alias=True, mode="json"
        ),
        "requiredProfileId": manifest.host.get("providerProfileId"),
        "requiredLaunchPolicyRef": manifest.host.get("launchPolicyRef"),
        "capacityReady": capacity_ready,
        "capacityBlockingReason": capacity_block,
        "artifactEvidence": {
            "externalStateRef": manifest.session.get("externalStateRef"),
            "externalStateDigest": manifest.session.get("externalStateDigest"),
            "workspaceCheckpointRef": manifest.workspace.get("checkpointRef"),
            "workspaceCheckpointDigest": manifest.workspace.get("checkpointDigest"),
            "headRef": manifest.workspace.get("headRef"),
            "headDigest": manifest.workspace.get("headDigest"),
            "diffRef": manifest.workspace.get("diffRef"),
            "diffDigest": manifest.workspace.get("diffDigest"),
        },
    }


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
    "OMNIGENT_CHECKPOINT_CONTENT_TYPE",
    "OmnigentCheckpointManifest",
    "OmnigentCheckpointIdentity",
    "OmnigentRecoveryMode",
    "OmnigentRestoreValidation",
    "RecoveryCapability",
    "artifact_digest",
    "build_omnigent_checkpoint_manifest",
    "materialize_cold_restore_inputs",
    "recovery_capability_projection",
    "recovery_mode",
    "validate_branch_identity",
    "validate_cold_restore_target",
    "validate_restore_material",
]
