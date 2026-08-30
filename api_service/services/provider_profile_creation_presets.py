"""Backend-owned Provider Profile creation presets.

The preset catalog is the create-time policy authority for guided Provider
Profile creation.  It deliberately contains no persistence or HTTP behavior so
the same immutable definition drives both preview and atomic create
normalization.

MoonLadderStudios/MoonMind#3819
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ProviderProfileAuthenticationMethod(str, Enum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class CreationPresetField:
    value: Any
    source: str
    editable: bool
    required: bool = False
    lock_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": copy.deepcopy(self.value),
            "source": self.source,
            "editable": self.editable,
            "required": self.required,
            "lock_reason": self.lock_reason,
        }


@dataclass(frozen=True, slots=True)
class CreationPresetDiagnostic:
    code: str
    severity: str
    message: str
    field: str | None = None
    action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
            "action": self.action,
        }


@dataclass(frozen=True, slots=True)
class ProviderProfileCreationPreset:
    runtime_id: str
    provider_id: str
    authentication_method: ProviderProfileAuthenticationMethod
    supported: bool
    fields: Mapping[str, CreationPresetField]
    diagnostics: tuple[CreationPresetDiagnostic, ...] = ()
    manual_creation_allowed: bool = False
    required_manual_fields: tuple[str, ...] = ()

    @property
    def version(self) -> str:
        canonical = {
            "runtime_id": self.runtime_id,
            "provider_id": self.provider_id,
            "authentication_method": self.authentication_method.value,
            "supported": self.supported,
            "fields": {
                name: field.as_dict() for name, field in sorted(self.fields.items())
            },
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "manual_creation_allowed": self.manual_creation_allowed,
            "required_manual_fields": list(self.required_manual_fields),
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"provider-profile-create-v1-{digest[:20]}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "supported": self.supported,
            "runtime_id": self.runtime_id,
            "provider_id": self.provider_id,
            "authentication_method": self.authentication_method.value,
            "fields": {
                name: field.as_dict() for name, field in self.fields.items()
            },
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "manual_creation_allowed": self.manual_creation_allowed,
            "required_manual_fields": list(self.required_manual_fields),
        }


class CreationPresetError(ValueError):
    """Base error for deterministic creation-preset conflicts."""

    code = "provider_profile_creation_preset_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


class UnsupportedCreationPresetError(CreationPresetError):
    code = "provider_profile_creation_preset_unsupported"


class CreationPresetVersionRequiredError(CreationPresetError):
    code = "provider_profile_creation_preset_version_required"


class CreationPresetVersionMismatchError(CreationPresetError):
    code = "provider_profile_creation_preset_version_mismatch"


class CreationPresetFieldLockedError(CreationPresetError):
    code = "provider_profile_creation_preset_field_locked"


_PERSISTED_PRESET_FIELDS = frozenset(
    {
        "credential_source",
        "runtime_materialization_mode",
        "volume_ref",
        "volume_mount_path",
        "tags",
        "priority",
        "secret_refs",
        "clear_env_keys",
        "env_template",
        "file_templates",
        "home_path_overrides",
        "command_behavior",
        "max_parallel_runs",
        "cooldown_after_429_seconds",
        "rate_limit_policy",
        "enabled",
        "is_default",
        "max_lease_duration_seconds",
        "auth_state",
        "disabled_reason",
        "first_authenticated_at",
        "last_validated_at",
        "last_auth_method",
    }
)


def _field(
    value: Any,
    source: str,
    *,
    editable: bool,
    required: bool = False,
    lock_reason: str | None = None,
) -> CreationPresetField:
    if not editable and lock_reason is None:
        lock_reason = "This value is controlled by the runtime/provider strategy."
    return CreationPresetField(
        value=value,
        source=source,
        editable=editable,
        required=required,
        lock_reason=lock_reason,
    )


def _missing_credentials_diagnostic(
    authentication_method: ProviderProfileAuthenticationMethod,
) -> CreationPresetDiagnostic:
    label = (
        "OAuth"
        if authentication_method == ProviderProfileAuthenticationMethod.OAUTH
        else "API-key"
    )
    return CreationPresetDiagnostic(
        code="credential_setup_required",
        severity="warning",
        message=(
            f"The profile will remain disabled and non-launch-ready until {label} "
            "credential setup succeeds."
        ),
        field="authentication_method",
        action=(
            "connect_oauth"
            if authentication_method == ProviderProfileAuthenticationMethod.OAUTH
            else "add_api_key"
        ),
    )


def _common_fields(
    *,
    authentication_method: ProviderProfileAuthenticationMethod,
    runtime_materialization_mode: str,
    system_tags: list[str],
    secret_ref_roles: list[str],
    clear_env_keys: list[str],
    env_template: dict[str, Any] | None = None,
    command_behavior: dict[str, Any] | None = None,
    max_parallel_runs: int = 1,
    max_parallel_runs_editable: bool = True,
    volume_mount_path_after_setup: str | None = None,
) -> dict[str, CreationPresetField]:
    credential_lock_reason = (
        "Credential source remains none until validated credential setup installs "
        "the source selected by the authentication method."
    )
    materialization_lock_reason = (
        "Materialization is fixed by the selected "
        "runtime/provider/authentication strategy."
    )
    fields = {
        "credential_source": _field(
            "none",
            "credential_activation_policy",
            editable=False,
            required=True,
            lock_reason=credential_lock_reason,
        ),
        "runtime_materialization_mode": _field(
            runtime_materialization_mode,
            "runtime_provider_authentication_strategy",
            editable=False,
            required=True,
            lock_reason=materialization_lock_reason,
        ),
        "max_parallel_runs": _field(
            max_parallel_runs,
            (
                "exclusive_credential_identity"
                if not max_parallel_runs_editable
                else "runtime_capacity_baseline"
            ),
            editable=max_parallel_runs_editable,
            required=True,
            lock_reason=(
                "This OAuth home is an exclusive mutable identity and supports "
                "one run at a time."
                if not max_parallel_runs_editable
                else None
            ),
        ),
        "cooldown_after_429_seconds": _field(
            300, "provider_rate_limit_default", editable=True, required=True
        ),
        "rate_limit_policy": _field(
            "backoff", "provider_rate_limit_default", editable=True, required=True
        ),
        "priority": _field(100, "provider_profile_default", editable=True),
        "user_tags": _field([], "user_input", editable=True),
        "system_tags": _field(
            system_tags,
            "runtime_provider_authentication_strategy",
            editable=False,
            lock_reason="System tags describe the selected backend strategy.",
        ),
        "tags": _field(
            system_tags,
            "system_tags_plus_user_tags",
            editable=True,
        ),
        "volume_ref": _field(
            None,
            "credential_enrollment",
            editable=False,
            required=authentication_method == ProviderProfileAuthenticationMethod.OAUTH,
            lock_reason=(
                "OAuth enrollment creates and owns the credential volume reference."
            ),
        ),
        "volume_mount_path": _field(
            None,
            "credential_enrollment",
            editable=False,
            required=authentication_method == ProviderProfileAuthenticationMethod.OAUTH,
            lock_reason=(
                "OAuth enrollment applies the runtime-owned credential mount path."
            ),
        ),
        "volume_mount_path_after_setup": _field(
            volume_mount_path_after_setup,
            "runtime_provider_authentication_strategy",
            editable=False,
        ),
        "secret_refs": _field(
            {},
            "credential_enrollment",
            editable=(
                authentication_method == ProviderProfileAuthenticationMethod.API_KEY
            ),
            required=bool(secret_ref_roles),
            lock_reason=(
                None
                if authentication_method == ProviderProfileAuthenticationMethod.API_KEY
                else (
                    "The selected authentication strategy does not accept manual "
                    "SecretRef bindings."
                )
            ),
        ),
        "secret_ref_roles": _field(
            secret_ref_roles,
            "runtime_provider_authentication_strategy",
            editable=False,
            required=bool(secret_ref_roles),
            lock_reason="SecretRef roles are named by the runtime/provider strategy.",
        ),
        "clear_env_keys": _field(
            clear_env_keys,
            "runtime_provider_isolation_policy",
            editable=False,
            required=True,
            lock_reason="Environment clearing is backend-owned launch security policy.",
        ),
        "clear_env_keys_strategy": _field(
            "runtime_provider_isolation_policy",
            "runtime_provider_isolation_policy",
            editable=False,
        ),
        "env_template": _field(
            env_template or {},
            "runtime_provider_authentication_strategy",
            editable=False,
        ),
        "file_templates": _field(
            [], "runtime_provider_authentication_strategy", editable=False
        ),
        "home_path_overrides": _field(
            {}, "runtime_provider_authentication_strategy", editable=False
        ),
        "command_behavior": _field(
            command_behavior or {},
            "runtime_provider_authentication_strategy",
            editable=False,
        ),
        "enabled": _field(
            False,
            "credential_activation_policy",
            editable=False,
            required=True,
            lock_reason=(
                "Creation cannot enable a profile before credential validation "
                "succeeds."
            ),
        ),
        "auth_state": _field(
            "not_configured",
            "credential_activation_policy",
            editable=False,
            required=True,
        ),
        "disabled_reason": _field(
            "missing_credentials",
            "credential_activation_policy",
            editable=False,
            required=True,
        ),
        "last_auth_method": _field(
            None, "credential_activation_policy", editable=False
        ),
        "first_authenticated_at": _field(
            None,
            "credential_activation_policy",
            editable=False,
            lock_reason=(
                "Authentication history is recorded only after validated "
                "credential setup."
            ),
        ),
        "last_validated_at": _field(
            None,
            "credential_activation_policy",
            editable=False,
            lock_reason=(
                "Credential validation history is recorded only by a validation "
                "or finalization endpoint."
            ),
        ),
        "is_default": _field(False, "user_intent_after_readiness", editable=True),
        "may_become_runtime_default": _field(
            True,
            "runtime_default_readiness_policy",
            editable=False,
            lock_reason=(
                "Default assignment is applied only after launch readiness succeeds."
            ),
        ),
        "max_lease_duration_seconds": _field(
            7200, "runtime_lease_default", editable=True, required=True
        ),
    }
    return fields


def _oauth_preset(
    *, runtime_id: str, provider_id: str, mount_path: str, clear_env_keys: list[str]
) -> ProviderProfileCreationPreset:
    command_behavior = {
        "supported_auth_methods": ["oauth_volume"],
        "auth_actions": ["connect_oauth"],
        "auth_strategy": "oauth_volume",
        "auth_state": "not_configured",
        "auth_status_label": "Not connected",
        "auth_readiness": {
            "connected": False,
            "backing_secret_exists": False,
            "launch_ready": False,
            "failure_reason": "OAuth setup has not completed.",
        },
    }
    return ProviderProfileCreationPreset(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method=ProviderProfileAuthenticationMethod.OAUTH,
        supported=True,
        fields=_common_fields(
            authentication_method=ProviderProfileAuthenticationMethod.OAUTH,
            runtime_materialization_mode="oauth_home",
            system_tags=["oauth", "first-party"],
            secret_ref_roles=[],
            clear_env_keys=clear_env_keys,
            command_behavior=command_behavior,
            max_parallel_runs=1,
            max_parallel_runs_editable=False,
            volume_mount_path_after_setup=mount_path,
        ),
        diagnostics=(
            _missing_credentials_diagnostic(
                ProviderProfileAuthenticationMethod.OAUTH
            ),
        ),
    )


def _api_key_preset(
    *,
    runtime_id: str,
    provider_id: str,
    materialization_mode: str,
    secret_role: str,
    clear_env_keys: list[str],
    env_template: dict[str, Any],
    auth_strategy: str,
    system_tags: list[str] | None = None,
) -> ProviderProfileCreationPreset:
    command_behavior = {
        "supported_auth_methods": ["secret_ref"],
        "auth_actions": ["use_api_key"],
        "auth_strategy": auth_strategy,
        "auth_state": "not_configured",
        "auth_status_label": "Not connected",
        "auth_readiness": {
            "connected": False,
            "backing_secret_exists": False,
            "launch_ready": False,
            "failure_reason": "API-key setup has not completed.",
        },
    }
    return ProviderProfileCreationPreset(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method=ProviderProfileAuthenticationMethod.API_KEY,
        supported=True,
        fields=_common_fields(
            authentication_method=ProviderProfileAuthenticationMethod.API_KEY,
            runtime_materialization_mode=materialization_mode,
            system_tags=system_tags or ["api-key", "first-party"],
            secret_ref_roles=[secret_role],
            clear_env_keys=clear_env_keys,
            env_template=env_template,
            command_behavior=command_behavior,
        ),
        diagnostics=(
            _missing_credentials_diagnostic(
                ProviderProfileAuthenticationMethod.API_KEY
            ),
        ),
    )


def _supported_presets() -> dict[
    tuple[str, str, ProviderProfileAuthenticationMethod],
    ProviderProfileCreationPreset,
]:
    presets = [
        _oauth_preset(
            runtime_id="codex_cli",
            provider_id="openai",
            mount_path="/home/app/.codex",
            clear_env_keys=[
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT",
                "MINIMAX_API_KEY",
            ],
        ),
        _oauth_preset(
            runtime_id="claude_code",
            provider_id="anthropic",
            mount_path="/home/app/.claude",
            clear_env_keys=[
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_API_KEY",
                "OPENAI_API_KEY",
            ],
        ),
        _api_key_preset(
            runtime_id="codex_cli",
            provider_id="openai",
            materialization_mode="api_key_env",
            secret_role="openai_api_key",
            clear_env_keys=[
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT",
                "MINIMAX_API_KEY",
            ],
            env_template={
                "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"}
            },
            auth_strategy="api_key_env",
        ),
        _api_key_preset(
            runtime_id="claude_code",
            provider_id="anthropic",
            materialization_mode="api_key_env",
            secret_role="anthropic_api_key",
            clear_env_keys=[
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_API_KEY",
                "OPENAI_API_KEY",
            ],
            env_template={
                "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}
            },
            auth_strategy="api_key_env",
        ),
        _api_key_preset(
            runtime_id="opencode",
            provider_id="opencode",
            materialization_mode="composite",
            secret_role="opencode_api_key",
            clear_env_keys=[
                "OPENCODE_AUTH_CONTENT",
                "OPENCODE_CONFIG",
                "OPENCODE_CONFIG_CONTENT",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
            ],
            env_template={},
            auth_strategy="opencode_auth_json",
            system_tags=["api-key", "opencode", "zen"],
        ),
        _api_key_preset(
            runtime_id="opencode",
            provider_id="opencode-go",
            materialization_mode="composite",
            secret_role="opencode_api_key",
            clear_env_keys=[
                "OPENCODE_AUTH_CONTENT",
                "OPENCODE_CONFIG",
                "OPENCODE_CONFIG_CONTENT",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
            ],
            env_template={},
            auth_strategy="opencode_auth_json",
            system_tags=["api-key", "opencode", "go"],
        ),
    ]
    return {
        (preset.runtime_id, preset.provider_id, preset.authentication_method): preset
        for preset in presets
    }


_SUPPORTED_PRESETS = _supported_presets()


def get_provider_profile_creation_preset(
    *,
    runtime_id: str,
    provider_id: str,
    authentication_method: ProviderProfileAuthenticationMethod | str,
) -> ProviderProfileCreationPreset:
    normalized_runtime_id = str(runtime_id or "").strip()
    normalized_provider_id = str(provider_id or "").strip()
    method = ProviderProfileAuthenticationMethod(authentication_method)
    preset = _SUPPORTED_PRESETS.get(
        (normalized_runtime_id, normalized_provider_id, method)
    )
    if preset is not None:
        return preset

    return ProviderProfileCreationPreset(
        runtime_id=normalized_runtime_id,
        provider_id=normalized_provider_id,
        authentication_method=method,
        supported=False,
        fields={},
        diagnostics=(
            CreationPresetDiagnostic(
                code="no_safe_standard_creation_preset",
                severity="error",
                message=(
                    "No validated standard creation preset exists for this runtime, "
                    "provider, and authentication method. Use the authorized manual "
                    "profile path and supply every required launch field."
                ),
                action="open_manual_profile",
            ),
        ),
        manual_creation_allowed=True,
        required_manual_fields=(
            "credential_source",
            "runtime_materialization_mode",
            "clear_env_keys",
            "command_behavior",
        ),
    )


def apply_provider_profile_creation_preset(
    *,
    preset: ProviderProfileCreationPreset,
    requested_version: str | None,
    values: Mapping[str, Any],
    supplied_fields: set[str],
) -> dict[str, Any]:
    """Apply one preset and explicit supported overrides to create values.

    Validation completes before a SQLAlchemy row is constructed, which keeps
    stale, unsupported, and locked-field requests outside the persistence
    transaction.
    """

    if not preset.supported:
        raise UnsupportedCreationPresetError(
            "No safe standard creation preset exists for this combination.",
            runtime_id=preset.runtime_id,
            provider_id=preset.provider_id,
            authentication_method=preset.authentication_method.value,
            diagnostics=[item.as_dict() for item in preset.diagnostics],
            manual_creation_allowed=preset.manual_creation_allowed,
            required_manual_fields=list(preset.required_manual_fields),
        )
    if not requested_version:
        raise CreationPresetVersionRequiredError(
            "preset_version is required for standard Provider Profile creation.",
            current_version=preset.version,
        )
    if requested_version != preset.version:
        raise CreationPresetVersionMismatchError(
            "The Provider Profile creation preset changed; reload and review the "
            "current policy.",
            requested_version=requested_version,
            current_version=preset.version,
        )

    normalized = dict(values)
    # Persist the exact canonical identity that selected this preset.  Requests
    # with surrounding whitespace must not validate against one tuple and then
    # create an unroutable profile under a different identity.
    normalized["runtime_id"] = preset.runtime_id
    normalized["provider_id"] = preset.provider_id
    for field_name in _PERSISTED_PRESET_FIELDS:
        field = preset.fields.get(field_name)
        if field is None:
            continue
        if field_name in supplied_fields:
            requested_value = values.get(field_name)
            if not field.editable and requested_value != field.value:
                raise CreationPresetFieldLockedError(
                    f"{field_name} is locked by the selected creation preset.",
                    field=field_name,
                    lock_reason=field.lock_reason,
                    expected_value=copy.deepcopy(field.value),
                )
            normalized[field_name] = copy.deepcopy(requested_value)
        else:
            normalized[field_name] = copy.deepcopy(field.value)

    # ``tags`` is the persisted union of backend-owned system tags and optional
    # user tags.  Explicit user input cannot erase strategy identity.
    system_tags = list(preset.fields["system_tags"].value)
    requested_tags = values.get("tags") if "tags" in supplied_fields else []
    normalized["tags"] = list(
        dict.fromkeys([*system_tags, *(requested_tags or [])])
    )
    return normalized


__all__ = [
    "CreationPresetError",
    "CreationPresetFieldLockedError",
    "CreationPresetVersionMismatchError",
    "CreationPresetVersionRequiredError",
    "ProviderProfileAuthenticationMethod",
    "ProviderProfileCreationPreset",
    "UnsupportedCreationPresetError",
    "apply_provider_profile_creation_preset",
    "get_provider_profile_creation_preset",
]
