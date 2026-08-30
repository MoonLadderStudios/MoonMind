"""Credential materializer registry (section 12).

Trusted boundary that turns leased Provider Profile + generation into runtime state.
"""

from __future__ import annotations

import json
import os
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

# OpenCode materializer constants per issue §5
OPENCODE_AUTH_TARGET_PATH = "/home/app/.local/share/opencode/auth.json"
OPENCODE_AUTH_PARENT_MODE = 0o700
OPENCODE_AUTH_FILE_MODE = 0o600
OPENCODE_AUTH_UID = 1000
OPENCODE_AUTH_GID = 1000
OPENCODE_PROVIDER_KEY = "opencode-go"
OPENCODE_BUILTIN_PROVIDER_KEY = "opencode"
OPENCODE_PROVIDER_KEYS = (OPENCODE_PROVIDER_KEY, OPENCODE_BUILTIN_PROVIDER_KEY)
OPENCODE_SUPPORTED_VERSION_RANGE = (  # inclusive lower, exclusive upper
    "1.17.7",
    "1.19.0",
)
FORBIDDEN_AMBIENT_ENV_KEYS = (
    "OPENCODE_AUTH_CONTENT",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
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
    acceptedHarnessImplementations: tuple[str, ...] = Field(
        default_factory=tuple, alias="acceptedHarnessImplementations"
    )
    acceptedHarnessIds: tuple[str, ...] = Field(
        default_factory=tuple, alias="acceptedHarnessIds"
    )
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
        if not self.acceptedHarnessImplementations and not self.acceptedHarnessIds:
            raise ValueError(
                "acceptedHarnessImplementations or acceptedHarnessIds required"
            )
        for ref in self.acceptedHarnessImplementations:
            if not ref.startswith("omnigent-harness-implementation:sha256:"):
                raise ValueError(
                    "acceptedHarnessImplementations must be implementation refs"
                )
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

    def supports_harness(
        self, implementation_ref: str, harness_id: str | None = None
    ) -> bool:
        return implementation_ref in self.acceptedHarnessImplementations or (
            harness_id is not None and harness_id in self.acceptedHarnessIds
        )

    def supports_host_mode(self, host_mode: str) -> bool:
        return host_mode in self.supportedHostModes


# Built-in registry
BUILTIN_MATERIALIZERS: dict[str, CredentialMaterializer] = {}

_PROVIDER_MATERIALIZER_REFS: dict[tuple[str, str], str] = {
    ("opencode", "opencode-go"): "opencode-auth-json@1",
    ("opencode", "opencode"): "opencode-auth-json@1",
    ("codex_cli", "openai"): "codex-oauth-home@1",
    ("omnigent", "anthropic"): "omnigent-provider-config@1",
    ("omnigent", "openai"): "omnigent-provider-config@1",
}


def _register_builtin(data: dict[str, Any]) -> CredentialMaterializer:
    m = CredentialMaterializer.model_validate(data)
    BUILTIN_MATERIALIZERS[m.ref] = m
    return m


_register_builtin(
    {
        "materializerId": "codex-oauth-home",
        "version": 1,
        "acceptedHarnessIds": ["codex-native"],
        "acceptedAuthModels": ["oauth_volume"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["oauth_token"],
        "state": {"scope": "run", "mutable": True},
        "target": {
            "kind": "oauth-home",
            "path": "/home/app/.codex",
            "permissions": "0700",
        },
        "preflight": {"kind": "login-status"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "opencode-auth-json",
        "version": 1,
        "acceptedHarnessIds": ["opencode-native"],
        "acceptedAuthModels": ["own-auth"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["opencode_api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {
            "kind": "generated-file",
            "path": "/home/app/.local/share/opencode/auth.json",
            "permissions": "0600",
        },
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "omnigent-provider-config",
        "version": 1,
        "acceptedHarnessIds": ["pi-native"],
        "acceptedAuthModels": ["omnigent-provider-config"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {
            "kind": "generated-file",
            "path": "/home/app/.moonmind-provider-config/config.yaml",
            "permissions": "0600",
        },
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "host-owned-auth",
        "version": 1,
        "acceptedHarnessIds": ["pi-native", "claude-native"],
        "acceptedAuthModels": ["host-owned-auth"],
        "supportedHostModes": ["static-connected"],
        "requiredSecretRoles": [],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "host-owned-auth", "path": ""},
        "preflight": {"kind": "host-auth"},
        "cleanup": {"mode": "none"},
    }
)

_register_builtin(
    {
        "materializerId": "none",
        "version": 1,
        "acceptedHarnessIds": [
            "codex-native",
            "opencode-native",
            "pi-native",
            "claude-native",
        ],
        "acceptedAuthModels": ["none"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": [],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "none", "path": ""},
        "preflight": {"kind": "none"},
        "cleanup": {"mode": "none"},
    }
)


def get_materializer(ref: str) -> CredentialMaterializer:
    if ref not in BUILTIN_MATERIALIZERS:
        raise HarnessPlatformError(
            f"credential materializer {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    return BUILTIN_MATERIALIZERS[ref]


def materializer_ref_for_provider(runtime_id: str, provider_id: str) -> str:
    ref = _PROVIDER_MATERIALIZER_REFS.get((runtime_id, provider_id))
    if ref is None:
        raise HarnessPlatformError(
            f"no credential materializer is registered for {runtime_id}/{provider_id}",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    return ref


def validate_binding_materializer(
    *,
    materializer_ref: str,
    harness_implementation_ref: str,
    harness_id: str | None = None,
    host_mode: str,
    required_secret_roles: list[str] | None = None,
) -> CredentialMaterializer:
    mat = get_materializer(materializer_ref)
    if not mat.supports_harness(harness_implementation_ref, harness_id):
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


# ---- OpenCode opencode-auth-json@1 trusted materialization (issue §5) ----


def _assert_no_forbidden_ambient_env() -> None:
    """Fail if conflicting ambient credentials are present (issue §5)."""
    present = [k for k in FORBIDDEN_AMBIENT_ENV_KEYS if os.environ.get(k)]
    if present:
        raise HarnessPlatformError(
            f"conflicting ambient credentials present: {present}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )


def _opencode_auth_json_payload(
    *, api_key: str, provider_key: str | None = None
) -> dict[str, Any]:
    """Exact OpenCode credential structure for the pinned version.

    Verified against opencode-ai 1.18.x auth.json shape:
    The provider key is `opencode-go` or `opencode` for the Zen free tier,
    and the file contains the API key under that key. This is the only
    secret-bearing structure; all diagnostics, handles, and logs must remain
    secret-free.
    """
    if not api_key or not api_key.strip():
        raise HarnessPlatformError(
            "api_key is required for opencode-auth-json",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    key = api_key.strip()
    if provider_key is not None:
        if provider_key not in OPENCODE_PROVIDER_KEYS:
            raise HarnessPlatformError(
                f"unsupported OpenCode provider key {provider_key!r}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return {provider_key: {"type": "api", "key": key}}
    # Default: write both provider keys with the same API key so that a single
    # credential grants access to both OpenCode Go and built-in Zen models.
    return {
        provider: {"type": "api", "key": key} for provider in OPENCODE_PROVIDER_KEYS
    }


def build_opencode_auth_json_bytes(
    *, api_key: str, provider_key: str | None = None
) -> bytes:
    """Return canonical JSON bytes for auth.json without touching filesystem."""
    payload = _opencode_auth_json_payload(api_key=api_key, provider_key=provider_key)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def assert_opencode_materialization_secret_free(
    handle: dict[str, Any], raw_key: str
) -> None:
    """Test helper: prove raw key is absent from handle and evidence."""
    handle_json = json.dumps(handle, sort_keys=True)
    if raw_key and raw_key in handle_json:
        raise AssertionError("handle leaked raw api key")


def clear_forbidden_ambient_env() -> list[str]:
    """Remove conflicting ambient credentials and return the cleared key names (no raw values)."""
    cleared: list[str] = []
    for key in FORBIDDEN_AMBIENT_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared.append(key)
    return cleared
