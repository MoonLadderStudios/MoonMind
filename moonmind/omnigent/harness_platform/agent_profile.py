"""Omnigent Agent Profiles v2 - discriminated agent source (section 9).

Two variants:
- upstream: stock or pre-existing upstream agent
- bundle: imported/custom/MoonMind-generated bundle with artifact+import receipt+content digest

Compatibility decoder for v1 -> v2 is required for historical replay.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class UpstreamSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["upstream"] = "upstream"
    upstreamId: str = Field(alias="upstreamId")
    upstreamVersion: str = Field(alias="upstreamVersion")
    upstreamSnapshotDigest: str = Field(alias="upstreamSnapshotDigest")

    @model_validator(mode="after")
    def validate(self) -> "UpstreamSource":
        if not self.upstreamId.strip():
            raise ValueError("upstreamId required")
        if not self.upstreamVersion.strip():
            raise ValueError("upstreamVersion required")
        if not _DIGEST_RE.fullmatch(self.upstreamSnapshotDigest):
            raise ValueError("upstreamSnapshotDigest must be sha256")
        return self


class BundleSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bundle"] = "bundle"
    bundleArtifactRef: str = Field(alias="bundleArtifactRef")
    bundleDigest: str = Field(alias="bundleDigest")
    importReceiptRef: str = Field(alias="importReceiptRef")
    importedAgentId: str = Field(alias="importedAgentId")
    importedAgentVersion: str = Field(alias="importedAgentVersion")
    importedContentDigest: str = Field(alias="importedContentDigest")

    @model_validator(mode="after")
    def validate(self) -> "BundleSource":
        for field in ("bundleArtifactRef", "importReceiptRef", "importedAgentId", "importedAgentVersion"):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} required")
        if not _DIGEST_RE.fullmatch(self.bundleDigest):
            raise ValueError("bundleDigest must be sha256")
        if not _DIGEST_RE.fullmatch(self.importedContentDigest):
            raise ValueError("importedContentDigest must be sha256")
        if not self.importReceiptRef.startswith("omnigent-agent-import:"):
            raise ValueError("importReceiptRef must start with omnigent-agent-import:")
        return self


class HarnessSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    catalogRef: str = Field(alias="catalogRef")
    implementationRef: str = Field(alias="implementationRef")

    @model_validator(mode="after")
    def validate(self) -> "HarnessSelection":
        if not _SAFE_ID_RE.fullmatch(self.id):
            raise ValueError("invalid harness id")
        if not self.catalogRef.startswith("omnigent-harness-catalog:sha256:"):
            raise ValueError("catalogRef invalid")
        if not self.implementationRef.startswith("omnigent-harness-implementation:sha256:"):
            raise ValueError("implementationRef invalid")
        return self


class CapabilityRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    required: tuple[str, ...] = ()
    preferred: tuple[str, ...] = ()


class AgentProfileRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harness: CapabilityRequirements = Field(default_factory=CapabilityRequirements)
    moonmind: CapabilityRequirements = Field(default_factory=CapabilityRequirements)
    host: CapabilityRequirements = Field(default_factory=CapabilityRequirements)


class CredentialSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    optional: bool = False
    acceptedAuthModels: tuple[str, ...] = Field(default_factory=tuple, alias="acceptedAuthModels")
    acceptedProviderIds: tuple[str, ...] = Field(default_factory=tuple, alias="acceptedProviderIds")


class OmnigentAgentProfileV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-agent-profile.v2", alias="schemaVersion")
    endpointRef: str = Field(alias="endpointRef")
    source: UpstreamSource | BundleSource = Field(discriminator="kind")
    harness: HarnessSelection
    requirements: AgentProfileRequirements = Field(default_factory=AgentProfileRequirements)
    credentialSlots: tuple[CredentialSlot, ...] = Field(default_factory=tuple, alias="credentialSlots")
    model: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    capture: dict[str, Any] = Field(default_factory=dict)
    continuations: dict[str, Any] = Field(default_factory=dict)
    publish: dict[str, Any] = Field(default_factory=dict)
    allowedLaunchPolicyRefs: tuple[str, ...] = Field(default_factory=tuple, alias="allowedLaunchPolicyRefs")

    @model_validator(mode="after")
    def validate_top(self) -> "OmnigentAgentProfileV2":
        if not self.endpointRef.strip():
            raise ValueError("endpointRef required")
        return self

    def snapshot_ref(self) -> str:
        canonical = json.dumps(self.model_dump(by_alias=True, mode="json"), sort_keys=True, separators=(",", ":"))
        return "omnigent-agent-profile:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    def snapshot_digest(self) -> str:
        canonical = json.dumps(self.model_dump(by_alias=True, mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


# Compatibility decoder: v1 -> v2 inputs
def decode_v1_profile_to_v2_inputs(v1: dict[str, Any]) -> dict[str, Any]:
    """Compile legacy v1 profile document into generic v2 planning inputs.

    v1 shape is less strict; we map known fields to v2's discriminated source,
    harness, providerRequirements -> credentialSlots, etc.
    Historical and in-flight authority depends on this decoder.
    """
    endpointRef = str(v1.get("endpointRef") or v1.get("endpoint") or "default")
    # Source mapping
    source: dict[str, Any]
    if "source" in v1 and isinstance(v1["source"], dict) and "kind" in v1["source"]:
        source = v1["source"]
    elif "bundleArtifactRef" in v1 or "bundle" in v1:
        bundle = v1.get("bundle") if isinstance(v1.get("bundle"), dict) else v1
        source = {
            "kind": "bundle",
            "bundleArtifactRef": bundle.get("bundleArtifactRef") or "artifact:placeholder",
            "bundleDigest": bundle.get("bundleDigest") or "sha256:" + "a" * 64,
            "importReceiptRef": bundle.get("importReceiptRef") or "omnigent-agent-import:placeholder",
            "importedAgentId": bundle.get("importedAgentId") or "imported-agent",
            "importedAgentVersion": bundle.get("importedAgentVersion") or "0.0.0",
            "importedContentDigest": bundle.get("importedContentDigest") or "sha256:" + "b" * 64,
        }
    else:
        source = {
            "kind": "upstream",
            "upstreamId": v1.get("upstreamId") or v1.get("agentName") or "codex-native-ui",
            "upstreamVersion": v1.get("upstreamVersion") or "1.0.0",
            "upstreamSnapshotDigest": v1.get("upstreamSnapshotDigest") or "sha256:" + "c" * 64,
        }
    harness = v1.get("harness") or {}
    if isinstance(harness, str):
        harness = {"id": harness, "catalogRef": "omnigent-harness-catalog:sha256:" + "d" * 64, "implementationRef": "omnigent-harness-implementation:sha256:" + "e" * 64}
    else:
        harness = {
            "id": harness.get("id") or harness.get("harnessId") or "codex-native",
            "catalogRef": harness.get("catalogRef") or "omnigent-harness-catalog:sha256:" + "d" * 64,
            "implementationRef": harness.get("implementationRef") or "omnigent-harness-implementation:sha256:" + "e" * 64,
        }
    # Credential slots from providerRequirements
    cred_slots = []
    provider_reqs = v1.get("providerRequirements") or v1.get("credentialSlots") or []
    if isinstance(provider_reqs, dict):
        provider_reqs = [provider_reqs]
    for slot in provider_reqs:
        if isinstance(slot, dict):
            cred_slots.append({
                "id": slot.get("id") or slot.get("slotId") or "primary-model",
                "optional": bool(slot.get("optional", False)),
                "acceptedAuthModels": slot.get("acceptedAuthModels") or ["own-auth"],
                "acceptedProviderIds": slot.get("acceptedProviderIds") or slot.get("acceptedProviders") or ["openai"],
            })
        elif isinstance(slot, str):
            cred_slots.append({"id": slot, "optional": False, "acceptedAuthModels": ["own-auth"], "acceptedProviderIds": ["openai"]})

    return {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": endpointRef,
        "source": source,
        "harness": harness,
        "requirements": v1.get("requirements") or {"harness": {"required": [], "preferred": []}, "moonmind": {"required": []}, "host": {"required": []}},
        "credentialSlots": cred_slots,
        "model": v1.get("model") or {},
        "workspace": v1.get("workspace") or {},
        "skills": v1.get("skills") or [],
        "tools": v1.get("tools") or [],
        "capture": v1.get("capture") or {},
        "continuations": v1.get("continuations") or {},
        "publish": v1.get("publish") or {},
        "allowedLaunchPolicyRefs": v1.get("allowedLaunchPolicyRefs") or [],
    }


def validate_agent_profile(profile: dict[str, Any] | OmnigentAgentProfileV2) -> OmnigentAgentProfileV2:
    if isinstance(profile, OmnigentAgentProfileV2):
        return profile
    # Try v2 first
    try:
        return OmnigentAgentProfileV2.model_validate(profile)
    except Exception:
        # Try v1 decode path
        decoded = decode_v1_profile_to_v2_inputs(dict(profile))
        return OmnigentAgentProfileV2.model_validate(decoded)
