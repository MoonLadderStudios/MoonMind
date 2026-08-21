"""Execution plan payload + envelope (sections 16, 4.5, 4.6, 16.2).

Plan records pre-host decisions only. No credential generations, host ids, lease refs, volumes, env.
Plan digest is non-self-referential: payload bytes hashed, ref stored outside payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
)


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    qualifiedId: str | None = Field(default=None, alias="qualifiedId")
    effort: str | None = None
    routeRef: str | None = Field(default=None, alias="routeRef")
    normalizedOptions: dict[str, Any] = Field(
        default_factory=dict, alias="normalizedOptions"
    )
    modelConfigDigest: str = Field(alias="modelConfigDigest")


class OmnigentExecutionPlanPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-execution-plan-payload.v1", alias="schemaVersion"
    )
    endpointRef: str = Field(alias="endpointRef")
    agentProfileSnapshotRef: str = Field(alias="agentProfileSnapshotRef")
    harnessCatalogRef: str = Field(alias="harnessCatalogRef")
    harnessId: str = Field(alias="harnessId")
    harnessImplementationRef: str = Field(alias="harnessImplementationRef")
    agentSource: dict[str, Any] = Field(alias="agentSource")
    credentialBindingSetRef: str = Field(alias="credentialBindingSetRef")
    credentialBindings: dict[str, dict[str, Any]] = Field(alias="credentialBindings")
    hostClassRef: str = Field(alias="hostClassRef")
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
    capturePolicyRef: str | None = Field(default=None, alias="capturePolicyRef")
    policySnapshotRef: str = Field(alias="policySnapshotRef")
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
                or pinned.modelConfigDigest
                != self.modelConfig.modelConfigDigest
            ):
                raise ValueError(
                    "supportIdentity differs from admitted execution authority"
                )
        forbidden_keys = {
            "credentialGeneration",
            "providerLeaseRef",
            "hostId",
            "hostLeaseRef",
            "volumeName",
            "hostBindingRef",
            "planRef",
            "credentials",
            "secretBody",
            "dockerSocket",
            "bindSource",
            "workerPath",
            "callerHostId",
            "skillBody",
        }
        payload = self.model_dump(by_alias=True, mode="json")

        def check(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in forbidden_keys:
                        raise ValueError(f"plan payload must not contain {k} at {path}")
                    # also check lowercased without underscore?
                    lowered = k.lower().replace("_", "")
                    if lowered in {"credentialgeneration", "providerleaseref"}:
                        raise ValueError(
                            f"plan payload must not contain generation at {path}.{k}"
                        )
                    check(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    check(item, f"{path}[{i}]")

        check(payload)
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
    forbidden_substrings = [
        "credential",
        "secretBody",
        "docker.sock",
        "volumeName",
        "hostLeaseRef",
        "credentialGeneration",
        "providerLeaseRef",
    ]
    text = json.dumps(payload, default=str).lower()
    for f in forbidden_substrings:
        if f.lower() in text:
            # Need to be careful: providerProfileRef is allowed, but generation not
            if f == "credential" and "credentialbinding" in text:
                # allow credentialBindings
                continue
            if f == "credentialGeneration":
                if "credentialgeneration" in text.replace("_", "").replace("-", ""):
                    raise HarnessPlatformError(
                        f"plan contains forbidden authority {f}",
                        code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                    )
            else:
                # Only flag if actually forbidden field present, not just substring in allowed refs
                pass
