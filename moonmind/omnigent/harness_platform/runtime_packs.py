"""Trusted runtime-pack descriptors (Primary Runtime Provider Strategy §5.4).

Runtime packs are deployment-owned, versioned descriptors that isolate genuine
harness-specific details behind a small trusted boundary. The generic host
lifecycle consumes the selected pack rather than accumulating harness-specific
branches.

Schema version: moonmind.omnigent-harness-runtime-pack.v1
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*@[0-9]+$")


class RuntimePack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-harness-runtime-pack.v1", alias="schemaVersion"
    )
    ref: str
    harnessId: str = Field(alias="harnessId")
    providerRuntimeId: str = Field(alias="providerRuntimeId")
    binary: dict[str, str]
    credentialMaterializers: tuple[str, ...] = Field(alias="credentialMaterializers")
    forbiddenAmbientEnvironment: tuple[str, ...] = Field(
        default_factory=tuple, alias="forbiddenAmbientEnvironment"
    )
    probes: dict[str, str]
    hostModes: tuple[str, ...] = Field(alias="hostModes")

    @model_validator(mode="after")
    def validate_top(self) -> "RuntimePack":
        if not _REF_RE.fullmatch(self.ref):
            raise ValueError(f"invalid runtime pack ref {self.ref!r}")
        if not _SAFE_ID_RE.fullmatch(self.harnessId):
            raise ValueError("invalid harnessId")
        if "command" not in self.binary or "supportedVersion" not in self.binary:
            raise ValueError("binary must contain command and supportedVersion")
        if not self.credentialMaterializers:
            raise ValueError("credentialMaterializers required")
        for m in self.credentialMaterializers:
            if not _REF_RE.fullmatch(m):
                raise ValueError(f"invalid materializer ref {m!r}")
        if not self.probes or "version" not in self.probes:
            raise ValueError("probes must contain at least version")
        if not self.hostModes:
            raise ValueError("hostModes required")
        for mode in self.hostModes:
            if mode not in {"on-demand", "static-connected"}:
                raise ValueError(f"unknown host mode {mode!r}")
        return self


BUILTIN_RUNTIME_PACKS: dict[str, RuntimePack] = {}


def _register(data: dict[str, object]) -> RuntimePack:
    pack = RuntimePack.model_validate(data)
    BUILTIN_RUNTIME_PACKS[pack.ref] = pack
    return pack


_register(
    {
        "schemaVersion": "moonmind.omnigent-harness-runtime-pack.v1",
        "ref": "codex-native-pack@1",
        "harnessId": "codex-native",
        "providerRuntimeId": "codex_cli",
        "binary": {"command": "codex", "supportedVersion": ">=0.1.0"},
        "credentialMaterializers": ("codex-oauth-home@1", "none@1"),
        "forbiddenAmbientEnvironment": ("OPENAI_API_KEY",),
        "probes": {
            "version": "codex --version",
            "authentication": "codex login status",
        },
        "hostModes": ("on-demand", "static-connected"),
    }
)

_register(
    {
        "schemaVersion": "moonmind.omnigent-harness-runtime-pack.v1",
        "ref": "claude-native-pack@1",
        "harnessId": "claude-native",
        "providerRuntimeId": "claude_code",
        "binary": {"command": "claude", "supportedVersion": ">=1.0.0"},
        "credentialMaterializers": ("claude-oauth-home@1", "none@1"),
        "forbiddenAmbientEnvironment": ("ANTHROPIC_API_KEY",),
        "probes": {
            "version": "claude --version",
            "authentication": "claude auth status",
        },
        "hostModes": ("on-demand", "static-connected"),
    }
)

_register(
    {
        "schemaVersion": "moonmind.omnigent-harness-runtime-pack.v1",
        "ref": "opencode-native-pack@1",
        "harnessId": "opencode-native",
        "providerRuntimeId": "opencode",
        "binary": {"command": "opencode", "supportedVersion": ">=1.17.7,<1.19.0"},
        "credentialMaterializers": ("opencode-auth-json@1", "none@1"),
        "forbiddenAmbientEnvironment": (
            "OPENCODE_AUTH_CONTENT",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        "probes": {
            "version": "opencode --version",
            "authentication": "live-model-options",
        },
        "hostModes": ("on-demand", "static-connected"),
    }
)


def get_runtime_pack(ref: str) -> RuntimePack:
    pack = BUILTIN_RUNTIME_PACKS.get(ref)
    if pack is None:
        raise HarnessPlatformError(
            f"runtime pack {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    return pack


def runtime_pack_for_harness(harness_id: str) -> RuntimePack:
    for pack in BUILTIN_RUNTIME_PACKS.values():
        if pack.harnessId == harness_id:
            return pack
    raise HarnessPlatformError(
        f"no runtime pack for harness {harness_id}",
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
    )


def list_runtime_packs() -> list[RuntimePack]:
    return sorted(BUILTIN_RUNTIME_PACKS.values(), key=lambda p: p.ref)
