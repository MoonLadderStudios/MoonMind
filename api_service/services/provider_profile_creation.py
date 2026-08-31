"""Backend-owned Provider Profile creation capabilities.

This module is the single semantic authority for the guided authentication
surface introduced by MoonLadderStudios/MoonMind#3820.  The browser consumes
these presets; it does not reconstruct credential-source/materialization pairs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True, slots=True)
class ExpertManualCredentialCapability:
    """One exact low-level contract explicitly authorized for expert use."""

    authentication_method: str
    label: str
    credential_source: str
    runtime_materialization_mode: str
    required_secret_roles: tuple[str, ...] = ()
    launch_validator: Callable[[Mapping[str, object]], bool] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProviderAuthenticationCapability:
    """Trusted authentication declarations owned by a runtime/provider pair."""

    runtime_id: str
    provider_id: str
    credential_free: bool = False
    expert_manual_credentials: tuple[ExpertManualCredentialCapability, ...] = ()


def _secret_ref_template_matches(
    value: object,
    *,
    secret_role: str,
) -> bool:
    return isinstance(value, Mapping) and value.get("from_secret_ref") == secret_role


def _expert_env_launch_contract_valid(
    profile: Mapping[str, object],
    *,
    secret_role: str,
    secret_env_key: str,
    required_literal_env_key: str,
) -> bool:
    env_template = profile.get("env_template")
    return bool(
        isinstance(env_template, Mapping)
        and _secret_ref_template_matches(
            env_template.get(secret_env_key),
            secret_role=secret_role,
        )
        and isinstance(env_template.get(required_literal_env_key), str)
        and str(env_template.get(required_literal_env_key) or "").strip()
    )


def _expert_composite_launch_contract_valid(
    profile: Mapping[str, object],
    *,
    secret_role: str,
    secret_env_key: str,
) -> bool:
    env_template = profile.get("env_template")
    file_templates = profile.get("file_templates")
    home_path_overrides = profile.get("home_path_overrides")
    return bool(
        isinstance(env_template, Mapping)
        and _secret_ref_template_matches(
            env_template.get(secret_env_key),
            secret_role=secret_role,
        )
        and isinstance(file_templates, list)
        and file_templates
        and isinstance(home_path_overrides, Mapping)
        and str(home_path_overrides.get("CODEX_HOME") or "").strip()
    )


# This immutable registry is the independent authority for authentication methods
# that cannot be derived from the OAuth or API-key strategy registries. Ordinary
# Provider Profile rows and their mutable command_behavior never extend it.
_RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES: tuple[
    RuntimeProviderAuthenticationCapability, ...
] = (
    RuntimeProviderAuthenticationCapability(
        runtime_id="claude_code",
        provider_id="minimax",
        expert_manual_credentials=(
            ExpertManualCredentialCapability(
                authentication_method="api_key",
                label="MiniMax API key (expert)",
                credential_source="secret_ref",
                runtime_materialization_mode="env_bundle",
                required_secret_roles=("provider_api_key",),
                launch_validator=lambda profile: _expert_env_launch_contract_valid(
                    profile,
                    secret_role="provider_api_key",
                    secret_env_key="ANTHROPIC_AUTH_TOKEN",
                    required_literal_env_key="ANTHROPIC_BASE_URL",
                ),
            ),
        ),
    ),
    RuntimeProviderAuthenticationCapability(
        runtime_id="codex_cli",
        provider_id="minimax",
        expert_manual_credentials=(
            ExpertManualCredentialCapability(
                authentication_method="api_key",
                label="MiniMax API key (expert)",
                credential_source="secret_ref",
                runtime_materialization_mode="composite",
                required_secret_roles=("provider_api_key",),
                launch_validator=lambda profile: _expert_composite_launch_contract_valid(
                    profile,
                    secret_role="provider_api_key",
                    secret_env_key="MINIMAX_API_KEY",
                ),
            ),
        ),
    ),
    RuntimeProviderAuthenticationCapability(
        runtime_id="codex_cli",
        provider_id="openrouter",
        expert_manual_credentials=(
            ExpertManualCredentialCapability(
                authentication_method="api_key",
                label="OpenRouter API key (expert)",
                credential_source="secret_ref",
                runtime_materialization_mode="composite",
                required_secret_roles=("provider_api_key",),
                launch_validator=lambda profile: _expert_composite_launch_contract_valid(
                    profile,
                    secret_role="provider_api_key",
                    secret_env_key="OPENROUTER_API_KEY",
                ),
            ),
        ),
    ),
)


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
    if strategy is not None:
        return (strategy.secret_role,)
    capability = _runtime_provider_authentication_capability(
        runtime_id.strip(),
        provider_id.strip(),
    )
    if capability is None:
        return ()
    return tuple(
        dict.fromkeys(
            role
            for method in capability.expert_manual_credentials
            for role in method.required_secret_roles
        )
    )


def _runtime_provider_authentication_capability(
    runtime_id: str,
    provider_id: str,
) -> RuntimeProviderAuthenticationCapability | None:
    for capability in _RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES:
        if (
            capability.runtime_id == runtime_id
            and capability.provider_id == provider_id
        ):
            return capability
    return None


def credential_free_authentication_supported(
    runtime_id: str,
    provider_id: str,
) -> bool:
    """Return whether trusted runtime metadata authorizes credential-free setup."""

    capability = _runtime_provider_authentication_capability(
        runtime_id.strip(),
        provider_id.strip(),
    )
    return bool(capability and capability.credential_free)


def _locked_field(value: object, source: str, lock_reason: str) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "editable": False,
        "lock_reason": lock_reason,
    }


def _editable_field(value: object) -> dict[str, Any]:
    return {
        "value": value,
        "source": "runtime_provider_capability",
        "editable": True,
        "lock_reason": "Backend capability permits this expert manual override.",
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


def _expert_manual_method(
    capability: ExpertManualCredentialCapability,
) -> dict[str, Any]:
    if capability.authentication_method not in {"oauth", "api_key", "none"}:
        raise ValueError(
            "Expert manual credential capability has an unsupported "
            f"authentication method: {capability.authentication_method!r}."
        )
    return {
        "id": capability.authentication_method,
        "label": capability.label,
        "setup_action": capability.authentication_method,
        "launch_ready_after_setup": False,
        "fields": {
            "credential_source": _editable_field(capability.credential_source),
            "runtime_materialization_mode": _editable_field(
                capability.runtime_materialization_mode
            ),
        },
        "secret_roles": [
            {
                "role": role,
                "label": role.replace("_", " ").title(),
                "required": True,
                "compatible_schemes": ["db", "env"],
            }
            for role in capability.required_secret_roles
        ],
        "imported_volume": {
            "supported": False,
            "mount_path": None,
            "source": "runtime_provider_capability",
            "lock_reason": (
                "This expert manual credential contract does not import a volume."
            ),
        },
    }


def provider_profile_creation_capabilities(
    *,
    runtime_id: str,
    provider_id: str,
) -> dict[str, Any]:
    """Return the coherent creation methods the backend explicitly supports."""

    runtime_id = runtime_id.strip()
    provider_id = provider_id.strip()
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

    independent_capability = _runtime_provider_authentication_capability(
        runtime_id,
        provider_id,
    )
    if independent_capability is not None and independent_capability.credential_free:
        methods.append(_no_credentials_method())
    if independent_capability is not None:
        for expert_capability in independent_capability.expert_manual_credentials:
            expert_method = _expert_manual_method(expert_capability)
            existing_index = next(
                (
                    index
                    for index, method in enumerate(methods)
                    if method["id"] == expert_capability.authentication_method
                ),
                None,
            )
            if existing_index is None:
                methods.append(expert_method)
            else:
                methods[existing_index] = expert_method

    diagnostics: list[str] = []
    if not methods:
        diagnostics.append(
            "No validated creation preset exists for this runtime and provider."
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
) -> dict[str, Any]:
    capabilities = provider_profile_creation_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
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
    authentication_methods: Iterable[object],
    auth_state: object = None,
    last_auth_method: object = None,
) -> str | None:
    """Infer a method only from an exact preset or explicit typed setup state."""

    source = str(getattr(credential_source, "value", credential_source) or "")
    materialization = str(
        getattr(runtime_materialization_mode, "value", runtime_materialization_mode)
        or ""
    )
    methods = [
        method for method in authentication_methods if isinstance(method, Mapping)
    ]
    method_ids = {str(method.get("id") or "") for method in methods}
    for method in methods:
        fields = method.get("fields")
        if not isinstance(fields, Mapping):
            continue
        source_field = fields.get("credential_source")
        materialization_field = fields.get("runtime_materialization_mode")
        if not isinstance(source_field, Mapping) or not isinstance(
            materialization_field, Mapping
        ):
            continue
        if (
            str(source_field.get("value") or "") == source
            and str(materialization_field.get("value") or "") == materialization
        ):
            return str(method.get("id") or "") or None

    normalized_state = str(getattr(auth_state, "value", auth_state) or "")
    if (
        normalized_state == "oauth_pending"
        and source == "none"
        and materialization in {"api_key_env", "oauth_home"}
        and "oauth" in method_ids
    ):
        return "oauth"
    if normalized_state == "api_key_pending" and "api_key" in method_ids:
        return "api_key"

    normalized_last_method = str(
        getattr(last_auth_method, "value", last_auth_method) or ""
    )
    if (
        normalized_state == "disconnected"
        and normalized_last_method == "oauth_volume"
        and source == "none"
        and materialization in {"api_key_env", "oauth_home"}
        and "oauth" in method_ids
    ):
        return "oauth"
    return None


def authentication_method_launch_ready_after_setup(
    *,
    authentication_method: str,
    authentication_methods: Iterable[object],
) -> bool:
    """Return the trusted capability's post-setup launch-readiness decision."""

    for method in authentication_methods:
        if not isinstance(method, Mapping):
            continue
        if str(method.get("id") or "") != authentication_method:
            continue
        return method.get("launch_ready_after_setup") is True
    return False


def expert_manual_credential_launch_ready(
    *,
    runtime_id: object,
    provider_id: object,
    credential_source: object,
    runtime_materialization_mode: object,
    secret_refs: object,
    env_template: object,
    file_templates: object,
    home_path_overrides: object,
    command_behavior: object,
) -> bool:
    """Validate one exact expert contract through backend-owned provider logic."""

    capability = _runtime_provider_authentication_capability(
        str(runtime_id or "").strip(),
        str(provider_id or "").strip(),
    )
    if capability is None:
        return False
    source = str(getattr(credential_source, "value", credential_source) or "")
    materialization = str(
        getattr(
            runtime_materialization_mode,
            "value",
            runtime_materialization_mode,
        )
        or ""
    )
    refs = secret_refs if isinstance(secret_refs, Mapping) else {}
    for method in capability.expert_manual_credentials:
        if (
            method.credential_source != source
            or method.runtime_materialization_mode != materialization
        ):
            continue
        if method.launch_validator is None:
            return False
        if any(not refs.get(role) for role in method.required_secret_roles):
            return False
        return bool(
            method.launch_validator(
                {
                    "secret_refs": refs,
                    "env_template": env_template,
                    "file_templates": file_templates,
                    "home_path_overrides": home_path_overrides,
                    "command_behavior": command_behavior,
                }
            )
        )
    return False


def validate_manual_credential_contract(
    *,
    credential_source: object,
    runtime_materialization_mode: object,
    authentication_methods: Iterable[object],
) -> None:
    """Require an exact capability match whose low-level fields are editable."""

    validate_credential_contract(
        credential_source=credential_source,
        runtime_materialization_mode=runtime_materialization_mode,
    )
    source = str(getattr(credential_source, "value", credential_source) or "")
    materialization = str(
        getattr(runtime_materialization_mode, "value", runtime_materialization_mode)
        or ""
    )
    for method in authentication_methods:
        if not isinstance(method, Mapping):
            continue
        fields = method.get("fields")
        if not isinstance(fields, Mapping):
            continue
        source_field = fields.get("credential_source")
        materialization_field = fields.get("runtime_materialization_mode")
        if not isinstance(source_field, Mapping) or not isinstance(
            materialization_field, Mapping
        ):
            continue
        if (
            str(source_field.get("value") or "") != source
            or str(materialization_field.get("value") or "") != materialization
        ):
            continue
        if bool(source_field.get("editable")) and bool(
            materialization_field.get("editable")
        ):
            return
        raise ValueError(
            "This runtime and provider does not advertise expert manual "
            "credential overrides; choose the supported authentication method."
        )
    raise ValueError(
        "Credential contract does not match a supported authentication preset."
    )


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
    "ExpertManualCredentialCapability",
    "ProviderApiKeyStrategy",
    "RuntimeProviderAuthenticationCapability",
    "authentication_method_launch_ready_after_setup",
    "authentication_method_preset",
    "credential_free_authentication_supported",
    "expert_manual_credential_launch_ready",
    "infer_authentication_method",
    "provider_api_key_strategy",
    "provider_profile_creation_capabilities",
    "required_secret_roles",
    "validate_credential_contract",
    "validate_manual_credential_contract",
]
