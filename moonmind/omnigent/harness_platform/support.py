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


# Model, effort, normalized model options, and Required Capabilities are per-run
# selections, not deployment substrate identity. Class admission rejects
# unsupported capabilities before the support key exists, and launch preflight
# validates the selected model against the exact host/provider catalog. Keeping
# either digest in the local deployment-qualification key would make every
# otherwise valid default-profile variation require manual requalification.
# Protected support evidence remains bound to the complete exact support key.
DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS = frozenset(
    {"modelConfigDigest", "requiredCapabilitiesDigest"}
)

# none@1 fast-path: credentialless runs mount no secret (target kind none,
# cleanup none). Build digests, harness implementation, vendor runtime, and
# agent source churn on every release/upgrade but are already gated elsewhere
# (catalog sync, trust record, launch preflight). For none@1 only, deployment
# qualification keeps the isolation-relevant triple — credential class,
# image/host/launch policy, provider route, realizer — and ignores the
# volatile build fields so routine upgrades don't force manual requalification.
# Auth-bearing materializers stay exact: a secret mount must never silently
# qualify across builds.
NONE_DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS = frozenset(
    {
        "omnigentServerBuildRef",
        "omnigentHostBuildRef",
        "harnessImplementationRef",
        "vendorRuntimeRefs",
        "agentSourceRef",
    }
)


def _materializer_refs_of(payload: SupportKeyPayload | dict[str, Any]) -> tuple[str, ...]:
    if isinstance(payload, SupportKeyPayload):
        return tuple(payload.materializerRefs)
    if isinstance(payload, dict):
        refs = payload.get("materializerRefs")
        if isinstance(refs, (list, tuple)):
            return tuple(str(v) for v in refs)
    return ()


def is_none_materializer_identity(payload: SupportKeyPayload | dict[str, Any]) -> bool:
    """Whether this identity uses the credentialless none@1 fast-path."""
    return _materializer_refs_of(payload) == ("none@1",)


def deployment_excluded_fields_for(
    payload: SupportKeyPayload | dict[str, Any],
) -> frozenset[str]:
    """Excluded fields for deployment matching of one identity.

    Auth-bearing identities stay exact (minus per-run model/capabilities).
    none@1 additionally ignores volatile build digests.
    """
    if is_none_materializer_identity(payload):
        return DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS | (
            NONE_DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS
        )
    return DEPLOYMENT_QUALIFICATION_EXCLUDED_FIELDS


def compute_deployment_qualification_key(
    payload: SupportKeyPayload | dict[str, Any],
) -> str:
    """Return the deployment-scoped projection of a support combination.

    Deployment qualification proves this deployment can run one exact harness,
    host, image, realizer, and credential class. It deliberately excludes
    per-run model/options and capability variance so ordinary default-profile
    workflows are admissible without manual requalification.

    For the credentialless none@1 fast-path it additionally ignores volatile
    build digests (server/host builds, harness impl, vendor runtime, agent
    source); those are covered by catalog/trust/preflight and otherwise force
    manual requalification on every upgrade. Auth-bearing identities stay
    exact.
    """

    if isinstance(payload, SupportKeyPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    excluded = deployment_excluded_fields_for(payload)
    projected = {
        key: value for key, value in data.items() if key not in excluded
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
