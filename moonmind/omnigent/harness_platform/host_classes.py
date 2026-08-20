"""Host Classes and Launch Policies (sections 13, 14).

Host Class is immutable class-level admission evidence. Exact host must still
pass attestation. Launch policy governs host behavior, not provider identity.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# Dedicated OpenCode host image env (issue §11)
OMNIGENT_OPENCODE_HOST_IMAGE_ENV = "OMNIGENT_OPENCODE_HOST_IMAGE_REF"
OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT = "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "c" * 64
OPENCODE_PINNED_VERSION = "1.18.11"
OPENCODE_SUPPORTED_RANGE = ">=1.17.7,<1.19.0"


def get_opencode_host_image_ref() -> str:
    """Return the digest-pinned OpenCode host image ref from deployment config.

    Fails closed when only a mutable tag is configured for production launch
    authority (issue §11). For local dev without env, returns the placeholder
    digest used by hermetic tests; production must supply a real GHCR digest.
    """
    raw = os.getenv(OMNIGENT_OPENCODE_HOST_IMAGE_ENV, "").strip()
    if not raw:
        return OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT
    if not _IMAGE_RE.fullmatch(raw):
        raise HarnessPlatformError(
            f"{OMNIGENT_OPENCODE_HOST_IMAGE_ENV} must be digest-pinned (got {raw!r})",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if raw.endswith("0" * 64):
        raise HarnessPlatformError(
            f"{OMNIGENT_OPENCODE_HOST_IMAGE_ENV} digest must not be placeholder",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    return raw


def get_opencode_host_class() -> HostClass:
    """Convenience for the dedicated OpenCode host class (omnigent-opencode@1)."""
    # Defined early for import convenience; actual lookup after registry init
    from typing import TYPE_CHECKING as _TC  # noqa: F401

    # Defer to get_host_class after registry populated
    return get_host_class("omnigent-opencode@1")


class HostClassHarnessEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harnessId: str = Field(alias="harnessId")
    implementationRef: str = Field(alias="implementationRef")
    runtimeDependencies: tuple[dict[str, Any], ...] = Field(default_factory=tuple, alias="runtimeDependencies")


class HostClass(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-host-class.v1", alias="schemaVersion")
    hostClassId: str = Field(alias="hostClassId")
    version: int = Field(ge=1)
    imageRef: str = Field(alias="imageRef")
    omnigentVersion: str = Field(alias="omnigentVersion")
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    architectures: tuple[str, ...]
    declaredHarnessImplementations: tuple[HostClassHarnessEntry, ...] = Field(alias="declaredHarnessImplementations")
    integrationModes: tuple[str, ...] = Field(alias="integrationModes")
    materializerRefs: tuple[str, ...] = Field(alias="materializerRefs")
    features: dict[str, bool]
    runtime: dict[str, Any]

    @model_validator(mode="after")
    def validate_top(self) -> "HostClass":
        if not _SAFE_ID_RE.fullmatch(self.hostClassId):
            raise ValueError("invalid hostClassId")
        if not _IMAGE_RE.fullmatch(self.imageRef):
            raise ValueError("imageRef must be digest-pinned")
        if not _DIGEST_RE.fullmatch(self.omnigentBuildDigest):
            raise ValueError("omnigentBuildDigest must be sha256")
        if not self.architectures:
            raise ValueError("architectures required")
        for entry in self.declaredHarnessImplementations:
            if not entry.implementationRef.startswith("omnigent-harness-implementation:sha256:"):
                raise ValueError("implementationRef invalid")
        if not self.integrationModes:
            raise ValueError("integrationModes required")
        # Host classes may omit some features; admission via policy will enforce required ones.
        return self

    @property
    def ref(self) -> str:
        return f"{self.hostClassId}@{self.version}"

    def declares_harness(self, harness_id: str, implementation_ref: str) -> bool:
        return any(
            e.harnessId == harness_id and e.implementationRef == implementation_ref
            for e in self.declaredHarnessImplementations
        )

    def supports_materializer(self, materializer_ref: str) -> bool:
        return materializer_ref in self.materializerRefs


HOST_CLASSES: dict[str, HostClass] = {}


def register_host_class(data: dict[str, Any]) -> HostClass:
    hc = HostClass.model_validate(data)
    existing = HOST_CLASSES.get(hc.ref)
    if existing is not None and existing != hc:
        raise ValueError(f"Host Class {hc.ref} already registered with different definition; new version required")
    HOST_CLASSES[hc.ref] = hc
    return hc


# Bootstrap some built-ins based on design sections 13.3, 13.4
# Compute implementation refs deterministically from harness implementation identities
# to align with catalog and planner expectations.
def _impl_ref(package: str, version: str, digest: str, kind: str = "core", entry: str | None = None) -> str:
    import hashlib, json
    payload = {
        "sourceKind": kind,
        "package": package,
        "version": version,
        "digest": digest,
        "pluginEntryPoint": entry,
    }
    # Use same canonical as HarnessImplementationIdentity
    from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
    return HarnessImplementationIdentity.model_validate(payload).implementation_ref()

register_host_class(
    {
        "schemaVersion": "moonmind.omnigent-host-class.v1",
        "hostClassId": "omnigent-native-standard",
        "version": 3,
        "imageRef": "ghcr.io/example/omnigent-host@sha256:" + "a" * 64,
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "b" * 64,
        "architectures": ["linux/amd64"],
        "declaredHarnessImplementations": [
            {
                "harnessId": "opencode-native",
                "implementationRef": _impl_ref("omnigent", "1.0.0", "sha256:" + "a" * 64),
                "runtimeDependencies": [{"name": "opencode", "version": "1.18.11", "digest": "sha256:" + "d" * 64}],
            },
            {
                "harnessId": "codex-native",
                "implementationRef": _impl_ref("omnigent", "1.0.0", "sha256:" + "e" * 64),
                "runtimeDependencies": [],
            },
        ],
        "integrationModes": ["native-tui", "native-server", "cli-subprocess", "sdk-in-process"],
        "materializerRefs": ["codex-oauth-home@1", "opencode-auth-json@1", "omnigent-provider-config@1"],
        "features": {
            "git": True,
            "tmux": True,
            "bubblewrap": True,
            "workspaceBind": True,
            "readOnlyRoot": True,
            "restrictedEgress": True,
            "mountedSkills": True,
            "mountedTools": True,
        },
        "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
    }
)

# Dedicated harness-specific OpenCode host (issue §1, §6)
# This is the first explicit harness-specific Host Class realization.
# It contains only opencode-native, not Codex or other harnesses.
register_host_class(
    {
        "schemaVersion": "moonmind.omnigent-host-class.v1",
        "hostClassId": "omnigent-opencode",
        "version": 1,
        "imageRef": get_opencode_host_image_ref(),
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "b" * 64,
        "architectures": ["linux/amd64", "linux/arm64"],
        "declaredHarnessImplementations": [
            {
                "harnessId": "opencode-native",
                "implementationRef": _impl_ref("omnigent", "1.0.0", "sha256:" + "a" * 64),
                # Exact OpenCode runtime digest pinned to image (issue §6)
                "runtimeDependencies": [{"name": "opencode", "version": OPENCODE_PINNED_VERSION, "digest": "sha256:" + "d" * 64}],
            }
        ],
        "integrationModes": ["native-server"],
        "materializerRefs": ["opencode-auth-json@1"],
        "features": {
            "git": True,
            "tmux": True,
            "bubblewrap": True,
            "workspaceBind": True,
            "readOnlyRoot": True,
            "restrictedEgress": True,
            "mountedSkills": True,
            "mountedTools": True,
        },
        "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
    }
)

register_host_class(
    {
        "schemaVersion": "moonmind.omnigent-host-class.v1",
        "hostClassId": "omnigent-codex-current",
        "version": 1,
        "imageRef": "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "b" * 64,
        "architectures": ["linux/amd64"],
        "declaredHarnessImplementations": [
            {
                "harnessId": "codex-native",
                "implementationRef": _impl_ref("omnigent", "1.0.0", "sha256:" + "e" * 64),
                "runtimeDependencies": [],
            }
        ],
        "integrationModes": ["native-server"],
        "materializerRefs": ["codex-oauth-home@1"],
        "features": {
            "git": True,
            "tmux": True,
            "bubblewrap": True,
            "workspaceBind": True,
            "readOnlyRoot": True,
            "restrictedEgress": True,
            "mountedSkills": True,
            "mountedTools": True,
        },
        "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
    }
)


def get_host_class(ref: str) -> HostClass:
    if ref not in HOST_CLASSES:
        raise HarnessPlatformError(
            f"host class {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    return HOST_CLASSES[ref]


# Launch Policies section 14
class LaunchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-launch-policy.v2", alias="schemaVersion")
    policyId: str = Field(alias="policyId")
    version: int = Field(ge=1)
    hostMode: Literal["on-demand", "static-connected", "on_demand_docker", "static_compose"] = Field(alias="hostMode")
    hostClassSelector: dict[str, Any] = Field(alias="hostClassSelector")
    isolation: dict[str, Any]
    limits: dict[str, int]
    network: dict[str, Any]
    capture: dict[str, Any]
    cleanup: dict[str, Any]
    controlCapabilities: tuple[str, ...] = Field(alias="controlCapabilities")

    @model_validator(mode="after")
    def validate_top(self) -> "LaunchPolicy":
        if not _SAFE_ID_RE.fullmatch(self.policyId):
            raise ValueError("invalid policyId")
        required_limits = {"cpuMillis", "memoryMiB", "processes", "timeoutSeconds", "temporaryStorageMiB"}
        if set(self.limits.keys()) != required_limits:
            raise ValueError(f"limits must contain {required_limits}")
        if any(not isinstance(v, int) or v <= 0 for v in self.limits.values()):
            raise ValueError("limits must be positive ints")
        # Normalize hostMode aliases for new v2 vs legacy
        return self

    @property
    def ref(self) -> str:
        return f"{self.policyId}@{self.version}"

    def allows_host_class(self, host_class: HostClass) -> bool:
        required_features = self.hostClassSelector.get("requiredFeatures", [])
        for feat in required_features:
            if not host_class.features.get(feat):
                return False
        return True

    def allows_integration_mode(self, mode: str, host_class: HostClass) -> bool:
        return mode in host_class.integrationModes


LAUNCH_POLICIES: dict[str, LaunchPolicy] = {}


def register_launch_policy(data: dict[str, Any]) -> LaunchPolicy:
    lp = LaunchPolicy.model_validate(data)
    existing = LAUNCH_POLICIES.get(lp.ref)
    if existing is not None and existing != lp:
        raise ValueError(f"Launch Policy {lp.ref} already registered with different definition; new version required")
    LAUNCH_POLICIES[lp.ref] = lp
    return lp


register_launch_policy(
    {
        "schemaVersion": "moonmind.omnigent-launch-policy.v2",
        "policyId": "omnigent-on-demand",
        "version": 1,
        "hostMode": "on-demand",
        "hostClassSelector": {"requiredFeatures": ["readOnlyRoot", "restrictedEgress", "workspaceBind"]},
        "isolation": {"runDedicated": True},
        "limits": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256, "timeoutSeconds": 5400, "temporaryStorageMiB": 256},
        "network": {"egressPolicyRef": "omnigent-restricted-egress@1"},
        "capture": {"required": True, "retentionDays": 30},
        "cleanup": {"mode": "remove", "janitor": True},
        "controlCapabilities": ["interrupt", "terminate", "clear_context"],
    }
)

register_launch_policy(
    {
        "schemaVersion": "moonmind.omnigent-launch-policy.v2",
        "policyId": "codex-on-demand",
        "version": 1,
        "hostMode": "on-demand",
        "hostClassSelector": {"requiredFeatures": ["readOnlyRoot", "restrictedEgress", "workspaceBind"]},
        "isolation": {"runDedicated": True},
        "limits": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256, "timeoutSeconds": 5400, "temporaryStorageMiB": 256},
        "network": {"egressPolicyRef": "omnigent-restricted-egress@1"},
        "capture": {"required": True, "retentionDays": 30},
        "cleanup": {"mode": "remove", "janitor": True},
        "controlCapabilities": ["interrupt", "terminate", "clear_context"],
    }
)

register_launch_policy(
    {
        "schemaVersion": "moonmind.omnigent-launch-policy.v2",
        "policyId": "codex-static",
        "version": 1,
        "hostMode": "static-connected",
        "hostClassSelector": {"requiredFeatures": ["workspaceBind"]},
        "isolation": {"runDedicated": False},
        "limits": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256, "timeoutSeconds": 5400, "temporaryStorageMiB": 256},
        "network": {"egressPolicyRef": "omnigent-restricted-egress@1"},
        "capture": {"required": True, "retentionDays": 30},
        "cleanup": {"mode": "drain", "janitor": True},
        "controlCapabilities": ["interrupt", "terminate", "clear_context"],
    }
)


def get_launch_policy(ref: str) -> LaunchPolicy:
    if ref not in LAUNCH_POLICIES:
        raise HarnessPlatformError(
            f"launch policy {ref} incompatible or unavailable",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    return LAUNCH_POLICIES[ref]


def validate_policy_for_host_class(
    *,
    policy: LaunchPolicy,
    host_class: HostClass,
    harness_integration_mode: str,
    materializer_refs: list[str],
    workspace_mutation: bool = True,
) -> None:
    if not policy.allows_host_class(host_class):
        raise HarnessPlatformError(
            f"policy {policy.ref} incompatible with host class {host_class.ref}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    if harness_integration_mode not in host_class.integrationModes:
        raise HarnessPlatformError(
            f"harness integration mode {harness_integration_mode} not in host class",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    for mat in materializer_refs:
        if not host_class.supports_materializer(mat):
            raise HarnessPlatformError(
                f"materializer {mat} not supported by host class {host_class.ref}",
                code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
            )
