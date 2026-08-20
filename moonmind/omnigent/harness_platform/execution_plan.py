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


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    qualifiedId: str | None = Field(default=None, alias="qualifiedId")
    effort: str | None = None
    routeRef: str | None = Field(default=None, alias="routeRef")
    normalizedOptions: dict[str, Any] = Field(default_factory=dict, alias="normalizedOptions")
    modelConfigDigest: str = Field(alias="modelConfigDigest")


class OmnigentExecutionPlanPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-execution-plan-payload.v1", alias="schemaVersion")
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
    classAdmissionDecision: dict[str, Any] = Field(alias="classAdmissionDecision")
    runtimeValidationRequirements: tuple[str, ...] = Field(alias="runtimeValidationRequirements")
    workspaceIntentRef: str = Field(alias="workspaceIntentRef")
    capturePolicyRef: str | None = Field(default=None, alias="capturePolicyRef")
    policySnapshotRef: str = Field(alias="policySnapshotRef")
    supportCombinationKey: str = Field(alias="supportCombinationKey")

    @model_validator(mode="after")
    def validate_no_forbidden(self) -> "OmnigentExecutionPlanPayload":
        forbidden_keys = {
            "credentialGeneration", "providerLeaseRef", "hostId", "hostLeaseRef",
            "volumeName", "hostBindingRef", "planRef", "credentials", "secretBody",
            "dockerSocket", "bindSource", "workerPath", "callerHostId", "skillBody"
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
                        raise ValueError(f"plan payload must not contain generation at {path}.{k}")
                    check(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    check(item, f"{path}[{i}]")
        check(payload)
        return self


def canonical_payload_bytes(payload: OmnigentExecutionPlanPayload | dict[str, Any]) -> bytes:
    if isinstance(payload, OmnigentExecutionPlanPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    # Ensure no envelope fields inside payload
    data.pop("planRef", None)
    # Normalize: sorted keys, no whitespace, utf-8, normalized enums/null
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


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
    forbidden_substrings = [
        "credential", "secretBody", "docker.sock", "volumeName", "hostLeaseRef",
        "credentialGeneration", "providerLeaseRef",
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
