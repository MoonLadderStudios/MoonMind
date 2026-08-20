"""Exact-host harness attestation (section 8).

Host Class says what image is expected to contain. Exact host attests what it
actually contains after creation, before runner/session.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOSTCLASS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*@\d+$")
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class RuntimeDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    digest: str

    @model_validator(mode="after")
    def validate(self) -> "RuntimeDependency":
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError("digest must be sha256")
        if not self.name.strip():
            raise ValueError("name required")
        if not self.version.strip():
            raise ValueError("version required")
        return self


class HostHarnessAttestation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-host-harness-attestation.v1", alias="schemaVersion")
    hostId: str = Field(alias="hostId")
    hostClassRef: str = Field(alias="hostClassRef")
    hostImageRef: str = Field(alias="hostImageRef")
    omnigentVersion: str = Field(alias="omnigentVersion")
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    harnessId: str = Field(alias="harnessId")
    harnessImplementation: dict[str, Any] = Field(alias="harnessImplementation")
    runtimeDependencies: tuple[RuntimeDependency, ...] = Field(default_factory=tuple, alias="runtimeDependencies")
    configured: bool
    capabilities: dict[str, Any]
    observedAt: datetime = Field(alias="observedAt")
    attestationRef: str | None = Field(default=None, alias="attestationRef")

    @model_validator(mode="after")
    def validate_refs(self) -> "HostHarnessAttestation":
        if not self.hostId.strip():
            raise ValueError("hostId required")
        if not _HOSTCLASS_RE.fullmatch(self.hostClassRef):
            raise ValueError("hostClassRef must be name@version")
        if not _IMAGE_RE.fullmatch(self.hostImageRef):
            raise ValueError("hostImageRef must be digest-pinned")
        if not _DIGEST_RE.fullmatch(self.omnigentBuildDigest):
            raise ValueError("omnigentBuildDigest must be sha256")
        impl = self.harnessImplementation
        for key in ("package", "version", "digest"):
            if not str(impl.get(key) or "").strip():
                raise ValueError(f"harnessImplementation.{key} required")
        if not _DIGEST_RE.fullmatch(str(impl.get("digest"))):
            raise ValueError("harnessImplementation.digest must be sha256")
        return self


def compute_attestation_ref(attestation: HostHarnessAttestation) -> str:
    payload = attestation.model_dump(by_alias=True, mode="json", exclude={"attestationRef"})
    # Normalize datetime
    if isinstance(payload.get("observedAt"), datetime):
        payload["observedAt"] = payload["observedAt"].astimezone(UTC).isoformat().replace("+00:00", "Z")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "artifact:" + hashlib.sha256(canonical.encode()).hexdigest()


def validate_exact_host_attestation(
    attestation: HostHarnessAttestation,
    *,
    expectedHostClassRef: str,
    expectedImageRef: str,
    expectedOmnigentBuildDigest: str,
    expectedHarnessId: str,
    expectedImplementation: dict[str, Any],
    requiredCapabilities: list[str],
    expectedArchitecture: str | None = None,
    max_age_seconds: int = 600,
    now: datetime | None = None,
    currentHostLeaseGeneration: int | None = None,
    attestationGeneration: int | None = None,
) -> None:
    now_ts = now or datetime.now(UTC)
    age = (now_ts - attestation.observedAt).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise HarnessPlatformError(
            f"attestation stale or future: age {age:.0f}s",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
    if attestation.hostClassRef != expectedHostClassRef:
        raise HarnessPlatformError(
            f"hostClassRef mismatch: {attestation.hostClassRef} != {expectedHostClassRef}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if attestation.hostImageRef != expectedImageRef:
        raise HarnessPlatformError(
            f"host image mismatch: {attestation.hostImageRef} != {expectedImageRef}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if attestation.omnigentBuildDigest != expectedOmnigentBuildDigest:
        raise HarnessPlatformError(
            "omnigent build digest mismatch",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if attestation.harnessId != expectedHarnessId:
        raise HarnessPlatformError(
            f"harness id mismatch: {attestation.harnessId} != {expectedHarnessId}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    # Implementation package/version/digest/entrypoint must match trusted catalog record
    for key in ("package", "version", "digest", "pluginEntryPoint"):
        exp_val = expectedImplementation.get(key)
        act_val = attestation.harnessImplementation.get(key)
        # Normalize None vs missing
        if exp_val != act_val:
            raise HarnessPlatformError(
                f"harness implementation {key} mismatch: {act_val} != {exp_val}",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
    # Vendor runtime
    expected_runtimes = {d["name"]: d for d in expectedImplementation.get("runtimeDependencies", [])}
    actual_runtimes = {d.name: d for d in attestation.runtimeDependencies}
    for name, exp_dep in expected_runtimes.items():
        actual = actual_runtimes.get(name)
        if actual is None:
            raise HarnessPlatformError(
                f"required vendor runtime {name} missing",
                code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            )
        if str(exp_dep.get("version")) != actual.version:
            raise HarnessPlatformError(
                f"vendor runtime {name} version mismatch",
                code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            )
        if str(exp_dep.get("digest")) != actual.digest:
            raise HarnessPlatformError(
                f"vendor runtime {name} digest mismatch",
                code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
            )
    # Required capabilities must be positively reported
    for cap in requiredCapabilities:
        if attestation.capabilities.get(cap) is not True:
            raise HarnessPlatformError(
                f"required exact-host capability {cap} missing or not true",
                code=HarnessPlatformFailure.OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH,
            )
    # Fencing generation
    if currentHostLeaseGeneration is not None and attestationGeneration is not None:
        if attestationGeneration != currentHostLeaseGeneration:
            raise HarnessPlatformError(
                "attestation generation does not match host lease generation",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
    if not attestation.configured:
        raise HarnessPlatformError(
            "host not configured",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
