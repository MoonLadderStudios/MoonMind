"""Canonical, immutable MoonMind-owned Omnigent agent-profile documents."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_AUTHORITY = ("credential", "password", "token", "secret")


class UpstreamAgentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str | None = Field(None, alias="agentId")
    agent_name: str | None = Field(None, alias="agentName")
    agent_version: str | None = Field(None, alias="agentVersion")
    bundle_artifact_ref: str | None = Field(None, alias="bundleArtifactRef")
    bundle_digest: str | None = Field(None, alias="bundleDigest")

    @model_validator(mode="after")
    def require_stable_identity(self) -> "UpstreamAgentIdentity":
        upstream = bool(self.agent_id and self.agent_version)
        bundle = bool(self.bundle_artifact_ref and self.bundle_digest)
        if upstream == bundle:
            raise ValueError(
                "exactly one versioned upstream agent or immutable bundle is required"
            )
        if self.bundle_digest and not _DIGEST.fullmatch(self.bundle_digest):
            raise ValueError("bundleDigest must be a sha256 digest")
        return self


class ProviderCompatibility(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtimes: tuple[str, ...]
    sources: tuple[str, ...] = ()
    materialization: tuple[str, ...] = ()


class OmnigentAgentProfileDocument(BaseModel):
    """Normalized reusable product configuration; never contains secret bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(1, alias="schemaVersion")
    display_name: str = Field(alias="displayName", min_length=1, max_length=256)
    description: str = ""
    owner_ref: str = Field(alias="ownerRef")
    visibility: Literal["private", "project", "organization"] = "private"
    endpoint_ref: str = Field(alias="endpointRef")
    bridge_mode: Literal["embedded", "proxy"] = Field(alias="bridgeMode")
    upstream: UpstreamAgentIdentity
    harness: str
    required_capabilities: tuple[str, ...] = Field(
        default=(), alias="requiredCapabilities"
    )
    execution_profile_ref: str = Field(alias="executionProfileRef")
    allowed_launch_policy_refs: tuple[str, ...] = Field(
        alias="allowedLaunchPolicyRefs"
    )
    default_launch_policy_ref: str = Field(alias="defaultLaunchPolicyRef")
    provider_compatibility: ProviderCompatibility = Field(
        alias="providerCompatibility"
    )
    model_settings: dict[str, Any] = Field(default_factory=dict, alias="modelSettings")
    workspace_defaults: dict[str, Any] = Field(
        default_factory=dict, alias="workspaceDefaults"
    )
    skill_refs: tuple[str, ...] = Field(default=(), alias="skillRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    capture_defaults: dict[str, Any] = Field(
        default_factory=dict, alias="captureDefaults"
    )
    rag_defaults: dict[str, Any] = Field(default_factory=dict, alias="ragDefaults")
    continuation: dict[str, bool] = Field(default_factory=dict)
    default_publish_mode: str | None = Field(None, alias="defaultPublishMode")
    policy_ref: str = Field(alias="policyRef")

    @model_validator(mode="after")
    def validate_product_authority(self) -> "OmnigentAgentProfileDocument":
        if self.default_launch_policy_ref not in self.allowed_launch_policy_refs:
            raise ValueError("default launch policy must be allowed")
        payload = self.model_dump(by_alias=True, mode="json")

        def forbidden(value: object) -> bool:
            if isinstance(value, Mapping):
                return any(
                    any(marker in str(key).lower() for marker in _FORBIDDEN_AUTHORITY)
                    or forbidden(item)
                    for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(forbidden(item) for item in value)
            if isinstance(value, str):
                lowered = value.lower()
                return (
                    "docker.sock" in lowered
                    or lowered.startswith("/")
                    or lowered.startswith("file://")
                )
            return False

        if forbidden(payload):
            raise ValueError("profile contains credential or host-path authority")
        return self

    def normalized(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.normalized(), sort_keys=True, separators=(",", ":")
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()


class OmnigentAgentProfileVersion(BaseModel):
    """Immutable identity and lifecycle envelope for a normalized document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(alias="profileId")
    version: int = Field(ge=1)
    document: OmnigentAgentProfileDocument
    document_digest: str = Field(alias="documentDigest")
    state: Literal["draft", "active", "disabled", "deprecated"]
    parent_ref: str | None = Field(None, alias="parentRef")
    clone_of_ref: str | None = Field(None, alias="cloneOfRef")
    supersedes_ref: str | None = Field(None, alias="supersedesRef")
    created_by: str = Field(alias="createdBy")
    created_at: str = Field(alias="createdAt")
    lifecycle_audit: tuple[dict[str, Any], ...] = Field(
        default=(), alias="lifecycleAudit"
    )
    upstream_metadata_snapshot: dict[str, Any] = Field(
        default_factory=dict, alias="upstreamMetadataSnapshot"
    )
    validation_result: dict[str, Any] = Field(
        default_factory=dict, alias="validationResult"
    )
    rollout_metadata: dict[str, Any] = Field(
        default_factory=dict, alias="rolloutMetadata"
    )
    dependent_usage: tuple[dict[str, str], ...] = Field(
        default=(), alias="dependentUsage"
    )

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    @model_validator(mode="after")
    def validate_envelope(self) -> "OmnigentAgentProfileVersion":
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise ValueError("profileId is not a stable safe identifier")
        if self.document_digest != self.document.digest:
            raise ValueError("documentDigest does not match normalized document")
        return self

