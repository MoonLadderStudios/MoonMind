"""Immutable harness catalog snapshot and trust classification.

Sections 7, 4.3, 7.2-7.4 of OmnigentHarnessPlatformDesign.
- MoonMind projects the Omnigent endpoint's /v1/harnesses, /v1/agents, /v1/hosts
- Catalog snapshot is immutable, bounded, digest-addressed
- Trust is bound to implementation identity (package/version/digest/entrypoint)
- Discovery != trusted != installed != class-admissible != exact-host-attested
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+.*$")
_CATALOG_REF_RE = re.compile(r"^omnigent-harness-catalog:sha256:[0-9a-f]{64}$")
_IMPL_REF_RE = re.compile(r"^omnigent-harness-implementation:sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALLOWED_FRESHNESS_SECONDS = 3600  # 1 hour default


class TrustState(StrEnum):
    core_trusted = "core_trusted"
    plugin_approved = "plugin_approved"
    quarantined = "quarantined"
    blocked = "blocked"


class HarnessImplementationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sourceKind: Literal["core", "plugin"] = Field(alias="sourceKind")
    package: str
    version: str
    digest: str
    pluginEntryPoint: str | None = Field(default=None, alias="pluginEntryPoint")

    @model_validator(mode="after")
    def validate_digest(self) -> "HarnessImplementationIdentity":
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError("digest must be sha256: hex")
        if not _SEMVER_RE.fullmatch(self.version):
            raise ValueError("version must be semver")
        if self.sourceKind == "core" and self.pluginEntryPoint is not None:
            raise ValueError("core harness must not have pluginEntryPoint")
        if self.sourceKind == "plugin" and not self.pluginEntryPoint:
            raise ValueError("plugin harness requires pluginEntryPoint")
        if not re.fullmatch(r"^[a-z0-9][a-z0-9._/-]*$", self.package):
            raise ValueError("invalid package name")
        return self

    def implementation_ref(self) -> str:
        canonical = json.dumps(self.model_dump(by_alias=True, mode="json"), sort_keys=True, separators=(",", ":"))
        return "omnigent-harness-implementation:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class HarnessCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    integrationMode: str | None = Field(default=None, alias="integrationMode")
    authModel: str | None = Field(default=None, alias="authModel")
    resume: str | None = None
    forkHistory: str | None = Field(default=None, alias="forkHistory")
    modelFamily: str | None = Field(default=None, alias="modelFamily")
    effortFamily: str | None = Field(default=None, alias="effortFamily")
    elicitation: str | None = None
    interrupt: bool | None = None
    streaming: bool | None = None
    subagents: bool | None = None
    steering: str | None = None
    liveQueue: str | None = Field(default=None, alias="liveQueue")
    images: str | None = None
    compaction: str | None = None


class HarnessRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    aliases: tuple[str, ...] = ()
    label: str
    implementation: HarnessImplementationIdentity
    runtimeRequirements: dict[str, Any] = Field(default_factory=dict, alias="runtimeRequirements")
    capabilities: HarnessCapabilities = Field(default_factory=HarnessCapabilities)
    setupSteps: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="setupSteps")

    @model_validator(mode="after")
    def validate_id(self) -> "HarnessRecord":
        if not _SAFE_ID_RE.fullmatch(self.id):
            raise ValueError(f"invalid harness id: {self.id}")
        for alias in self.aliases:
            if not _SAFE_ID_RE.fullmatch(alias):
                raise ValueError(f"invalid alias: {alias}")
        return self


class HarnessCatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-harness-catalog.v1", alias="schemaVersion")
    endpointRef: str = Field(alias="endpointRef")
    omnigentVersion: str = Field(alias="omnigentVersion")
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    observedAt: datetime = Field(alias="observedAt")
    sourceDigest: str = Field(alias="sourceDigest")
    catalogRef: str = Field(alias="catalogRef")
    pluginLoadErrors: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="pluginLoadErrors")
    harnesses: tuple[HarnessRecord, ...] = ()

    @model_validator(mode="after")
    def validate_refs(self) -> "HarnessCatalogSnapshot":
        if not _DIGEST_RE.fullmatch(self.omnigentBuildDigest):
            raise ValueError("omnigentBuildDigest must be sha256")
        if not _DIGEST_RE.fullmatch(self.sourceDigest):
            raise ValueError("sourceDigest must be sha256")
        if not _CATALOG_REF_RE.fullmatch(self.catalogRef):
            raise ValueError("catalogRef must be omnigent-harness-catalog:sha256:")
        # Verify digest matches canonical payload
        expected = compute_catalog_ref(self)
        if expected != self.catalogRef:
            raise ValueError(f"catalogRef digest mismatch: expected {expected}")
        if not _SEMVER_RE.fullmatch(self.omnigentVersion):
            raise ValueError("omnigentVersion must be semver")
        return self


def _canonical_catalog_bytes(snapshot: HarnessCatalogSnapshot | dict[str, Any]) -> bytes:
    if isinstance(snapshot, HarnessCatalogSnapshot):
        payload = snapshot.model_dump(by_alias=True, mode="json", exclude={"catalogRef"})
        # Exclude catalogRef from digest computation
        payload = {k: v for k, v in payload.items() if k != "catalogRef"}
        # Normalize datetime to isoformat UTC
        if "observedAt" in payload and isinstance(payload["observedAt"], str):
            pass
    else:
        payload = {k: v for k, v in dict(snapshot).items() if k != "catalogRef"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_catalog_ref(snapshot: HarnessCatalogSnapshot | dict[str, Any]) -> str:
    if isinstance(snapshot, HarnessCatalogSnapshot):
        payload = snapshot.model_dump(by_alias=True, mode="json", exclude={"catalogRef"})
    else:
        payload = {k: v for k, v in dict(snapshot).items() if k != "catalogRef"}
    # Normalize observedAt for consistent hashing - both datetime and string forms to Z
    if "observedAt" in payload:
        val = payload["observedAt"]
        if isinstance(val, datetime):
            payload["observedAt"] = val.astimezone(UTC).isoformat().replace("+00:00", "Z")
        elif isinstance(val, str):
            # Pydantic json mode yields +00:00, creation yields Z - normalize both to Z
            payload["observedAt"] = val.replace("+00:00", "Z")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "omnigent-harness-catalog:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def create_catalog_snapshot(
    *,
    endpointRef: str,
    omnigentVersion: str,
    omnigentBuildDigest: str,
    sourceDigest: str,
    harnesses: list[dict[str, Any]],
    observedAt: datetime | None = None,
    pluginLoadErrors: list[dict[str, Any]] | None = None,
) -> HarnessCatalogSnapshot:
    observed = observedAt or datetime.now(UTC)
    # Normalize harnesses through HarnessRecord to match validator canonicalization
    normalized_harnesses = [
        HarnessRecord.model_validate(h).model_dump(by_alias=True, mode="json") for h in harnesses
    ]
    # Build without catalogRef first to compute it using same canonical logic as validator
    raw: dict[str, Any] = {
        "schemaVersion": "moonmind.omnigent-harness-catalog.v1",
        "endpointRef": endpointRef,
        "omnigentVersion": omnigentVersion,
        "omnigentBuildDigest": omnigentBuildDigest,
        "observedAt": observed,  # keep as datetime for compute_catalog_ref normalization
        "sourceDigest": sourceDigest,
        "pluginLoadErrors": pluginLoadErrors or [],
        "harnesses": normalized_harnesses,
    }
    # Compute ref via shared helper (which normalizes observedAt and uses json)
    raw["catalogRef"] = compute_catalog_ref(raw)
    return HarnessCatalogSnapshot.model_validate(raw)


def assert_catalog_fresh(
    snapshot: HarnessCatalogSnapshot,
    *,
    now: datetime | None = None,
    max_age_seconds: int = _ALLOWED_FRESHNESS_SECONDS,
    allow_stale_offline: bool = False,
) -> None:
    if allow_stale_offline:
        return
    current = now or datetime.now(UTC)
    age = (current - snapshot.observedAt).total_seconds()
    if age < 0:
        raise HarnessPlatformError(
            "catalog snapshot observedAt is in the future",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
        )
    if age > max_age_seconds:
        raise HarnessPlatformError(
            f"catalog snapshot is stale: {age:.0f}s > {max_age_seconds}s",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
        )


def assert_catalog_refresh_attests(
    *,
    authority: HarnessCatalogSnapshot,
    observation: HarnessCatalogSnapshot,
    harness_id: str,
    implementation_ref: str,
) -> None:
    """Prove a fresh observation still matches immutable profile authority.

    Agent Profile versions remain bound to their original catalog snapshot.
    A later synchronization supplies only freshness/liveness evidence and may
    not silently replace the selected build or harness implementation.
    """

    assert_catalog_fresh(observation)
    if authority.endpointRef != observation.endpointRef:
        raise HarnessPlatformError(
            "fresh catalog observation is for a different endpoint",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
        )
    if (
        authority.omnigentVersion != observation.omnigentVersion
        or authority.omnigentBuildDigest != observation.omnigentBuildDigest
    ):
        raise HarnessPlatformError(
            "fresh catalog observation reports a different Omnigent build",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    authority_harness = next(
        (item for item in authority.harnesses if item.id == harness_id), None
    )
    observation_harness = next(
        (item for item in observation.harnesses if item.id == harness_id), None
    )
    if authority_harness is None or observation_harness is None:
        raise HarnessPlatformError(
            f"harness {harness_id} is absent from catalog authority",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    if (
        authority_harness.implementation.implementation_ref()
        != implementation_ref
        or observation_harness.implementation.implementation_ref()
        != implementation_ref
    ):
        raise HarnessPlatformError(
            "fresh catalog observation reports a different harness implementation",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )


class HarnessTrustRecord(BaseModel):
    """Binds trust state to exact implementation identity, not just harness id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    harnessId: str = Field(alias="harnessId")
    implementation: HarnessImplementationIdentity
    implementationRef: str = Field(alias="implementationRef")
    trustState: TrustState = Field(alias="trustState")
    decidedAt: datetime = Field(alias="decidedAt")
    decidedBy: str = Field(alias="decidedBy")

    @model_validator(mode="after")
    def validate_ref(self) -> "HarnessTrustRecord":
        if not _SAFE_ID_RE.fullmatch(self.harnessId):
            raise ValueError("invalid harnessId")
        if not _IMPL_REF_RE.fullmatch(self.implementationRef):
            raise ValueError("implementationRef must be sha256")
        expected = self.implementation.implementation_ref()
        if expected != self.implementationRef:
            raise ValueError(f"implementationRef mismatch: expected {expected}")
        return self


def classify_harness_trust(
    *,
    harnessId: str,
    implementation: HarnessImplementationIdentity,
    trustState: TrustState,
    decidedBy: str = "operator",
    decidedAt: datetime | None = None,
) -> HarnessTrustRecord:
    return HarnessTrustRecord.model_validate(
        {
            "harnessId": harnessId,
            "implementation": implementation.model_dump(by_alias=True, mode="json"),
            "implementationRef": implementation.implementation_ref(),
            "trustState": trustState,
            "decidedAt": decidedAt or datetime.now(UTC),
            "decidedBy": decidedBy,
        }
    )


def is_launchable_trust(state: TrustState) -> bool:
    return state in {TrustState.core_trusted, TrustState.plugin_approved}


def is_supported_trust(state: TrustState) -> bool:
    # Quarantined and blocked are not launchable; launchable trusts may still be experimental until conformance passes
    return is_launchable_trust(state)


TRUST_PRIORITY = {
    TrustState.core_trusted: 3,
    TrustState.plugin_approved: 2,
    TrustState.quarantined: 1,
    TrustState.blocked: 0,
}
