"""Runtime binding (section 17).

Separate fenced binding after plan commitment. Records acquired generations,
exact-host attestation, live model validation, workspace resolution, session
identity, cleanup authority. Generation sticky after binding.

Also covers lifecycle ordering invariants and control-plane integration.
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


class RuntimeBindingProviderLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    providerProfileRef: str = Field(alias="providerProfileRef")
    providerLeaseRef: str = Field(alias="providerLeaseRef")
    credentialGeneration: int = Field(alias="credentialGeneration")
    credentialRuntimeRef: str = Field(alias="credentialRuntimeRef")


class OmnigentRuntimeBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-runtime-binding.v1", alias="schemaVersion")
    runtimeBindingRef: str = Field(alias="runtimeBindingRef")
    executionPlanRef: str = Field(alias="executionPlanRef")
    executionScopeRef: str | None = Field(default=None, alias="executionScopeRef")
    providerLeases: dict[str, RuntimeBindingProviderLease] = Field(alias="providerLeases")
    hostBindingRef: str | None = Field(default=None, alias="hostBindingRef")
    hostLeaseRef: str | None = Field(default=None, alias="hostLeaseRef")
    hostLeaseGeneration: int | None = Field(default=None, alias="hostLeaseGeneration")
    omnigentHostId: str | None = Field(default=None, alias="omnigentHostId")
    hostHarnessAttestationRef: str | None = Field(default=None, alias="hostHarnessAttestationRef")
    exactHostCapabilityDecisionRef: str | None = Field(default=None, alias="exactHostCapabilityDecisionRef")
    workspaceResolutionRef: str | None = Field(default=None, alias="workspaceResolutionRef")
    modelOptionAttestationRef: str | None = Field(default=None, alias="modelOptionAttestationRef")
    skillDeliveryAttestationRef: str | None = Field(default=None, alias="skillDeliveryAttestationRef")
    omnigentRunnerRef: str | None = Field(default=None, alias="omnigentRunnerRef")
    omnigentSessionId: str | None = Field(default=None, alias="omnigentSessionId")
    chatBindingRef: str | None = Field(default=None, alias="chatBindingRef")
    cleanupAuthorityRefs: tuple[str, ...] = Field(default_factory=tuple, alias="cleanupAuthorityRefs")

    @model_validator(mode="after")
    def validate_refs(self) -> "OmnigentRuntimeBinding":
        if not self.executionPlanRef.startswith("omnigent-execution-plan:sha256:"):
            raise ValueError("executionPlanRef invalid")
        if not self.runtimeBindingRef.startswith("omnigent-runtime-binding:sha256:"):
            raise ValueError("runtimeBindingRef invalid")
        if self.executionScopeRef is not None and not self.executionScopeRef.strip():
            raise ValueError("executionScopeRef must be non-empty when recorded")
        host_values = (
            self.hostBindingRef,
            self.hostLeaseRef,
            self.hostLeaseGeneration,
            self.omnigentHostId,
        )
        if any(value is not None for value in host_values) and not all(
            value is not None for value in host_values
        ):
            raise ValueError("host runtime authority must be bound atomically")
        if self.hostLeaseGeneration is not None and self.hostLeaseGeneration < 1:
            raise ValueError("hostLeaseGeneration must be >=1")
        # Historical v1 bindings could record a session before chat authority
        # existed.  Continue to verify those payloads, while every new store
        # transition binds session + chat atomically.
        if self.chatBindingRef and not self.omnigentSessionId:
            raise ValueError(
                "Omnigent chat authority requires a bound session"
            )
        if self.omnigentRunnerRef and not self.omnigentSessionId:
            raise ValueError("Omnigent runner authority requires a bound session")
        for slot, lease in self.providerLeases.items():
            if lease.credentialGeneration < 1:
                raise ValueError(f"generation for {slot} must be >=1")
        # Verify digest-addressed fencing: recompute ref from remaining fields
        expected = compute_runtime_binding_ref(self)
        if expected != self.runtimeBindingRef:
            raise ValueError(f"runtimeBindingRef digest mismatch: expected {expected}")
        return self


def compute_runtime_binding_ref(binding: dict[str, Any] | OmnigentRuntimeBinding) -> str:
    if isinstance(binding, OmnigentRuntimeBinding):
        payload = binding.model_dump(by_alias=True, mode="json", exclude={"runtimeBindingRef"})
    else:
        payload = {k: v for k, v in dict(binding).items() if k != "runtimeBindingRef"}
    # ``omnigentRunnerRef`` and ``chatBindingRef`` were added to the v1 payload
    # after runtime bindings had already been persisted.  Keep the original
    # canonical representation while neither authority has been bound so an
    # older binding continues to verify after model validation supplies the new
    # fields' default ``None`` values.  Once either value is present both fields
    # participate in the digest.
    if (
        payload.get("omnigentRunnerRef") is None
        and payload.get("chatBindingRef") is None
    ):
        payload.pop("omnigentRunnerRef", None)
        payload.pop("chatBindingRef", None)
    # Bindings persisted before execution-scoped authority was introduced did
    # not include this field in their digest. New production bindings always
    # record it so two executions can safely realize the same immutable plan.
    if payload.get("executionScopeRef") is None:
        payload.pop("executionScopeRef", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "omnigent-runtime-binding:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def create_runtime_binding(
    *,
    executionPlanRef: str,
    executionScopeRef: str | None = None,
    providerLeases: dict[str, dict[str, Any]],
    hostBindingRef: str | None = None,
    hostLeaseRef: str | None = None,
    hostLeaseGeneration: int | None = None,
    omnigentHostId: str | None = None,
    hostHarnessAttestationRef: str | None = None,
    exactHostCapabilityDecisionRef: str | None = None,
    workspaceResolutionRef: str | None = None,
    modelOptionAttestationRef: str | None = None,
    skillDeliveryAttestationRef: str | None = None,
    omnigentRunnerRef: str | None = None,
    omnigentSessionId: str | None = None,
    chatBindingRef: str | None = None,
    cleanupAuthorityRefs: list[str] | None = None,
) -> OmnigentRuntimeBinding:
    raw: dict[str, Any] = {
        "schemaVersion": "moonmind.omnigent-runtime-binding.v1",
        "executionPlanRef": executionPlanRef,
        "executionScopeRef": executionScopeRef,
        "providerLeases": providerLeases,
        "hostBindingRef": hostBindingRef,
        "hostLeaseRef": hostLeaseRef,
        "hostLeaseGeneration": hostLeaseGeneration,
        "omnigentHostId": omnigentHostId,
        "hostHarnessAttestationRef": hostHarnessAttestationRef,
        "exactHostCapabilityDecisionRef": exactHostCapabilityDecisionRef,
        "workspaceResolutionRef": workspaceResolutionRef,
        "modelOptionAttestationRef": modelOptionAttestationRef,
        "skillDeliveryAttestationRef": skillDeliveryAttestationRef,
        "omnigentRunnerRef": omnigentRunnerRef,
        "omnigentSessionId": omnigentSessionId,
        "chatBindingRef": chatBindingRef,
        "cleanupAuthorityRefs": cleanupAuthorityRefs or [],
    }
    raw["runtimeBindingRef"] = compute_runtime_binding_ref(raw)
    return OmnigentRuntimeBinding.model_validate(raw)


def assert_runtime_binding_generation_sticky(
    *,
    binding: OmnigentRuntimeBinding,
    provider_profile_ref: str,
    slot: str,
    new_generation: int,
) -> None:
    lease = binding.providerLeases.get(slot)
    if lease is None:
        raise HarnessPlatformError(
            f"slot {slot} not in runtime binding",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    if lease.providerProfileRef != provider_profile_ref:
        raise HarnessPlatformError(
            "provider profile mismatch in runtime binding",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    if lease.credentialGeneration != new_generation:
        raise HarnessPlatformError(
            f"generation fenced: {new_generation} != {lease.credentialGeneration}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )


def assert_no_plan_mutation(
    *,
    original_plan_ref: str,
    new_plan_payload: dict[str, Any],
) -> None:
    """Runtime binding cannot change plan decisions (harness, HostClass, policy, model, skills, realizer)."""
    # Caller should compare plan ref; if different, it's a new plan with lineage
    new_ref = "omnigent-execution-plan:sha256:" + hashlib.sha256(
        json.dumps(new_plan_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    if new_ref != original_plan_ref:
        raise HarnessPlatformError(
            "runtime binding cannot mutate plan decisions",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
