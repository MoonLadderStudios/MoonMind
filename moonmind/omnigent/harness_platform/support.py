"""Support classification and identity (sections 8.4, 24).

Support applies to digest of exact combination. Two runs differing by model,
options, effort, or realizer have different keys. Evidence for one does not
qualify another. Classification: fully_managed, connected_host, experimental,
discovered_only, quarantined.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupportClassification(StrEnum):
    fully_managed = "fully_managed"
    connected_host = "connected_host"
    experimental = "experimental"
    discovered_only = "discovered_only"
    quarantined = "quarantined"


class SupportKeyPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    omnigentServerBuildRef: str = Field(alias="omnigentServerBuildRef")
    omnigentHostBuildRef: str = Field(alias="omnigentHostBuildRef")
    harnessImplementationRef: str = Field(alias="harnessImplementationRef")
    vendorRuntimeRefs: tuple[str, ...] = Field(alias="vendorRuntimeRefs")
    agentSourceRef: str = Field(alias="agentSourceRef")
    materializerRefs: tuple[str, ...] = Field(alias="materializerRefs")
    providerCompatibilityClass: str = Field(alias="providerCompatibilityClass")
    hostClassRef: str = Field(alias="hostClassRef")
    architecture: str
    launchPolicyRef: str = Field(alias="launchPolicyRef")
    modelConfigDigest: str = Field(alias="modelConfigDigest")
    executionRealizerRef: str = Field(alias="executionRealizerRef")
    requiredCapabilitiesDigest: str = Field(alias="requiredCapabilitiesDigest")

    @model_validator(mode="after")
    def validate(self) -> "SupportKeyPayload":
        for field in ("omnigentServerBuildRef", "omnigentHostBuildRef", "harnessImplementationRef", "agentSourceRef", "hostClassRef", "launchPolicyRef", "executionRealizerRef"):
            val = getattr(self, field)
            if not str(val).strip():
                raise ValueError(f"{field} required")
        if not self.modelConfigDigest.startswith("sha256:"):
            raise ValueError("modelConfigDigest must be sha256")
        if not self.requiredCapabilitiesDigest.startswith("sha256:"):
            raise ValueError("requiredCapabilitiesDigest must be sha256")
        return self


def compute_support_combination_key(payload: SupportKeyPayload | dict[str, Any]) -> str:
    if isinstance(payload, SupportKeyPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return "omnigent-support:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


# ``requiredCapabilitiesDigest`` records the capabilities one workflow asked
# for, not what the deployment can run. Class admission already refuses a plan
# whose required capabilities are unsupported or unknown, and it does so before
# the support key exists, so a deployment cannot be qualified per capability
# set without qualifying every workflow shape in advance.
DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS = frozenset({"requiredCapabilitiesDigest"})


def compute_deployment_qualification_key(
    payload: SupportKeyPayload | dict[str, Any],
) -> str:
    """Return the deployment-scoped projection of a support combination.

    Deployment qualification proves this deployment can run one exact harness,
    host, image, realizer, credential, and model combination. It deliberately
    excludes per-run request variance so an ordinary workflow is admissible
    without re-qualifying the deployment for every capability set.
    """

    if isinstance(payload, SupportKeyPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    projected = {
        key: value
        for key, value in data.items()
        if key not in DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS
    }
    canonical = json.dumps(
        projected, sort_keys=True, separators=(",", ":"), default=str
    )
    return (
        "omnigent-deployment-qualification:sha256:"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )


def compute_required_capabilities_digest(capabilities: list[str]) -> str:
    canonical = json.dumps(sorted(capabilities), separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def classify_support(
    *,
    trust_state: str,
    launchable: bool,
    has_conformance_evidence: bool,
    has_experimental_evidence: bool,
    is_static_connected: bool,
    host_owned_auth: bool,
) -> SupportClassification:
    if trust_state in {"quarantined", "blocked"}:
        return SupportClassification.quarantined
    if not launchable:
        return SupportClassification.discovered_only
    if not has_conformance_evidence:
        if not has_experimental_evidence:
            # No smoke evidence: remain discovered_only until bounded smoke passes
            return SupportClassification.discovered_only
        return SupportClassification.experimental
    if is_static_connected and host_owned_auth:
        return SupportClassification.connected_host
    # fully_managed requires on-demand + managed credential + unattended + validation + cleanup etc.
    # For now, if has_conformance and not connected, it's fully managed
    return SupportClassification.fully_managed


# Realizer registry: executionRealizerRef is trusted planner selected, never workflow-authored
KNOWN_REALIZERS = {
    "codex-profile-bound@1": {"description": "Existing Codex profile-bound coordinator", "deprecated": False},
    "generic-omnigent-host@1": {"description": "Generic Omnigent host realizer", "deprecated": False},
}


def validate_realizer(ref: str) -> None:
    if ref not in KNOWN_REALIZERS:
        raise ValueError(f"execution realizer {ref} unavailable")
