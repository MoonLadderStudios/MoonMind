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
    def validate_top(self) -> CredentialMaterializer:
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

# Ownership classes for profile-owned OAuth credential homes (#3829): the
# enrollment-owned volume is attached read-write so vendor token refresh
# persists, run cleanup only detaches it, and the durable enrollment state
# survives every host.
OAUTH_HOME_PROFILE_OWNED_STATE = {"scope": "profile", "mutable": True}
OAUTH_HOME_PROFILE_OWNED_CLEANUP = {"mode": "detach-profile-owned"}

_PROVIDER_MATERIALIZER_REFS: dict[tuple[str, str], str] = {
    ("codex_cli", "openai"): "codex-oauth-home@1",
    ("claude_code", "anthropic"): "claude-oauth-home@1",
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
        "requiredSecretRoles": [],
        "state": OAUTH_HOME_PROFILE_OWNED_STATE,
        "target": {
            "kind": "oauth-home",
            "path": "/home/app/.codex",
            "permissions": "0700",
        },
        "preflight": {"kind": "login-status"},
        "cleanup": OAUTH_HOME_PROFILE_OWNED_CLEANUP,
    }
)

_register_builtin(
    {
        "materializerId": "claude-oauth-home",
        "version": 1,
        "acceptedHarnessIds": ["claude-native"],
        "acceptedAuthModels": ["oauth_volume"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": [],
        "state": OAUTH_HOME_PROFILE_OWNED_STATE,
        "target": {
            "kind": "oauth-home",
            "path": "/home/app/.claude",
            "permissions": "0700",
        },
        "preflight": {"kind": "login-status"},
        "cleanup": OAUTH_HOME_PROFILE_OWNED_CLEANUP,
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
        "acceptedHarnessIds": ["pi-native", "claude-native", "codex-native"],
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
    if runtime_id == "opencode" and _SAFE_ID_RE.fullmatch(provider_id):
        return (
            "none@1"
            if provider_id == OPENCODE_BUILTIN_PROVIDER_KEY
            else "opencode-auth-json@1"
        )
    ref = _PROVIDER_MATERIALIZER_REFS.get((runtime_id, provider_id))
    if ref is None:
        raise HarnessPlatformError(
            f"no credential materializer is registered for {runtime_id}/{provider_id}",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    return ref


PROVIDER_CONFIG_MATERIALIZER_REF = "omnigent-provider-config@1"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_CLAUDE_KEY_ENV = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})
# Claude Code's per-tier model pins and the Omnigent provider-config ``models``
# keys that set the same pins on the native Claude launch.
_CLAUDE_TIER_MODEL_ENV = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "opus",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "haiku",
    "ANTHROPIC_DEFAULT_FABLE_MODEL": "fable",
}
_CLAUDE_CARRIED_LITERAL_ENV = frozenset(
    {"ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", *_CLAUDE_TIER_MODEL_ENV}
)
_CODEX_KEY_ENV = frozenset({"OPENAI_API_KEY"})
_CODEX_CARRIED_LITERAL_ENV = frozenset({"OPENAI_BASE_URL"})


class ProviderConfigEndpoint(BaseModel):
    """Secret-free endpoint an API-key Provider Profile declares.

    The Omnigent provider config carries a key-authenticated provider as one
    model family with a base URL, a static key, and model pins. Omnigent's
    native Claude launch turns that into ``ANTHROPIC_BASE_URL``, a key helper
    and per-tier model pins; its native Codex launch turns an ``openai``
    family into a ``model_provider`` override (base URL, bearer auth command,
    Responses wire).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: str
    secretRole: str = Field(alias="secretRole")
    baseUrl: str = Field(alias="baseUrl")
    defaultModel: str | None = Field(None, alias="defaultModel")
    modelPins: dict[str, str] = Field(default_factory=dict, alias="modelPins")


def _enum_value(raw: Any) -> str:
    return str(getattr(raw, "value", raw) or "").strip()


def _omnigent_egress_admits(url: str) -> bool:
    from urllib.parse import urlsplit

    from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE

    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or port not in (None, 443):
        return False
    return any(
        destination.dns_name is not None
        and 443 in destination.ports
        and (host == destination.dns_name or host.endswith(f".{destination.dns_name}"))
        for destination in OMNIGENT_EGRESS_PROFILE.destinations
    )


def _declared_key_launch(
    profile: Any,
    *,
    runtime_id: str,
    harness_label: str,
    key_env: frozenset[str],
    carried_literal_env: frozenset[str],
) -> tuple[str, dict[str, str], str]:
    """Split an API-key Profile's launch environment into key role and literals.

    Returns ``(key_role, literals, "")`` when the declared environment is one
    key SecretRef plus literals the provider config has a field for, or
    ``("", {}, reason)`` naming why it cannot be carried exactly.
    """

    if str(getattr(profile, "runtime_id", "") or "").strip() != runtime_id:
        return "", {}, f"not a {harness_label} Provider Profile"
    if _enum_value(getattr(profile, "credential_source", None)) != "secret_ref":
        return "", {}, "not a SecretRef Provider Profile"
    if getattr(profile, "file_templates", None) or getattr(
        profile, "home_path_overrides", None
    ):
        return "", {}, "file templates and home path overrides are direct-launch only"
    secret_refs = dict(getattr(profile, "secret_refs", None) or {})
    key_role = ""
    literals: dict[str, str] = {}
    for name, value in dict(getattr(profile, "env_template", None) or {}).items():
        if isinstance(value, dict) and set(value) == {"from_secret_ref"}:
            role = str(value["from_secret_ref"] or "").strip()
            if name not in key_env or key_role:
                return "", {}, f"{name} cannot carry a secret on the shared path"
            if not str(secret_refs.get(role) or "").strip():
                return "", {}, f"secret role {role!r} has no SecretRef"
            key_role = role
        elif isinstance(value, str) and value.strip() and "{{" not in value:
            literals[name] = value.strip()
        else:
            return "", {}, f"{name} is not a literal value"
    if not key_role:
        return "", {}, f"no {' or '.join(sorted(key_env))} SecretRef"
    uncarried = sorted(set(literals) - carried_literal_env)
    if uncarried:
        return "", {}, f"launch environment {uncarried} has no provider-config field"
    return key_role, literals, ""


def claude_provider_config_endpoint(
    profile: Any,
) -> tuple[ProviderConfigEndpoint | None, str]:
    """Translate an API-key Claude Provider Profile into a provider config.

    Returns the endpoint, or ``None`` with the reason the Profile's declared
    launch environment cannot be carried exactly. Only an equivalent
    translation moves a Profile off its direct launch; anything else keeps it
    there with that reason.
    """

    key_role, literals, reason = _declared_key_launch(
        profile,
        runtime_id="claude_code",
        harness_label="Claude Code",
        key_env=_CLAUDE_KEY_ENV,
        carried_literal_env=_CLAUDE_CARRIED_LITERAL_ENV,
    )
    if not key_role:
        return None, reason
    base_url = literals.get("ANTHROPIC_BASE_URL", ANTHROPIC_DEFAULT_BASE_URL)
    base_url = base_url.rstrip("/")
    if not _omnigent_egress_admits(base_url):
        return None, "the Omnigent egress profile does not admit the endpoint"
    return (
        ProviderConfigEndpoint(
            family="anthropic",
            secretRole=key_role,
            baseUrl=base_url,
            defaultModel=literals.get("ANTHROPIC_MODEL"),
            modelPins={
                tier: literals[name]
                for name, tier in _CLAUDE_TIER_MODEL_ENV.items()
                if name in literals
            },
        ),
        "",
    )


def codex_provider_config_endpoint(
    profile: Any,
) -> tuple[ProviderConfigEndpoint | None, str]:
    """Translate an API-key Codex Provider Profile into a provider config.

    The direct Codex launch reads ``OPENAI_API_KEY`` and, for a custom
    endpoint, ``OPENAI_BASE_URL`` on Codex's built-in Responses provider; an
    ``openai`` provider-config family carries both. A Profile that brings its
    own ``config.toml`` (a custom ``model_provider``, wire or retry tuning)
    keeps its direct launch with the reason.
    """

    key_role, literals, reason = _declared_key_launch(
        profile,
        runtime_id="codex_cli",
        harness_label="Codex CLI",
        key_env=_CODEX_KEY_ENV,
        carried_literal_env=_CODEX_CARRIED_LITERAL_ENV,
    )
    if not key_role:
        return None, reason
    base_url = literals.get("OPENAI_BASE_URL", OPENAI_DEFAULT_BASE_URL).rstrip("/")
    if not _omnigent_egress_admits(base_url):
        return None, "the Omnigent egress profile does not admit the endpoint"
    return (
        ProviderConfigEndpoint(family="openai", secretRole=key_role, baseUrl=base_url),
        "",
    )


_PROVIDER_CONFIG_TRANSLATIONS = {
    "claude_code": claude_provider_config_endpoint,
    "codex_cli": codex_provider_config_endpoint,
}


def is_api_key_launch_profile(profile: Any) -> bool:
    """Whether a Codex or Claude Profile authenticates with a declared key."""

    return (
        str(getattr(profile, "runtime_id", "") or "").strip()
        in _PROVIDER_CONFIG_TRANSLATIONS
        and _enum_value(getattr(profile, "credential_source", None)) == "secret_ref"
    )


def provider_config_endpoint(
    profile: Any,
) -> tuple[ProviderConfigEndpoint | None, str]:
    """Translate an API-key Codex or Claude Profile into a provider config."""

    translate = _PROVIDER_CONFIG_TRANSLATIONS.get(
        str(getattr(profile, "runtime_id", "") or "").strip()
    )
    if translate is None:
        return None, "no provider-config translation for this runtime"
    return translate(profile)


def materializer_ref_for_profile(profile: Any) -> str:
    """Return the materializer for one Provider Profile's credential shape.

    An OAuth Profile mounts its enrolled home; an API-key Codex or Claude
    Profile is delivered as an Omnigent provider config when that is an exact
    translation, and is otherwise refused here (it keeps its direct launch).
    """

    if is_api_key_launch_profile(profile):
        endpoint, reason = provider_config_endpoint(profile)
        if endpoint is None:
            raise HarnessPlatformError(
                f"API-key Provider Profile cannot run on Omnigent: {reason}",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        return PROVIDER_CONFIG_MATERIALIZER_REF
    return materializer_ref_for_provider(
        str(getattr(profile, "runtime_id", "") or "").strip(),
        str(getattr(profile, "provider_id", "") or "").strip(),
    )


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


def _opencode_auth_json_payload(*, api_key: str, provider_key: str) -> dict[str, Any]:
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
    if (
        not _SAFE_ID_RE.fullmatch(provider_key)
        or provider_key == OPENCODE_BUILTIN_PROVIDER_KEY
    ):
        raise HarnessPlatformError(
            f"unsupported OpenCode provider key {provider_key!r}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    return {provider_key: {"type": "api", "key": key}}


def build_opencode_auth_json_bytes(*, api_key: str, provider_key: str) -> bytes:
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
