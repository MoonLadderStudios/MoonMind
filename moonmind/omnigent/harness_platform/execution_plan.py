"""Execution plan payload + envelope (sections 16, 4.5, 4.6, 16.2).

Plan records pre-host decisions only. No credential generations, host ids, lease refs, volumes, env.
Plan digest is non-self-referential: payload bytes hashed, ref stored outside payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.credential_bindings import CredentialBinding
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


_MAX_PLAN_PAYLOAD_BYTES = 256 * 1024
_MAX_PLAN_COLLECTION_ITEMS = 256
_MAX_PLAN_NESTING_DEPTH = 16
_MAX_PLAN_STRING_BYTES = 16 * 1024

_FORBIDDEN_PLAN_KEYS = {
    # Post-acquisition and live runtime authority belongs in the runtime binding.
    "credentialgeneration",
    "credentialruntimeref",
    "providerleaseref",
    "hostid",
    "omnigenthostid",
    "hostleaseref",
    "hostleasegeneration",
    "hostbindingref",
    "runtimebindingref",
    "replacementgeneration",
    "cleanupref",
    # Secret bodies and common credential spellings must never enter the plan.
    "apikey",
    "accesskey",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "authorization",
    "bearer",
    "clientsecret",
    "cookie",
    "credentials",
    "password",
    "privatekey",
    "secret",
    "secretbody",
    "sessioncookie",
    "skillbody",
    "token",
    # Mutable host realization belongs in the runtime binding.
    "bindsource",
    "dockersocket",
    "hostpath",
    "mountsource",
    "volumename",
    "workerpath",
    "workspacepath",
    "callerhostid",
}


def _normalized_plan_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _validate_plan_data(obj: Any, *, path: str = "$", depth: int = 0) -> None:
    """Reject live authority, secret bodies, and unbounded embedded values."""

    if depth > _MAX_PLAN_NESTING_DEPTH:
        raise ValueError(
            f"plan payload exceeds maximum nesting depth at {path}"
        )
    if isinstance(obj, dict):
        if len(obj) > _MAX_PLAN_COLLECTION_ITEMS:
            raise ValueError(f"plan payload mapping is too large at {path}")
        for key, value in obj.items():
            normalized_key = _normalized_plan_key(key)
            if normalized_key in _FORBIDDEN_PLAN_KEYS:
                raise ValueError(
                    f"plan payload must not contain {key} at {path}"
                )
            _validate_plan_data(value, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        if len(obj) > _MAX_PLAN_COLLECTION_ITEMS:
            raise ValueError(f"plan payload collection is too large at {path}")
        for index, value in enumerate(obj):
            _validate_plan_data(value, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(obj, str) and len(obj.encode("utf-8")) > _MAX_PLAN_STRING_BYTES:
        raise ValueError(f"plan payload string is too large at {path}")


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    qualifiedId: str | None = Field(default=None, alias="qualifiedId")
    effort: str | None = None
    routeRef: str | None = Field(default=None, alias="routeRef")
    normalizedOptions: dict[str, Any] = Field(default_factory=dict, alias="normalizedOptions")
    modelConfigDigest: str = Field(alias="modelConfigDigest")


class ExecutionAuthority(BaseModel):
    """Immutable product-boundary inputs that the plan was compiled from.

    Bodies deliberately remain in the artifact system.  The plan carries only
    opaque references and content digests so Temporal admission can prove that
    retries, continuations, remediation, and checkpoint branches are using the
    same authored authority without copying instructions into workflow history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authoredRequestRef: str = Field(alias="authoredRequestRef")
    authoredRequestDigest: str = Field(alias="authoredRequestDigest")
    taskInputSnapshotRef: str = Field(alias="taskInputSnapshotRef")
    taskInputSnapshotDigest: str = Field(alias="taskInputSnapshotDigest")
    repositoryIntentRef: str = Field(alias="repositoryIntentRef")
    continuationPolicyRef: str = Field(alias="continuationPolicyRef")
    remediationPolicyRef: str = Field(alias="remediationPolicyRef")
    checkpointPolicyRef: str = Field(alias="checkpointPolicyRef")
    publicationPolicyRef: str = Field(alias="publicationPolicyRef")
    timingPolicyRef: str = Field(alias="timingPolicyRef")
    failurePolicyRef: str = Field(alias="failurePolicyRef")


class AdmissionAuthority(BaseModel):
    """Immutable evidence required before a new session may be admitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supportEvidenceRef: str = Field(alias="supportEvidenceRef")
    supportEvidenceDigest: str = Field(alias="supportEvidenceDigest")
    featureGeneration: str = Field(alias="featureGeneration")
    replayCompatibilityVersion: str = Field(alias="replayCompatibilityVersion")
    rollbackPolicyVersion: str = Field(alias="rollbackPolicyVersion")

    @model_validator(mode="after")
    def validate_authority(self) -> "AdmissionAuthority":
        if not self.supportEvidenceRef.startswith("artifact:"):
            raise ValueError("supportEvidenceRef must be artifact-backed")
        if not self.supportEvidenceDigest.startswith("sha256:"):
            raise ValueError("supportEvidenceDigest must be a sha256 digest")
        for field_name in (
            "featureGeneration",
            "replayCompatibilityVersion",
            "rollbackPolicyVersion",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        return self


class OmnigentExecutionPlanPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-execution-plan-payload.v1", alias="schemaVersion")
    authority: ExecutionAuthority | None = None
    admissionAuthority: AdmissionAuthority | None = Field(
        default=None, alias="admissionAuthority"
    )
    endpointRef: str = Field(alias="endpointRef")
    agentProfileSnapshotRef: str = Field(alias="agentProfileSnapshotRef")
    harnessCatalogRef: str = Field(alias="harnessCatalogRef")
    harnessId: str = Field(alias="harnessId")
    harnessImplementationRef: str = Field(alias="harnessImplementationRef")
    agentSource: dict[str, Any] = Field(alias="agentSource")
    credentialBindingSetRef: str = Field(alias="credentialBindingSetRef")
    credentialBindings: dict[str, CredentialBinding] = Field(alias="credentialBindings")
    hostClassRef: str = Field(alias="hostClassRef")
    hostImageRef: str | None = Field(default=None, alias="hostImageRef")
    omnigentHostBuildDigest: str | None = Field(
        default=None, alias="omnigentHostBuildDigest"
    )
    hostArchitecture: str | None = Field(default=None, alias="hostArchitecture")
    launchPolicyRef: str = Field(alias="launchPolicyRef")
    executionRealizerRef: str = Field(alias="executionRealizerRef")
    modelConfig: ModelConfig = Field(alias="model")
    resolvedSkills: dict[str, Any] = Field(alias="resolvedSkills")
    classAdmissionDecision: dict[str, Any] = Field(alias="classAdmissionDecision")
    runtimeValidationRequirements: tuple[str, ...] = Field(alias="runtimeValidationRequirements")
    workspaceIntentRef: str = Field(alias="workspaceIntentRef")
    capturePolicyRef: str | None = Field(default=None, alias="capturePolicyRef")
    policySnapshotRef: str = Field(alias="policySnapshotRef")
    policySnapshotDigest: str | None = Field(
        default=None, alias="policySnapshotDigest"
    )
    effectiveLaunchSnapshotRef: str | None = Field(
        default=None, alias="effectiveLaunchSnapshotRef"
    )
    effectiveLaunchSnapshotDigest: str | None = Field(
        default=None, alias="effectiveLaunchSnapshotDigest"
    )
    supportCombinationKey: str = Field(alias="supportCombinationKey")

    @model_validator(mode="after")
    def validate_no_forbidden(self) -> "OmnigentExecutionPlanPayload":
        exact_launch_authority = (
            self.hostImageRef,
            self.omnigentHostBuildDigest,
            self.hostArchitecture,
            self.policySnapshotDigest,
            self.effectiveLaunchSnapshotRef,
            self.effectiveLaunchSnapshotDigest,
        )
        if any(value is not None for value in exact_launch_authority) and not all(
            value is not None for value in exact_launch_authority
        ):
            raise ValueError(
                "exact launch authority must be recorded atomically"
            )
        payload = self.model_dump(by_alias=True, mode="json")
        _validate_plan_data(payload)
        if len(canonical_payload_bytes(payload)) > _MAX_PLAN_PAYLOAD_BYTES:
            raise ValueError("plan payload exceeds maximum canonical size")
        return self


def canonical_payload_bytes(payload: OmnigentExecutionPlanPayload | dict[str, Any]) -> bytes:
    if isinstance(payload, OmnigentExecutionPlanPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    # Ensure no envelope fields inside payload
    data.pop("planRef", None)
    # These exact launch-authority fields were added to the v1 payload after
    # plans had already been persisted.  Missing values retain the historical
    # canonical representation so in-flight plans continue to verify; every
    # newly compiled plan supplies all of them.
    for optional_v1_field in (
        "hostImageRef",
        "omnigentHostBuildDigest",
        "hostArchitecture",
        "policySnapshotDigest",
        "effectiveLaunchSnapshotRef",
        "effectiveLaunchSnapshotDigest",
        "admissionAuthority",
    ):
        if data.get(optional_v1_field) is None:
            data.pop(optional_v1_field, None)
    # Normalize: sorted keys, no whitespace, utf-8, normalized enums/null
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def execution_plan_support_evidence(
    payload: OmnigentExecutionPlanPayload,
    *,
    feature_generation: str,
    replay_compatibility_version: str,
    rollback_policy_version: str,
) -> dict[str, Any]:
    """Build the canonical, secret-free admission evidence for one plan."""

    return {
        "schemaVersion": "moonmind.omnigent-execution-support-evidence.v1",
        "supportCombinationKey": payload.supportCombinationKey,
        "hostImageRef": payload.hostImageRef,
        "omnigentHostBuildDigest": payload.omnigentHostBuildDigest,
        "hostArchitecture": payload.hostArchitecture,
        "harnessImplementationRef": payload.harnessImplementationRef,
        "effectiveLaunchSnapshotRef": payload.effectiveLaunchSnapshotRef,
        "effectiveLaunchSnapshotDigest": payload.effectiveLaunchSnapshotDigest,
        "executionRealizerRef": payload.executionRealizerRef,
        "featureGeneration": feature_generation,
        "replayCompatibilityVersion": replay_compatibility_version,
        "rollbackPolicyVersion": rollback_policy_version,
    }


def compute_plan_ref(payload: OmnigentExecutionPlanPayload | dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()
    return f"omnigent-execution-plan:sha256:{digest}"


def compute_model_config_digest(
    *,
    qualifiedId: str | None,
    effort: str | None,
    routeRef: str | None,
    normalizedOptions: dict[str, Any],
) -> str:
    payload = {
        "qualifiedId": qualifiedId,
        "effort": effort,
        "routeRef": routeRef,
        "normalizedOptions": normalizedOptions,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class OmnigentExecutionPlanEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-execution-plan-envelope.v1", alias="schemaVersion")
    planRef: str = Field(alias="planRef")
    payload: OmnigentExecutionPlanPayload

    @model_validator(mode="after")
    def validate_digest(self) -> "OmnigentExecutionPlanEnvelope":
        expected = compute_plan_ref(self.payload)
        if self.planRef != expected:
            raise ValueError(f"planRef digest mismatch: {self.planRef} != {expected}")
        return self


def create_execution_plan_envelope(payload: OmnigentExecutionPlanPayload | dict[str, Any]) -> OmnigentExecutionPlanEnvelope:
    if isinstance(payload, dict):
        # Validate payload first
        parsed = OmnigentExecutionPlanPayload.model_validate(payload)
    else:
        parsed = payload
    ref = compute_plan_ref(parsed)
    return OmnigentExecutionPlanEnvelope.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-envelope.v1",
            "planRef": ref,
            "payload": parsed.model_dump(by_alias=True, mode="json"),
        }
    )


def verify_execution_plan_envelope(envelope: dict[str, Any] | OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope:
    if isinstance(envelope, OmnigentExecutionPlanEnvelope):
        return envelope
    # Verify without mutation: canonicalize only payload and compare
    parsed = OmnigentExecutionPlanEnvelope.model_validate(envelope)
    return parsed


def forbidden_plan_check(payload: dict[str, Any]) -> None:
    try:
        _validate_plan_data(payload)
        if len(canonical_payload_bytes(payload)) > _MAX_PLAN_PAYLOAD_BYTES:
            raise ValueError("plan payload exceeds maximum canonical size")
    except ValueError as exc:
        raise HarnessPlatformError(
            str(exc),
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        ) from exc
