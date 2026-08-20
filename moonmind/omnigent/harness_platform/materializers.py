"""Credential materializer registry (section 12).

Trusted boundary that turns leased Provider Profile + generation into runtime state.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

class MaterializerTargetKind(StrEnum):
    generated_file = "generated-file"
    oauth_home = "oauth-home"
    provider_config = "provider-config"
    secret_env_file = "secret-env-file"
    session_config = "session-config"
    host_owned_auth = "host-owned-auth"
    none = "none"


class CredentialMaterializer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    materializerId: str = Field(alias="materializerId")
    version: int = Field(ge=1)
    acceptedHarnessImplementations: tuple[str, ...] = Field(alias="acceptedHarnessImplementations")
    acceptedAuthModels: tuple[str, ...] = Field(alias="acceptedAuthModels")
    supportedHostModes: tuple[str, ...] = Field(alias="supportedHostModes")
    requiredSecretRoles: tuple[str, ...] = Field(alias="requiredSecretRoles")
    state: dict[str, Any]
    target: dict[str, Any]
    preflight: dict[str, Any] | None = None
    cleanup: dict[str, Any]

    @model_validator(mode="after")
    def validate_top(self) -> "CredentialMaterializer":
        if not _SAFE_ID_RE.fullmatch(self.materializerId):
            raise ValueError("invalid materializerId")
        if not self.acceptedHarnessImplementations:
            raise ValueError("acceptedHarnessImplementations required")
        for ref in self.acceptedHarnessImplementations:
            if not ref.startswith("omnigent-harness-implementation:sha256:"):
                raise ValueError("acceptedHarnessImplementations must be implementation refs")
        if not self.acceptedAuthModels:
            raise ValueError("acceptedAuthModels required")
        if not self.supportedHostModes:
            raise ValueError("supportedHostModes required")
        # target must have kind
        if "kind" not in self.target:
            raise ValueError("target.kind required")
        kind = self.target["kind"]
        allowed_kinds = {e.value for e in MaterializerTargetKind}
        if kind not in allowed_kinds and kind not in {"generated-file", "oauth-home"}:
            raise ValueError(f"unknown target kind {kind}")
        if kind == "generated-file" and "path" not in self.target:
            raise ValueError("generated-file target requires path")
        if "mode" not in self.cleanup:
            raise ValueError("cleanup.mode required")
        return self

    @property
    def ref(self) -> str:
        return f"{self.materializerId}@{self.version}"

    def supports_harness(self, implementation_ref: str) -> bool:
        return implementation_ref in self.acceptedHarnessImplementations

    def supports_host_mode(self, host_mode: str) -> bool:
        return host_mode in self.supportedHostModes


# Built-in registry
BUILTIN_MATERIALIZERS: dict[str, CredentialMaterializer] = {}


def _impl_ref_for_materializer(package: str, version: str, digest: str, kind: str = "core", entry: str | None = None) -> str:
    from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
    return HarnessImplementationIdentity.model_validate(
        {"sourceKind": kind, "package": package, "version": version, "digest": digest, "pluginEntryPoint": entry}
    ).implementation_ref()

def _register_builtin(data: dict[str, Any]) -> CredentialMaterializer:
    m = CredentialMaterializer.model_validate(data)
    BUILTIN_MATERIALIZERS[m.ref] = m
    return m


_register_builtin(
    {
        "materializerId": "codex-oauth-home",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "a" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "e" * 64),
        ],
        "acceptedAuthModels": ["oauth_volume"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["oauth_token"],
        "state": {"scope": "run", "mutable": True},
        "target": {"kind": "oauth-home", "path": "/home/app/.codex", "permissions": "0700"},
        "preflight": {"kind": "login-status"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "opencode-auth-json",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "a" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "b" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64),
        ],
        "acceptedAuthModels": ["own-auth"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "generated-file", "path": "/home/app/.local/share/opencode/auth.json", "permissions": "0600"},
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "omnigent-provider-config",
        "version": 1,
        "acceptedHarnessImplementations": [_impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64)],
        "acceptedAuthModels": ["omnigent-provider-config"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "generated-file", "path": "/home/app/.config/omnigent/provider.json", "permissions": "0600"},
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)


def get_materializer(ref: str) -> CredentialMaterializer:
    if ref not in BUILTIN_MATERIALIZERS:
        raise HarnessPlatformError(
            f"credential materializer {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    return BUILTIN_MATERIALIZERS[ref]


def validate_binding_materializer(
    *,
    materializer_ref: str,
    harness_implementation_ref: str,
    host_mode: str,
    required_secret_roles: list[str] | None = None,
) -> CredentialMaterializer:
    mat = get_materializer(materializer_ref)
    if not mat.supports_harness(harness_implementation_ref):
        raise HarnessPlatformError(
            f"materializer {materializer_ref} incompatible with harness {harness_implementation_ref}",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    if not mat.supports_host_mode(host_mode):
        raise HarnessPlatformError(
            f"materializer {materializer_ref} does not support host mode {host_mode}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    if required_secret_roles:
        missing = set(required_secret_roles) - set(mat.requiredSecretRoles)
        if missing:
            raise HarnessPlatformError(
                f"materializer missing required secret roles: {missing}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
    return mat


def materialize_credential(
    *,
    materializer_ref: str,
    provider_profile_ref: str,
    provider_lease_ref: str,
    credential_generation: int,
    host_mode: str = "on-demand",
) -> dict[str, Any]:
    """Return secret-free handle (section 12.3). Never contains secret bodies."""
    mat = get_materializer(materializer_ref)
    # Forbidden: secret bodies in handle
    target_path = mat.target.get("path", "")
    # Derive access mode from materializer state: mutable OAuth homes must remain writable for token refresh
    is_mutable = bool(mat.state.get("mutable"))
    access_mode = "read_write" if is_mutable else "read-only"
    return {
        "credentialRuntimeRef": f"credential-runtime:{provider_profile_ref}:{credential_generation}",
        "providerProfileRef": provider_profile_ref,
        "providerLeaseRef": provider_lease_ref,
        "credentialGeneration": credential_generation,
        "materializerRef": mat.ref,
        "mountClass": "provider-auth",
        "targetPath": target_path,
        "accessMode": access_mode,
        "cleanupRef": f"credential-cleanup:{provider_profile_ref}:{credential_generation}",
        "attestationRef": f"artifact:{mat.materializerId}:{credential_generation}",
    }
