"""Backend-owned Provider Profile creation capabilities.

This module is the single semantic authority for the guided authentication
surface introduced by MoonLadderStudios/MoonMind#3820.  The browser consumes
these presets; it does not reconstruct credential-source/materialization pairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from moonmind.workflows.temporal.runtime.providers.registry import (
    get_provider,
    get_provider_default,
)


CREATION_PRESET_VERSION = "provider-profile-creation-v1"


@dataclass(frozen=True, slots=True)
class ProviderApiKeyStrategy:
    runtime_id: str
    provider_id: str
    secret_role: str
    role_label: str
    env_key: str
    clear_env_keys: tuple[str, ...]
    auth_strategy: str
    materialization_mode: str
    ready_label: str


_API_KEY_STRATEGIES: dict[tuple[str, str], ProviderApiKeyStrategy] = {
    ("claude_code", "anthropic"): ProviderApiKeyStrategy(
        runtime_id="claude_code",
        provider_id="anthropic",
        secret_role="anthropic_api_key",
        role_label="Anthropic API key",
        env_key="ANTHROPIC_API_KEY",
        clear_env_keys=(
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
        ),
        auth_strategy="api_key_env",
        materialization_mode="api_key_env",
        ready_label="Anthropic API key ready",
    ),
    ("codex_cli", "openai"): ProviderApiKeyStrategy(
        runtime_id="codex_cli",
        provider_id="openai",
        secret_role="openai_api_key",
        role_label="OpenAI API key",
        env_key="OPENAI_API_KEY",
        clear_env_keys=("MINIMAX_API_KEY",),
        auth_strategy="api_key_env",
        materialization_mode="api_key_env",
        ready_label="OpenAI API key ready",
    ),
    ("opencode", "opencode-go"): ProviderApiKeyStrategy(
        runtime_id="opencode",
        provider_id="opencode-go",
        secret_role="opencode_api_key",
        role_label="OpenCode Go API key",
        env_key="OPENCODE_API_KEY",
        clear_env_keys=(
            "OPENCODE_AUTH_CONTENT",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        auth_strategy="opencode_auth_json",
        materialization_mode="composite",
        ready_label="OpenCode Go API key ready",
    ),
    ("opencode", "opencode"): ProviderApiKeyStrategy(
        runtime_id="opencode",
        provider_id="opencode",
        secret_role="opencode_api_key",
        role_label="OpenCode API key",
        env_key="OPENCODE_API_KEY",
        clear_env_keys=(
            "OPENCODE_AUTH_CONTENT",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_CONTENT",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        auth_strategy="opencode_auth_json",
        materialization_mode="composite",
        ready_label="OpenCode API key ready",
    ),
}


def provider_api_key_strategy(
    runtime_id: str,
    provider_id: str,
) -> ProviderApiKeyStrategy | None:
    return _API_KEY_STRATEGIES.get((runtime_id.strip(), provider_id.strip()))


def required_secret_roles(runtime_id: str, provider_id: str) -> tuple[str, ...]:
    strategy = provider_api_key_strategy(runtime_id, provider_id)
    return (strategy.secret_role,) if strategy is not None else ()


def _normalize_declared_auth_methods(methods: Iterable[object]) -> set[str]:
    normalized: set[str] = set()
    for raw_method in methods:
        value = str(getattr(raw_method, "value", raw_method) or "").strip()
        mapped = {
            "oauth_volume": "oauth",
            "secret_ref": "api_key",
            "manual": "api_key",
        }.get(value, value)
        if mapped in {"oauth", "api_key", "none"}:
            normalized.add(mapped)
    return normalized


def _locked_field(value: object, source: str, lock_reason: str) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "editable": False,
        "lock_reason": lock_reason,
    }


def _oauth_method(runtime_id: str) -> dict[str, Any]:
    mount_path = get_provider_default(runtime_id, "volume_mount_path")
    return {
        "id": "oauth",
        "label": "OAuth",
        "setup_action": "oauth",
        "launch_ready_after_setup": True,
        "fields": {
            "credential_source": _locked_field(
                "oauth_volume",
                "runtime_provider_strategy",
                "OAuth enrollment owns the credential source.",
            ),
            "runtime_materialization_mode": _locked_field(
                "oauth_home",
                "runtime_provider_strategy",
                "OAuth enrollment owns runtime materialization.",
            ),
        },
        "secret_roles": [],
        "imported_volume": {
            "supported": bool(mount_path),
            "mount_path": mount_path,
            "source": "runtime_provider_strategy",
            "lock_reason": "The runtime strategy owns the credential mount path.",
        },
    }


def _api_key_method(strategy: ProviderApiKeyStrategy) -> dict[str, Any]:
    return {
        "id": "api_key",
        "label": "API key",
        "setup_action": "api_key",
        "launch_ready_after_setup": True,
        "fields": {
            "credential_source": _locked_field(
                "secret_ref",
                "runtime_provider_strategy",
                "Guided API-key setup owns the credential source.",
            ),
            "runtime_materialization_mode": _locked_field(
                strategy.materialization_mode,
                "runtime_provider_strategy",
                "Guided API-key setup owns runtime materialization.",
            ),
            "clear_env_keys": _locked_field(
                list(strategy.clear_env_keys),
                "runtime_provider_strategy",
                "The runtime strategy owns launch credential isolation.",
            ),
        },
        "secret_roles": [
            {
                "role": strategy.secret_role,
                "label": strategy.role_label,
                "required": True,
                "compatible_schemes": ["db", "env"],
            }
        ],
        "imported_volume": {
            "supported": False,
            "mount_path": None,
            "source": "runtime_provider_strategy",
            "lock_reason": "API-key setup does not use a credential volume.",
        },
    }


def _no_credentials_method() -> dict[str, Any]:
    return {
        "id": "none",
        "label": "No credentials",
        "setup_action": "none",
        "launch_ready_after_setup": True,
        "fields": {
            "credential_source": _locked_field(
                "none",
                "runtime_provider_capability",
                "The provider explicitly declares credential-free launch support.",
            ),
            "runtime_materialization_mode": _locked_field(
                "composite",
                "runtime_provider_capability",
                "The credential-free provider strategy owns materialization.",
            ),
        },
        "secret_roles": [],
        "imported_volume": {
            "supported": False,
            "mount_path": None,
            "source": "runtime_provider_capability",
            "lock_reason": "Credential-free launch does not use a credential volume.",
        },
    }


def provider_profile_creation_capabilities(
    *,
    runtime_id: str,
    provider_id: str,
    declared_auth_methods: Iterable[object] = (),
) -> dict[str, Any]:
    """Return the coherent creation methods the backend explicitly supports."""

    runtime_id = runtime_id.strip()
    provider_id = provider_id.strip()
    declared = _normalize_declared_auth_methods(declared_auth_methods)
    methods: list[dict[str, Any]] = []

    oauth_provider = get_provider(runtime_id)
    oauth_supported = bool(
        oauth_provider and oauth_provider.get("provider_id") == provider_id
    )
    if oauth_supported:
        methods.append(_oauth_method(runtime_id))

    api_key_strategy = provider_api_key_strategy(runtime_id, provider_id)
    if api_key_strategy is not None:
        methods.append(_api_key_method(api_key_strategy))

    if "none" in declared:
        methods.append(_no_credentials_method())

    diagnostics: list[str] = []
    if not methods:
        diagnostics.append(
            "No validated creation preset exists for this runtime and provider."
        )
    unrepresented = sorted(declared.difference({method["id"] for method in methods}))
    if unrepresented:
        diagnostics.append(
            "Declared authentication methods lack a guided backend strategy: "
            + ", ".join(unrepresented)
        )

    return {
        "version": CREATION_PRESET_VERSION,
        "runtime_id": runtime_id,
        "provider_id": provider_id,
        "supported": bool(methods),
        "authentication_methods": methods,
        "diagnostics": diagnostics,
    }


def authentication_method_preset(
    *,
    runtime_id: str,
    provider_id: str,
    authentication_method: str,
    declared_auth_methods: Iterable[object] = (),
) -> dict[str, Any]:
    capabilities = provider_profile_creation_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
        declared_auth_methods=declared_auth_methods,
    )
    for method in capabilities["authentication_methods"]:
        if method["id"] == authentication_method:
            return method
    raise ValueError(
        "Unsupported authentication method "
        f"{authentication_method!r} for runtime {runtime_id!r} and provider "
        f"{provider_id!r}."
    )


def infer_authentication_method(
    *,
    credential_source: object,
    runtime_materialization_mode: object,
    declared_method_ids: Iterable[object],
) -> str | None:
    declared = _normalize_declared_auth_methods(declared_method_ids)
    source = str(getattr(credential_source, "value", credential_source) or "")
    materialization = str(
        getattr(runtime_materialization_mode, "value", runtime_materialization_mode)
        or ""
    )
    if (
        source == "oauth_volume"
        and materialization == "oauth_home"
        and "oauth" in declared
    ):
        return "oauth"
    if source == "secret_ref" and "api_key" in declared:
        return "api_key"
    if source == "none" and "none" in declared:
        return "none"
    return None


def validate_credential_contract(
    *,
    credential_source: object,
    runtime_materialization_mode: object,
) -> None:
    """Reject source/materialization pairs that cannot form one launch contract."""

    source = str(getattr(credential_source, "value", credential_source) or "")
    materialization = str(
        getattr(runtime_materialization_mode, "value", runtime_materialization_mode)
        or ""
    )
    oauth_source = source == "oauth_volume"
    oauth_materialization = materialization == "oauth_home"
    if oauth_source != oauth_materialization:
        raise ValueError(
            "Incoherent credential contract: oauth_volume and oauth_home must be "
            "selected together."
        )


__all__ = [
    "CREATION_PRESET_VERSION",
    "ProviderApiKeyStrategy",
    "authentication_method_preset",
    "infer_authentication_method",
    "provider_api_key_strategy",
    "provider_profile_creation_capabilities",
    "required_secret_roles",
    "validate_credential_contract",
]
