"""Execution plan payload + envelope (sections 16, 4.5, 4.6, 16.2).

Plan records pre-host decisions only. No credential generations, host ids, lease refs, volumes, env.
Plan digest is non-self-referential: payload bytes hashed, ref stored outside payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.support import SupportKeyPayload

from moonmind.omnigent.harness_platform.credential_bindings import CredentialBinding
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
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
    normalizedOptions: dict[str, Any] = Field(
        default_factory=dict, alias="normalizedOptions"
    )
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
    # Which evidence tier backs admission. Plans persisted before this field
    # existed always carried protected-tier evidence, so the default preserves
    # their in-flight interpretation.
    supportTier: Literal["supported", "deployment_qualified"] = Field(
        default="supported", alias="supportTier"
    )
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

    schemaVersion: str = Field(
        "moonmind.omnigent-execution-plan-payload.v1", alias="schemaVersion"
    )
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
    resolvedTools: dict[str, Any] = Field(default_factory=dict, alias="resolvedTools")
    classAdmissionDecision: dict[str, Any] = Field(alias="classAdmissionDecision")
    runtimeValidationRequirements: tuple[str, ...] = Field(
        alias="runtimeValidationRequirements"
    )
    workspaceIntentRef: str = Field(alias="workspaceIntentRef")
    workspaceMutation: str = Field("allowed", alias="workspaceMutation")
    capturePolicyRef: str | None = Field(default=None, alias="capturePolicyRef")
    capturePolicy: dict[str, Any] = Field(default_factory=dict, alias="capturePolicy")
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
    # Added as optional for replay compatibility with execution plans admitted
    # before MoonLadderStudios/MoonMind#3701 recorded the complete support
    # identity. New admissions always populate it.
    supportIdentity: SupportKeyPayload | None = Field(
        default=None, alias="supportIdentity"
    )

    @model_validator(mode="after")
    def validate_no_forbidden(self) -> "OmnigentExecutionPlanPayload":
        if self.supportIdentity is not None:
            if (
                compute_support_combination_key(self.supportIdentity)
                != self.supportCombinationKey
            ):
                raise ValueError(
                    "supportCombinationKey does not match supportIdentity"
                )
            pinned = self.supportIdentity
            if (
                pinned.harnessImplementationRef != self.harnessImplementationRef
                or pinned.hostClassRef != self.hostClassRef
                or pinned.launchPolicyRef != self.launchPolicyRef
                or pinned.executionRealizerRef != self.executionRealizerRef
                or pinned.modelConfigDigest != self.modelConfig.modelConfigDigest
            ):
                raise ValueError(
                    "supportIdentity differs from admitted execution authority"
                )
        if self.workspaceMutation not in {
            "allowed",
            "read_only",
            "checkpoint_branch",
        }:
            raise ValueError("workspaceMutation is unsupported")
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


def canonical_payload_bytes(
    payload: OmnigentExecutionPlanPayload | dict[str, Any]
) -> bytes:
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
        "supportIdentity",
    ):
        if data.get(optional_v1_field) is None:
            data.pop(optional_v1_field, None)
    # Normalize: sorted keys, no whitespace, utf-8, normalized enums/null
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


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

    schemaVersion: str = Field(
        "moonmind.omnigent-execution-plan-envelope.v1", alias="schemaVersion"
    )
    planRef: str = Field(alias="planRef")
    payload: OmnigentExecutionPlanPayload

    @model_validator(mode="after")
    def validate_digest(self) -> "OmnigentExecutionPlanEnvelope":
        expected = compute_plan_ref(self.payload)
        if self.planRef != expected:
            raise ValueError(f"planRef digest mismatch: {self.planRef} != {expected}")
        return self


def create_execution_plan_envelope(
    payload: OmnigentExecutionPlanPayload | dict[str, Any]
) -> OmnigentExecutionPlanEnvelope:
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


def bind_runtime_request_authority(
    envelope: OmnigentExecutionPlanEnvelope,
    *,
    resolved_skillset_ref: str | None,
    model: Any = None,
    effort: Any = None,
) -> OmnigentExecutionPlanEnvelope:
    """Bind late-resolved step authority before any runtime side effect.

    Skill snapshots are resolved by the deterministic Run workflow after API
    admission, and a step may author a model override.  Both decisions must be
    incorporated into a new immutable plan before the Activity acquires a
    Provider Profile lease or launches a host.
    """

    payload = envelope.payload.model_dump(by_alias=True, mode="json")
    changed = False

    requested_skill_ref = str(resolved_skillset_ref or "").strip() or None
    admitted_skills = dict(payload.get("resolvedSkills") or {})
    admitted_skill_ref = str(
        admitted_skills.get("resolvedSkillSetRef") or ""
    ).strip() or None
    if admitted_skill_ref and requested_skill_ref != admitted_skill_ref:
        raise HarnessPlatformError(
            "runtime request Skill snapshot differs from admitted authority",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
    if requested_skill_ref and admitted_skill_ref is None:
        skill_digest = "sha256:" + hashlib.sha256(
            requested_skill_ref.encode("utf-8")
        ).hexdigest()
        delivery_digest = hashlib.sha256(
            json.dumps(
                {
                    "resolvedSkillSetRef": requested_skill_ref,
                    "resolvedSkillSetDigest": skill_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        payload["resolvedSkills"] = {
            **admitted_skills,
            "resolvedSkillSetRef": requested_skill_ref,
            "resolvedSkillSetDigest": skill_digest,
            "skillDeliveryRef": f"skill-delivery:sha256:{delivery_digest}",
        }
        changed = True

    model_config = dict(payload["model"])
    requested_model = str(model or "").strip() or None
    requested_effort = str(effort or "").strip() or None
    effective_model = requested_model or model_config.get("qualifiedId")
    effective_effort = (
        requested_effort if effort is not None else model_config.get("effort")
    )
    if not effective_model:
        raise HarnessPlatformError(
            "an explicit model is required before Omnigent host acquisition",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    if (
        effective_model != model_config.get("qualifiedId")
        or effective_effort != model_config.get("effort")
    ):
        model_digest = compute_model_config_digest(
            qualifiedId=effective_model,
            effort=effective_effort,
            routeRef=model_config.get("routeRef"),
            normalizedOptions=dict(model_config.get("normalizedOptions") or {}),
        )
        payload["model"] = {
            **model_config,
            "qualifiedId": effective_model,
            "effort": effective_effort,
            "modelConfigDigest": model_digest,
        }
        support_identity = payload.get("supportIdentity")
        if not isinstance(support_identity, dict):
            raise HarnessPlatformError(
                "step model override requires complete support identity",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        updated_identity = {**support_identity, "modelConfigDigest": model_digest}
        parsed_identity = SupportKeyPayload.model_validate(updated_identity)
        payload["supportIdentity"] = parsed_identity.model_dump(
            by_alias=True, mode="json"
        )
        payload["supportCombinationKey"] = compute_support_combination_key(
            parsed_identity
        )
        changed = True

    return create_execution_plan_envelope(payload) if changed else envelope


def verify_execution_plan_envelope(
    envelope: dict[str, Any] | OmnigentExecutionPlanEnvelope,
) -> OmnigentExecutionPlanEnvelope:
    if isinstance(envelope, OmnigentExecutionPlanEnvelope):
        return envelope
    # Verify without mutation: canonicalize only payload and compare
    parsed = OmnigentExecutionPlanEnvelope.model_validate(envelope)
    return parsed


def execution_support_identity(
    envelope: OmnigentExecutionPlanEnvelope,
) -> dict[str, Any]:
    """Project exact, secret-free support evidence from admitted authority."""

    identity = envelope.payload.supportIdentity
    if identity is None:
        # Replay-visible plans admitted before the complete identity was
        # embedded remain readable, but cannot be mistaken for current
        # combination-qualified acceptance evidence.
        return {
            "supportCombinationKey": envelope.payload.supportCombinationKey,
            "identityComplete": False,
        }
    return {
        **identity.model_dump(by_alias=True, mode="json"),
        "supportCombinationKey": envelope.payload.supportCombinationKey,
        "identityComplete": True,
    }


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
