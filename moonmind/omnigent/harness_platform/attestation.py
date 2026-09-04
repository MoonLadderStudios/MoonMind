"""Exact-host harness attestation (section 8).

Host Class says what image is expected to contain. Exact host attests what it
actually contains after creation, before runner/session.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

if TYPE_CHECKING:
    from moonmind.omnigent.harness_platform.runtime_packs import (
        RuntimePackDescriptor,
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
    def validate(self) -> RuntimeDependency:
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError("digest must be sha256")
        if not self.name.strip():
            raise ValueError("name required")
        if not self.version.strip():
            raise ValueError("version required")
        return self


class HostHarnessAttestation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-host-harness-attestation.v1", alias="schemaVersion"
    )
    hostId: str = Field(alias="hostId")
    hostClassRef: str = Field(alias="hostClassRef")
    hostImageRef: str = Field(alias="hostImageRef")
    omnigentVersion: str = Field(alias="omnigentVersion")
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    harnessId: str = Field(alias="harnessId")
    harnessImplementation: dict[str, Any] = Field(alias="harnessImplementation")
    runtimeDependencies: tuple[RuntimeDependency, ...] = Field(
        default_factory=tuple, alias="runtimeDependencies"
    )
    configured: bool
    capabilities: dict[str, Any]
    architecture: str | None = Field(default=None, alias="architecture")
    attestationGeneration: int | None = Field(
        default=None, alias="attestationGeneration"
    )
    observedAt: datetime = Field(alias="observedAt")
    attestationRef: str | None = Field(default=None, alias="attestationRef")

    @model_validator(mode="after")
    def validate_refs(self) -> HostHarnessAttestation:
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
    payload = attestation.model_dump(
        by_alias=True, mode="json", exclude={"attestationRef"}
    )
    # Normalize datetime
    if isinstance(payload.get("observedAt"), datetime):
        payload["observedAt"] = (
            payload["observedAt"].astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
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
    expectedHostId: str | None = None,
    max_age_seconds: int = 600,
    now: datetime | None = None,
    currentHostLeaseGeneration: int | None = None,
    attestationGeneration: int | None = None,
    expectedVendorRuntimes: list[dict[str, Any]] | None = None,
) -> None:
    # Use attestation's own generation if provided in object when caller didn't supply
    if attestationGeneration is None and attestation.attestationGeneration is not None:
        attestationGeneration = attestation.attestationGeneration
    now_ts = now or datetime.now(UTC)
    age = (now_ts - attestation.observedAt).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise HarnessPlatformError(
            f"attestation stale or future: age {age:.0f}s",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
    # Bind attestation to expected host identity (lease-selected host)
    if expectedHostId is not None and attestation.hostId != expectedHostId:
        raise HarnessPlatformError(
            f"attestation hostId mismatch: {attestation.hostId} != {expectedHostId}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
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
    # Vendor runtime: prefer explicit HostClass-declared runtimes if supplied, else fallback to implementation
    vendor_source = (
        expectedVendorRuntimes
        if expectedVendorRuntimes is not None
        else expectedImplementation.get("runtimeDependencies", [])
    )
    expected_runtimes = {
        d["name"] if isinstance(d, dict) else getattr(d, "name", ""): d
        for d in vendor_source
    }
    # Normalize expected dict values to dict
    normalized_expected = {}
    for name, dep in expected_runtimes.items():
        if isinstance(dep, dict):
            normalized_expected[name] = dep
        else:
            normalized_expected[name] = {
                "name": dep.name,
                "version": dep.version,
                "digest": dep.digest,
            }
    expected_runtimes = normalized_expected
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
        # A descriptor-driven runtime pack pins the vendor version identity;
        # only an explicitly recorded expectation carries a digest to compare.
        expected_digest = exp_dep.get("digest")
        if expected_digest and str(expected_digest) != actual.digest:
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
    # Fencing generation: if lease generation is known, attestation must provide matching generation
    if currentHostLeaseGeneration is not None:
        if attestationGeneration is None:
            raise HarnessPlatformError(
                "attestation missing generation for fenced host lease",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if attestationGeneration != currentHostLeaseGeneration:
            raise HarnessPlatformError(
                "attestation generation does not match host lease generation",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
    if (
        expectedArchitecture is not None
        and attestation.architecture is not None
        and attestation.architecture != expectedArchitecture
    ):
        raise HarnessPlatformError(
            f"architecture mismatch: {attestation.architecture} != {expectedArchitecture}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if not attestation.configured:
        raise HarnessPlatformError(
            "host not configured",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )


# ---- Runtime-pack-driven exact-host preflight (issues #3827, #3831) ----


def validate_runtime_pack_preflight(
    attestation: HostHarnessAttestation,
    *,
    expectedRuntimePackRef: str,
    expectedHostClassRef: str,
    expectedImageRef: str,
    expectedOmnigentBuildDigest: str,
    expectedImplementation: dict[str, Any],
    expectedHostId: str | None = None,
    requiredCapabilities: list[str] | None = None,
    expectedArchitecture: str | None = None,
    currentHostLeaseGeneration: int | None = None,
    attestationGeneration: int | None = None,
    max_age_seconds: int = 600,
    now: datetime | None = None,
    require_restricted_egress: bool = True,
    requiredSkillDeliveryRef: str | None = None,
) -> RuntimePackDescriptor:
    """Descriptor-driven exact-host preflight (issue #3827).

    The trusted runtime pack — not a harness-specific branch — declares:

    - which vendor runtime the exact host must report (name + version),
    - the supported vendor range (exclusive upper bound),
    - the readiness probe the caller executes inside the exact container.

    The launched container must have been selected by a Host Class that binds
    the same pack, so the attested vendor runtime identity is compared against
    the pack rather than against harness-specific hard-coded expectations.
    """

    from moonmind.omnigent.harness_platform.runtime_packs import (
        get_runtime_pack,
        is_vendor_version_supported,
    )

    pack = get_runtime_pack(expectedRuntimePackRef)
    if attestation.harnessId not in pack.harnessIds:
        raise HarnessPlatformError(
            f"runtime pack {pack.ref} does not own harness {attestation.harnessId}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    caps = list(requiredCapabilities or ["interrupt"])
    if require_restricted_egress:
        lower_caps = [str(cap).lower() for cap in caps]
        if (
            "restricted-egress" not in lower_caps
            and "restrictedegress" not in lower_caps
            and "restricted_egress" not in lower_caps
        ):
            caps.append("restricted-egress")
    # The pack is the vendor-runtime authority for this exact host.
    expectedVendorRuntimes: list[dict[str, Any]] = [
        {"name": pack.vendorRuntime.name, "version": pack.vendorRuntime.pinnedVersion}
    ]
    validate_exact_host_attestation(
        attestation=attestation,
        expectedHostClassRef=expectedHostClassRef,
        expectedImageRef=expectedImageRef,
        expectedOmnigentBuildDigest=expectedOmnigentBuildDigest,
        expectedHarnessId=attestation.harnessId,
        expectedImplementation=expectedImplementation,
        requiredCapabilities=caps,
        expectedArchitecture=expectedArchitecture,
        expectedHostId=expectedHostId,
        max_age_seconds=max_age_seconds,
        now=now,
        currentHostLeaseGeneration=currentHostLeaseGeneration,
        attestationGeneration=attestationGeneration,
        expectedVendorRuntimes=expectedVendorRuntimes,
    )
    vendor = next(
        (
            dep
            for dep in attestation.runtimeDependencies
            if dep.name == pack.vendorRuntime.name
        ),
        None,
    )
    if vendor is None:
        raise HarnessPlatformError(
            f"{pack.vendorRuntime.name} binary missing from exact host",
            code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
        )
    if not is_vendor_version_supported(pack, vendor.version):
        raise HarnessPlatformError(
            f"{pack.vendorRuntime.name} version {vendor.version} outside supported "
            f"range {pack.vendorRuntime.supportedRange} (exclusive upper)",
            code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
        )
    if requiredSkillDeliveryRef is not None:
        if not requiredSkillDeliveryRef.startswith("skill-delivery:sha256:"):
            raise HarnessPlatformError(
                f"requiredSkillDeliveryRef must be skill-delivery:sha256: digest "
                f"(got {requiredSkillDeliveryRef!r})",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
            )
        if not attestation.capabilities.get("mountedSkills"):
            raise HarnessPlatformError(
                "required skill delivery but host does not advertise mountedSkills",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
            )
        attested_ref = (
            attestation.capabilities.get("skillDeliveryRef")
            or attestation.capabilities.get("skill_delivery_ref")
            or attestation.capabilities.get("skillDelivery")
        )
        if attested_ref is not None:
            if str(attested_ref) != requiredSkillDeliveryRef:
                raise HarnessPlatformError(
                    "skill delivery ref mismatch",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
                )
    return pack
