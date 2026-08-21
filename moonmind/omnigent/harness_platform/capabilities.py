"""Two-stage capability negotiation (section 15).

Pre-host admission: workflow ∩ profile ∩ catalog ∩ HostClass ∩ materializer ∩ bridge ∩ policy
Exact-host validation: class-decision ∩ attestation ∩ mounts ∩ model ∩ session
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class ClassAdmissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requiredSatisfied: tuple[str, ...] = Field(alias="requiredSatisfied")
    preferredSatisfied: tuple[str, ...] = Field(alias="preferredSatisfied")
    degraded: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


class ExactHostCapabilityDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classAdmissionRef: str = Field(alias="classAdmissionRef")
    exactHostAttested: bool = Field(alias="exactHostAttested")
    requiredSatisfied: tuple[str, ...] = Field(alias="requiredSatisfied")
    missingRequired: tuple[str, ...] = Field(alias="missingRequired")
    degraded: tuple[str, ...] = ()


def _decision_ref(prefix: str, value: BaseModel | dict[str, Any]) -> str:
    payload = (
        value.model_dump(by_alias=True, mode="json")
        if isinstance(value, BaseModel)
        else dict(value)
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:sha256:{sha256(canonical.encode()).hexdigest()}"


def compute_class_admission_ref(
    decision: ClassAdmissionDecision | dict[str, Any],
) -> str:
    """Return the immutable reference for a class admission decision."""

    return _decision_ref("class-admission", decision)


def compute_exact_host_capability_decision_ref(
    decision: ExactHostCapabilityDecision | dict[str, Any],
) -> str:
    """Return the immutable reference stored by a runtime binding."""

    return _decision_ref("exact-host-capability", decision)


def compute_class_admission(
    *,
    workflow_requirements: list[str],
    profile_requirements: dict[str, list[str]],
    catalog_capabilities: dict[str, bool | str | None],
    host_class_capabilities: dict[str, bool],
    materializer_capabilities: dict[str, bool],
    bridge_capabilities: dict[str, bool],
    launch_policy_capabilities: list[str],
) -> ClassAdmissionDecision:
    """Compute class-level admission decision.

    - Missing required class-level capability blocks plan creation.
    - Unknown required class-level capability blocks plan creation.
    """
    required = set(workflow_requirements) | set(profile_requirements.get("required", []))
    preferred = set(profile_requirements.get("preferred", []))

    # Build effective capability map: true if any source says true
    # For class-level, we intersect: capability must be present in catalog+host+materializer+bridge+policy
    # Represented as checking each required capability exists positively somewhere required.
    satisfied: list[str] = []
    degraded: list[str] = []
    unknown: list[str] = []
    missing: list[str] = []

    for cap in required:
        # Unknown if not declared anywhere
        declared = (
            cap in catalog_capabilities
            or cap in host_class_capabilities
            or cap in materializer_capabilities
            or cap in bridge_capabilities
            or cap in launch_policy_capabilities
        )
        if not declared:
            unknown.append(cap)
            continue
        # Check if positively supported across required layers
        # For simplicity: must be True in host_class and (catalog or bridge) and materializer if applicable
        # We'll check host_class and catalog/bridge
        host_ok = host_class_capabilities.get(cap, True)  # if not host-specific, ignore
        catalog_ok = catalog_capabilities.get(cap)
        bridge_ok = bridge_capabilities.get(cap)
        materializer_ok = materializer_capabilities.get(cap, True)
        # Launch-policy capabilities must positively include control-plane caps
        policy_ok = True
        if cap in {"interrupt", "terminate", "clear_context", "streaming"}:
            policy_ok = cap in launch_policy_capabilities
        elif cap in catalog_capabilities or cap in bridge_capabilities:
            policy_ok = True
        else:
            policy_ok = True  # non-policy caps defer to other layers

        # If any layer explicitly says False or unsupported, then missing
        # catalog/bridge None means unknown already handled; False means unsupported
        if catalog_ok is False or bridge_ok is False or host_ok is False or materializer_ok is False or policy_ok is False:
            missing.append(cap)
        elif catalog_ok is None and bridge_ok is None:
            # Unknown value remains unknown (not coerced)
            unknown.append(cap)
        else:
            satisfied.append(cap)

    if missing:
        raise HarnessPlatformError(
            f"required capabilities unsupported: {missing}",
            code=HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED,
        )
    if unknown:
        raise HarnessPlatformError(
            f"required capabilities unknown: {unknown}",
            code=HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNKNOWN,
        )

    # Preferred handling: missing preferred may be degraded, unknown recorded as unknown
    pref_satisfied: list[str] = []
    pref_degraded: list[str] = []
    pref_unknown: list[str] = []
    for cap in preferred:
        if cap in satisfied:
            pref_satisfied.append(cap)
            continue
        # Check if available
        catalog_val = catalog_capabilities.get(cap)
        host_val = host_class_capabilities.get(cap)
        if catalog_val is None and host_val is None:
            pref_unknown.append(cap)
        elif catalog_val is False or host_val is False:
            pref_degraded.append(cap)
        else:
            pref_satisfied.append(cap)

    return ClassAdmissionDecision.model_validate(
        {
            "requiredSatisfied": sorted(satisfied),
            "preferredSatisfied": sorted(pref_satisfied),
            "degraded": sorted(pref_degraded),
            "unknown": sorted(pref_unknown),
        }
    )


def validate_exact_host_capabilities(
    *,
    class_decision: ClassAdmissionDecision,
    attestation_capabilities: dict[str, bool],
    mount_attested: bool,
    network_attested: bool,
    model_attested: bool,
    required_capabilities: list[str],
) -> ExactHostCapabilityDecision:
    missing = []
    for cap in required_capabilities:
        if attestation_capabilities.get(cap) is not True:
            missing.append(cap)
    if not mount_attested:
        missing.append("workspace.bind")
    if not network_attested:
        missing.append("restricted-egress")
    if not model_attested:
        missing.append("live-model-option")

    if missing:
        raise HarnessPlatformError(
            f"exact-host capability mismatch: missing {missing}",
            code=HarnessPlatformFailure.OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH,
        )
    return ExactHostCapabilityDecision.model_validate(
        {
            "classAdmissionRef": compute_class_admission_ref(class_decision),
            "exactHostAttested": True,
            "requiredSatisfied": sorted(required_capabilities),
            "missingRequired": [],
            "degraded": [],
        }
    )


# Representative capability rules helper (section 15.4)
REPRESENTATIVE_RULES: dict[str, list[str]] = {
    "Active cancellation": ["interrupt", "bridge.interrupt", "policy.interrupt"],
    "Token streaming": ["streaming"],
    "Warm continuation": ["warm-reattach"],
    "Cold continuation": ["workspace.checkpoint"],
    "Tool approval": ["elicitation"],
    "Subagent fanout": ["subagents"],
    "Image input": ["images"],
    "Reasoning effort": ["effortFamily"],
    "Model override": ["modelFamily"],
    "Repository mutation": ["repository.mutation", "workspace.bind", "git"],
    "Restricted egress": ["restricted-egress"],
}
