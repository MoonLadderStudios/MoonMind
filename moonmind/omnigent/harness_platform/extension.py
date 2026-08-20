"""Extension boundary (sections 29, 29.2).

- Upstream harness metadata may be insufficient; MoonMind uses approved companion descriptor keyed by canonical implementation identity.
- Community plugins launchable only when approved, pinned, attested, materialized, enforceable, and support-qualified.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity, TrustState
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError, HarnessPlatformFailure

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CompanionDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harnessImplementationRef: str = Field(alias="harnessImplementationRef")
    credentialSlots: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="credentialSlots")
    acceptedMaterializerClasses: tuple[str, ...] = Field(default_factory=tuple, alias="acceptedMaterializerClasses")
    hostFeatures: tuple[str, ...] = Field(default_factory=tuple, alias="hostFeatures")
    requiredBinaries: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="requiredBinaries")
    mutableStatePaths: tuple[str, ...] = Field(default_factory=tuple, alias="mutableStatePaths")
    validationProbes: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="validationProbes")
    knownLimitations: tuple[str, ...] = Field(default_factory=tuple, alias="knownLimitations")

    @model_validator(mode="after")
    def validate_no_forbidden(self) -> "CompanionDescriptor":
        if not self.harnessImplementationRef.startswith("omnigent-harness-implementation:sha256:"):
            raise ValueError("harnessImplementationRef invalid")
        forbidden_paths = {"/", "~", ".", "docker.sock"}
        for path in self.mutableStatePaths:
            if any(fp in path for fp in forbidden_paths):
                raise ValueError(f"mutableStatePaths contains forbidden path: {path}")
        # Cannot declare secret values, arbitrary mounts, Docker authority, policy exceptions
        # Ensure no secret values present
        for slot in self.credentialSlots:
            if isinstance(slot, dict) and "secretValue" in slot:
                raise ValueError("companion descriptor cannot declare secret values")
        return self


COMPANION_DESCRIPTORS: dict[str, CompanionDescriptor] = {}


def register_companion_descriptor(data: dict[str, Any]) -> CompanionDescriptor:
    desc = CompanionDescriptor.model_validate(data)
    COMPANION_DESCRIPTORS[desc.harnessImplementationRef] = desc
    return desc


def get_companion_descriptor(implementation_ref: str) -> CompanionDescriptor | None:
    return COMPANION_DESCRIPTORS.get(implementation_ref)


def validate_community_plugin_launchable(
    *,
    implementation: HarnessImplementationIdentity,
    catalog_id: str,
    trust_state: TrustState,
    host_class_declares: bool,
    exact_host_attests: bool,
    materializer_approved: bool,
    capabilities_enforceable: bool,
    support_classification: str,
) -> None:
    checks = [
        (trust_state == TrustState.plugin_approved, HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED),
        (host_class_declares, HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE),
        (exact_host_attests, HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY),
        (materializer_approved, HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE),
        (capabilities_enforceable, HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED),
        (support_classification not in {"quarantined", "discovered_only"}, HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED),
    ]
    for ok, code in checks:
        if not ok:
            raise HarnessPlatformError(
                f"community plugin {catalog_id} not launchable: {code}",
                code=code,
            )
