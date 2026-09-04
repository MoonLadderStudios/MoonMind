"""Trusted runtime-pack descriptors for the shared Omnigent host image.

A runtime pack is the deployment-owned declaration of how one vendor runtime
behaves inside the shared ``omnigent-host-moonmind`` image: the exact vendor
runtime identity the image must contain, the bounded environment the generic
startup may shape, the credential-home layout it mounts, and the exact-host
readiness and attestation probes that must pass before a runner starts.

Workflows, Agent Profiles, and Omnigent never author a pack. The registry is
pure data: it carries no secret, no image digest, and no endpoint. Host Class
templates consume pack refs to declare vendor runtime dependencies, and
exact-host attestation consumes pack refs to validate what the launched
container actually reports.

Source: MoonLadderStudios/MoonMind#3827 (design doc:
``docs/Omnigent/PrimaryRuntimeProviderStrategy.md`` sections 5.3 and 6).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

RUNTIME_PACK_SCHEMA_VERSION = "moonmind.omnigent-harness-runtime-pack.v1"

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,3}$")
_RANGE_RE = re.compile(r"^>=([0-9.]+),<([0-9.]+)$")


class VendorRuntimeDescriptor(BaseModel):
    """One vendor runtime binary the pack requires in the shared image."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    versionCommand: tuple[str, ...] = Field(alias="versionCommand")
    versionRegex: str = Field(alias="versionRegex")
    supportedRange: str = Field(alias="supportedRange")
    pinnedVersion: str = Field(alias="pinnedVersion")

    @model_validator(mode="after")
    def validate_top(self) -> VendorRuntimeDescriptor:
        if not self.name.strip() or not _SAFE_ID_RE.fullmatch(self.name):
            raise ValueError(f"invalid vendor runtime name {self.name!r}")
        if not self.versionCommand or not all(
            part.strip() for part in self.versionCommand
        ):
            raise ValueError("versionCommand required")
        try:
            re.compile(self.versionRegex)
        except re.error as exc:
            raise ValueError(f"invalid versionRegex: {exc}") from exc
        if not _RANGE_RE.fullmatch(self.supportedRange):
            raise ValueError(
                f"supportedRange must be '>=<min>,<<max>' (got {self.supportedRange!r})"
            )
        if not _VERSION_RE.fullmatch(self.pinnedVersion):
            raise ValueError(
                f"pinnedVersion must be a plain version (got {self.pinnedVersion!r})"
            )
        low, high = self.supportedRange.split(",", 1)
        if low[2:] > high[1:]:
            raise ValueError("supportedRange lower bound must not exceed upper")
        return self


class CredentialLayoutDescriptor(BaseModel):
    """The credential-home layout the pack's materializer mounts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    targetPath: str = Field(alias="targetPath")
    writable: bool = True
    ownerUid: int = Field(1000, alias="ownerUid")
    ownerGid: int = Field(1000, alias="ownerGid")

    @model_validator(mode="after")
    def validate_top(self) -> CredentialLayoutDescriptor:
        path = self.targetPath.rstrip("/")
        if not path.startswith("/home/app/") or path.count("/") < 3:
            raise ValueError(
                f"credential home target must be under /home/app/ (got {self.targetPath!r})"
            )
        if self.ownerUid <= 0 or self.ownerGid <= 0:
            raise ValueError("credential home owner must be a positive uid/gid")
        return self


class ReadinessProbeDescriptor(BaseModel):
    """A bounded exact-host readiness probe selected by the pack."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["vendor-version", "login-status", "none"] = "vendor-version"
    command: tuple[str, ...] = ()
    outputRegex: str | None = Field(None, alias="outputRegex")

    @model_validator(mode="after")
    def validate_top(self) -> ReadinessProbeDescriptor:
        if self.kind == "none":
            return self
        if not self.command or not all(part.strip() for part in self.command):
            raise ValueError(f"{self.kind} probe requires a command")
        if self.outputRegex is not None:
            try:
                re.compile(self.outputRegex)
            except re.error as exc:
                raise ValueError(f"invalid outputRegex: {exc}") from exc
        return self


class RuntimePackDescriptor(BaseModel):
    """One deployment-owned runtime pack (``<pack-id>@<version>``)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schemaVersion: str = Field(RUNTIME_PACK_SCHEMA_VERSION)
    packId: str = Field(alias="packId")
    version: int = Field(ge=1)
    harnessIds: tuple[str, ...] = Field(alias="harnessIds")
    vendorRuntime: VendorRuntimeDescriptor = Field(alias="vendorRuntime")
    credentialLayout: CredentialLayoutDescriptor = Field(alias="credentialLayout")
    # Bounded environment shaping the generic startup may apply. Keys are
    # validated at registration; values never contain credentials.
    environment: dict[str, str] = Field(default_factory=dict)
    forbiddenAmbientEnvKeys: tuple[str, ...] = Field(
        default=(), alias="forbiddenAmbientEnvKeys"
    )
    readiness: ReadinessProbeDescriptor = Field(
        default_factory=lambda: ReadinessProbeDescriptor(kind="none")
    )

    @model_validator(mode="after")
    def validate_top(self) -> RuntimePackDescriptor:
        if self.schemaVersion != RUNTIME_PACK_SCHEMA_VERSION:
            raise ValueError(f"unsupported runtime-pack schema {self.schemaVersion}")
        if not _SAFE_ID_RE.fullmatch(self.packId):
            raise ValueError(f"invalid packId {self.packId!r}")
        if not self.harnessIds or not all(
            _SAFE_ID_RE.fullmatch(harness) for harness in self.harnessIds
        ):
            raise ValueError("harnessIds must be non-empty safe ids")
        for key, value in self.environment.items():
            if not key or not key.replace("_", "").isalnum():
                raise ValueError(f"invalid pack environment key {key!r}")
            if not value or "\n" in value or "\r" in value or "\0" in value:
                raise ValueError(f"invalid pack environment value for {key!r}")
        for key in self.forbiddenAmbientEnvKeys:
            if not key or not key.replace("_", "").isalnum():
                raise ValueError(f"invalid forbidden ambient key {key!r}")
        return self

    @property
    def ref(self) -> str:
        return f"{self.packId}@{self.version}"

    def supports_harness(self, harness_id: str) -> bool:
        return harness_id in self.harnessIds


_RUNTIME_PACKS: dict[str, RuntimePackDescriptor] = {}


def register_runtime_pack(pack: RuntimePackDescriptor) -> None:
    """Register a runtime pack. Re-registering identical data is a no-op."""

    existing = _RUNTIME_PACKS.get(pack.ref)
    if existing is not None and existing != pack:
        raise HarnessPlatformError(
            f"runtime pack {pack.ref} is already registered with a different "
            "descriptor",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    _RUNTIME_PACKS[pack.ref] = pack


def get_runtime_pack(ref: str) -> RuntimePackDescriptor:
    pack = _RUNTIME_PACKS.get(ref)
    if pack is None:
        raise HarnessPlatformError(
            f"runtime pack {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    return pack


def pack_ref_for_harness(harness_id: str) -> str:
    """Return the one registered pack ref that owns ``harness_id``."""

    matches = sorted(
        pack.ref for pack in _RUNTIME_PACKS.values() if pack.supports_harness(harness_id)
    )
    if not matches:
        raise HarnessPlatformError(
            f"no runtime pack is registered for harness {harness_id}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    if len(matches) > 1:
        raise HarnessPlatformError(
            f"runtime pack selection is ambiguous for {harness_id}: {matches}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    return matches[0]


def is_vendor_version_supported(pack: RuntimePackDescriptor, version: str) -> bool:
    """Return whether ``version`` is inside the pack's supported range."""

    match = _RANGE_RE.fullmatch(pack.vendorRuntime.supportedRange)
    if match is None:
        return False
    low, high = match.group(1), match.group(2)

    def parse(value: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in value.split("."))
        except ValueError:
            return ()

    observed = parse(version)
    if not observed:
        return False
    lower, upper = parse(low), parse(high)
    if not lower or not upper:
        return False
    width = max(len(observed), len(lower), len(upper))
    observed += (0,) * (width - len(observed))
    lower += (0,) * (width - len(lower))
    upper += (0,) * (width - len(upper))
    return lower <= observed < upper


def runtime_dependencies_for_pack(pack: RuntimePackDescriptor) -> tuple[dict[str, Any], ...]:
    """Return the Host Class ``runtimeDependencies`` entries for a pack.

    The descriptor pins the vendor version identity, while the launched image
    attests the installed runtime. The planner binds the support key to the
    exact host image digest when no per-runtime digest is recorded, so a
    rebuilt image cannot reuse prior evidence.
    """

    return (
        {
            "name": pack.vendorRuntime.name,
            "version": pack.vendorRuntime.pinnedVersion,
        },
    )


def _register(pack: RuntimePackDescriptor) -> None:
    register_runtime_pack(pack)


def _pack(
    *,
    pack_id: str,
    harness_ids: tuple[str, ...],
    vendor_name: str,
    version_command: tuple[str, ...],
    version_regex: str,
    supported_range: str,
    pinned_version: str,
    credential_target: str,
    environment: dict[str, str] | None = None,
    forbidden: tuple[str, ...] = (),
) -> RuntimePackDescriptor:
    return RuntimePackDescriptor.model_validate(
        {
            "packId": pack_id,
            "version": 1,
            "harnessIds": list(harness_ids),
            "vendorRuntime": {
                "name": vendor_name,
                "versionCommand": list(version_command),
                "versionRegex": version_regex,
                "supportedRange": supported_range,
                "pinnedVersion": pinned_version,
            },
            "credentialLayout": {"targetPath": credential_target},
            "environment": environment or {},
            "forbiddenAmbientEnvKeys": list(forbidden),
            "readiness": {"kind": "vendor-version", "command": list(version_command)},
        }
    )


_OPENCODE_FORBIDDEN = (
    "OPENCODE_AUTH_CONTENT",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
)
_SHARED_OAUTH_FORBIDDEN = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_BASE_URL",
)

register_runtime_pack(
    _pack(
        pack_id="opencode-native-pack",
        harness_ids=("opencode-native",),
        vendor_name="opencode",
        version_command=("opencode", "--version"),
        version_regex=r"[0-9]+\.[0-9]+\.[0-9]+",
        supported_range=">=1.17.7,<1.19.0",
        pinned_version="1.18.11",
        credential_target="/home/app/.local/share/opencode",
        environment={
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        },
        forbidden=_OPENCODE_FORBIDDEN + _SHARED_OAUTH_FORBIDDEN,
    )
)
# Vendor pins mirror the deployment's CLI tooling pins
# (``api_service/Dockerfile`` CLAUDE_CLI_VERSION, the documented known-good
# CODEX_CLI_VERSION in ``tools/update-moonmind.sh``, and the OpenCode pin in
# ``host_classes.OPENCODE_PINNED_VERSION``). Updating a pin is a deployment
# change that must land in the shared image build args and these descriptors
# in the same change; exact-host attestation rejects a drifted runtime.
register_runtime_pack(
    _pack(
        pack_id="codex-native-pack",
        harness_ids=("codex-native",),
        vendor_name="codex",
        version_command=("codex", "--version"),
        version_regex=r"[0-9]+\.[0-9]+\.[0-9]+",
        supported_range=">=0.100.0,<0.200.0",
        pinned_version="0.104.0",
        credential_target="/home/app/.codex",
        forbidden=_SHARED_OAUTH_FORBIDDEN
        + ("CODEX_ACCESS_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
)
register_runtime_pack(
    _pack(
        pack_id="claude-native-pack",
        harness_ids=("claude-native",),
        vendor_name="claude",
        version_command=("claude", "--version"),
        version_regex=r"[0-9]+\.[0-9]+\.[0-9]+",
        supported_range=">=2.0.0,<3.0.0",
        pinned_version="2.1.257",
        credential_target="/home/app/.claude",
        forbidden=_SHARED_OAUTH_FORBIDDEN
        + ("CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
)


__all__ = [
    "RUNTIME_PACK_SCHEMA_VERSION",
    "CredentialLayoutDescriptor",
    "ReadinessProbeDescriptor",
    "RuntimePackDescriptor",
    "VendorRuntimeDescriptor",
    "get_runtime_pack",
    "is_vendor_version_supported",
    "pack_ref_for_harness",
    "register_runtime_pack",
    "runtime_dependencies_for_pack",
]
