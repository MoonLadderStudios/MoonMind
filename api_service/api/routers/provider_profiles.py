"""CRUD API for managed agent provider profiles."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.models import (
    ManagedAgentProviderProfile,
    ManagedAgentRateLimitPolicy,
    ManagedSecret,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
    SecretStatus,
    User,
)
from api_service.services.provider_profile_creation import (
    ProviderApiKeyStrategy,
    authentication_method_launch_ready_after_setup,
    expert_manual_credential_launch_ready,
    infer_authentication_method,
    provider_api_key_strategy,
    provider_profile_creation_capabilities,
    required_secret_roles,
    validate_credential_contract,
    validate_manual_credential_contract,
)
from api_service.services.provider_profile_creation_presets import (
    CreationPresetError,
    ProviderProfileAuthenticationMethod,
    apply_provider_profile_creation_preset,
    get_provider_profile_creation_preset,
)
from api_service.services.settings_catalog import has_settings_permission
from moonmind.auth.secret_refs import (
    ParsedSecretRef,
    SecretBackend,
    SecretReferenceError,
    parse_secret_ref,
)
from moonmind.provider_profiles.model_tiers import (
    ProviderModelEffortTier,
    coerce_model_effort_tier_policy,
    is_single_legacy_default_model_effort_tier,
    is_single_runtime_default_model_effort_tier,
)
from moonmind.provider_profiles.oauth_policy import (
    CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR,
    is_codex_oauth_profile,
)
from moonmind.schemas.agent_runtime_models import validate_codex_oauth_profile_refs
from moonmind.utils.logging import (
    redact_profile_file_templates,
    redact_sensitive_payload,
)
from moonmind.workflows.executions.model_resolver import (
    RequestedModelTierUnavailableError,
    provider_profile_version,
    resolve_model_effort,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-profiles", tags=["provider-profiles"])
_claude_manual_validation_client: httpx.AsyncClient | None = None


async def _credential_maintenance_guard(
    *,
    profile_id: str,
    purpose: str,
    request: Request,
    session: AsyncSession,
    current_user: User,
) -> AsyncIterator[object]:
    """Hold the shared credential lane through an HTTP maintenance action."""

    from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
    from moonmind.provider_profiles.maintenance import (
        acquire_credential_maintenance_guard,
        drain_profile_bound_hosts,
    )

    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)
    operation_id = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Request-ID")
        or uuid4().hex
    )
    guard = await acquire_credential_maintenance_guard(
        runtime_id=profile.runtime_id,
        profile_id=profile.profile_id,
        purpose=CredentialLeasePurpose(purpose),
        operation_id=operation_id,
        metadata={
            "workflowId": f"http:{operation_id}",
            "ownerIsWorkflow": False,
        },
    )
    try:
        await drain_profile_bound_hosts(
            profile_id=profile.profile_id,
            operation_id=operation_id,
        )
        yield guard
    finally:
        await guard.release()


@dataclass(frozen=True, slots=True)
class _SecretRefParseResult:
    parsed: ParsedSecretRef | None = None
    error: str | None = None


def validate_secret_refs_helper(value: dict[str, str] | None) -> dict[str, str] | None:
    if not value:
        return value
    for k, v in value.items():
        if not v:
            continue
        try:
            parse_secret_ref(v)
        except SecretReferenceError as e:
            raise ValueError(f"Invalid secret reference {v!r} for key {k!r}: {e}")
    return value


def _default_model_tiers() -> list[dict[str, Any]]:
    return [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        }
    ]


def _validate_model_tiers_value(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("model_tiers must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    for tier in value:
        if not isinstance(tier, dict):
            raise ValueError("model_tiers entries must be mappings")
        normalized.append(
            ProviderModelEffortTier.model_validate(tier).model_dump(mode="json")
        )
    return normalized


def _validate_default_model_tier_value(
    default_model_tier: int,
    model_tiers: list[dict[str, Any]],
) -> int:
    if isinstance(default_model_tier, bool) or not isinstance(default_model_tier, int):
        raise ValueError(
            "default_model_tier must be an integer greater than or equal to 1"
        )
    if default_model_tier < 1:
        raise ValueError(
            "default_model_tier must be an integer greater than or equal to 1"
        )
    if default_model_tier > len(model_tiers):
        raise ValueError(
            "default_model_tier must be within configured model_tiers range"
        )
    return default_model_tier


def _validate_default_model_tier_input(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "default_model_tier must be an integer greater than or equal to 1"
        )
    if value < 1:
        raise ValueError(
            "default_model_tier must be an integer greater than or equal to 1"
        )
    return value


def _validate_profile_tier_policy(row: ManagedAgentProviderProfile) -> None:
    model_tiers = _validate_model_tiers_value(row.model_tiers)
    default_model_tier = _validate_default_model_tier_value(
        row.default_model_tier,
        model_tiers,
    )
    row.model_tiers = model_tiers
    row.default_model_tier = default_model_tier


class ProviderProfileCreate(BaseModel):
    profile_id: str = Field(..., max_length=128)
    runtime_id: str = Field(..., max_length=64)
    provider_id: str = Field(default="unknown", max_length=64)
    provider_label: Optional[str] = None
    default_model: Optional[str] = None
    default_effort: Optional[str] = Field(default=None, max_length=64)
    model_tiers: Optional[list[ProviderModelEffortTier]] = None
    default_model_tier: Optional[int] = Field(default=None, ge=1)
    model_overrides: Optional[dict[str, str]] = None

    authentication_method: Optional[ProviderProfileAuthenticationMethod] = None
    preset_version: Optional[str] = Field(default=None, max_length=128)
    credential_source: Optional[str] = Field(
        default=None, pattern="^(oauth_volume|secret_ref|none)$"
    )
    runtime_materialization_mode: Optional[str] = Field(
        default=None,
        pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$",
    )

    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None

    tags: Optional[list[str]] = None
    priority: Optional[int] = None

    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, Any]] = None
    file_templates: Optional[list[dict[str, Any]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None

    max_parallel_runs: Optional[int] = Field(default=None, ge=1)
    cooldown_after_429_seconds: Optional[int] = Field(default=None, ge=0)
    rate_limit_policy: Optional[str] = Field(
        default=None, pattern="^(backoff|queue|fail_fast)$"
    )
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    max_lease_duration_seconds: Optional[int] = Field(default=None, ge=60)
    auth_state: Optional[str] = Field(
        default=None,
        pattern="^(not_configured|oauth_pending|api_key_pending|connected|validation_failed|disconnected)$",
    )
    disabled_reason: Optional[str] = Field(
        default=None,
        pattern="^(missing_credentials|auth_invalid|user_disabled|policy_disabled|disconnected)$",
    )
    first_authenticated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    last_auth_method: Optional[str] = Field(
        default=None,
        pattern="^(oauth_volume|secret_ref|manual)$",
    )
    import_existing_credential_volume: bool = False

    @field_validator("env_template", mode="before")
    @classmethod
    def _stringify_runtime_env(cls, value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env_template must be a JSON object")
        return value

    @field_validator("secret_refs", mode="after")
    @classmethod
    def _validate_secret_refs(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        return validate_secret_refs_helper(value)

    @field_validator("model_tiers", mode="before")
    @classmethod
    def _validate_model_tiers(cls, value: object) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        return _validate_model_tiers_value(value)

    @field_validator("default_model_tier", mode="before")
    @classmethod
    def _validate_default_model_tier(cls, value: object) -> int | None:
        return _validate_default_model_tier_input(value)

    @model_validator(mode="after")
    def _validate_runtime_env(self) -> "ProviderProfileCreate":
        if self.model_tiers is not None and self.default_model_tier is not None:
            self.default_model_tier = _validate_default_model_tier_value(
                self.default_model_tier,
                self.model_tiers,
            )
        if self.authentication_method is None:
            if self.credential_source is None:
                raise ValueError(
                    "credential_source is required for an expert manual profile"
                )
            if self.runtime_materialization_mode is None:
                raise ValueError(
                    "runtime_materialization_mode is required for an expert manual profile"
                )
            validate_credential_contract(
                credential_source=self.credential_source,
                runtime_materialization_mode=self.runtime_materialization_mode,
            )
        if self.import_existing_credential_volume:
            if self.authentication_method != "oauth":
                raise ValueError(
                    "Imported credential volumes require authentication_method=oauth"
                )
            if not self.volume_ref:
                raise ValueError("volume_ref is required for imported credential volumes")
        if self.enabled and self.auth_state != ProviderProfileAuthState.CONNECTED.value:
            raise ValueError("enabled profiles require auth_state=connected")
        if self.enabled:
            self.disabled_reason = None
        if self.authentication_method is None:
            validate_codex_oauth_profile_refs(
                runtime_id=self.runtime_id,
                credential_source=self.credential_source,
                runtime_materialization_mode=self.runtime_materialization_mode,
                volume_ref=self.volume_ref,
                volume_mount_path=self.volume_mount_path,
                max_parallel_runs=self.max_parallel_runs,
                volume_ref_field_name="volume_ref",
                volume_mount_path_field_name="volume_mount_path",
            )
        return self


class ProviderProfileUpdate(BaseModel):
    provider_id: Optional[str] = Field(default=None, max_length=64)
    provider_label: Optional[str] = None
    default_model: Optional[str] = None
    default_effort: Optional[str] = Field(default=None, max_length=64)
    model_tiers: Optional[list[ProviderModelEffortTier]] = None
    default_model_tier: Optional[int] = Field(default=None, ge=1)
    model_overrides: Optional[dict[str, str]] = None
    credential_source: Optional[str] = Field(
        default=None, pattern="^(oauth_volume|secret_ref|none)$"
    )
    runtime_materialization_mode: Optional[str] = Field(
        default=None,
        pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$",
    )
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    tags: Optional[list[str]] = None
    priority: Optional[int] = None
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, Any]] = None
    file_templates: Optional[list[dict[str, Any]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None

    max_parallel_runs: Optional[int] = Field(default=None, ge=1)

    @field_validator("env_template", mode="before")
    @classmethod
    def _stringify_runtime_env_update(cls, value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env_template must be a JSON object")
        return value

    cooldown_after_429_seconds: Optional[int] = Field(default=None, ge=0)
    rate_limit_policy: Optional[str] = Field(
        default=None, pattern="^(backoff|queue|fail_fast)$"
    )
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    max_lease_duration_seconds: Optional[int] = Field(default=None, ge=60)
    auth_state: Optional[str] = Field(
        default=None,
        pattern="^(not_configured|oauth_pending|api_key_pending|connected|validation_failed|disconnected)$",
    )
    disabled_reason: Optional[str] = Field(
        default=None,
        pattern="^(missing_credentials|auth_invalid|user_disabled|policy_disabled|disconnected)$",
    )
    first_authenticated_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    last_auth_method: Optional[str] = Field(
        default=None,
        pattern="^(oauth_volume|secret_ref|manual)$",
    )
    import_existing_credential_volume: bool = False

    @field_validator("secret_refs", mode="after")
    @classmethod
    def _validate_secret_refs_update(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        return validate_secret_refs_helper(value)

    @field_validator("model_tiers", mode="before")
    @classmethod
    def _validate_model_tiers_update(cls, value: object) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        return _validate_model_tiers_value(value)

    @field_validator("default_model_tier", mode="before")
    @classmethod
    def _validate_default_model_tier_update(cls, value: object) -> int | None:
        return _validate_default_model_tier_input(value)

    @model_validator(mode="after")
    def _validate_runtime_env_update(self) -> "ProviderProfileUpdate":
        if self.model_tiers is not None and self.default_model_tier is not None:
            self.default_model_tier = _validate_default_model_tier_value(
                self.default_model_tier,
                self.model_tiers,
            )
        return self


class ProviderProfileCreationField(BaseModel):
    value: Any
    source: str
    editable: bool
    lock_reason: str


class ProviderProfileSecretRoleCapability(BaseModel):
    role: str
    label: str
    required: bool
    compatible_schemes: list[str]


class ProviderProfileImportedVolumeCapability(BaseModel):
    supported: bool
    mount_path: Optional[str]
    source: str
    lock_reason: str


class ProviderProfileAuthenticationMethodCapability(BaseModel):
    id: str = Field(..., pattern="^(oauth|api_key|none)$")
    label: str
    setup_action: str = Field(..., pattern="^(oauth|api_key|none)$")
    launch_ready_after_setup: bool
    fields: dict[str, ProviderProfileCreationField]
    secret_roles: list[ProviderProfileSecretRoleCapability]
    imported_volume: ProviderProfileImportedVolumeCapability


class ProviderProfileCreationCapabilitiesResponse(BaseModel):
    version: str
    runtime_id: str
    provider_id: str
    supported: bool
    authentication_methods: list[ProviderProfileAuthenticationMethodCapability]
    diagnostics: list[str]


class ProviderProfileTierCapabilitiesEvidence(BaseModel):
    source: str
    credential_generation: Optional[int] = None
    image_ref: Optional[str] = None
    observed_at: Optional[str] = None
    stale: bool


class ProviderProfileTierCapabilitiesModelOption(BaseModel):
    value: str
    label: str
    description: Optional[str] = None
    status: str = Field(..., pattern="^(available|deprecated|unavailable)$")
    recommended: bool = False


class ProviderProfileTierCapabilitiesEffortOption(BaseModel):
    value: str
    label: str
    description: Optional[str] = None
    status: str = Field(..., pattern="^(available|deprecated|unavailable)$")
    compatible_models: Optional[list[str]] = None


class ProviderProfileTierCapabilitiesTierConstraints(BaseModel):
    min_count: int
    max_count: Optional[int] = None


class ProviderProfileTierCapabilitiesModel(BaseModel):
    runtime_default: Optional[str] = None
    allow_custom: bool
    options: list[ProviderProfileTierCapabilitiesModelOption]


class ProviderProfileTierCapabilitiesEffort(BaseModel):
    supported: bool
    runtime_default: Optional[str] = None
    allow_custom: bool
    application: str
    options: list[ProviderProfileTierCapabilitiesEffortOption]


class ProviderProfileTierCapabilitiesResponse(BaseModel):
    version: str
    profile_id: Optional[str] = None
    runtime_id: str
    provider_id: str
    evidence: ProviderProfileTierCapabilitiesEvidence
    tier_constraints: ProviderProfileTierCapabilitiesTierConstraints
    model: ProviderProfileTierCapabilitiesModel
    effort: ProviderProfileTierCapabilitiesEffort
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class ProviderProfileResponse(BaseModel):
    profile_id: str
    runtime_id: str
    provider_id: str
    provider_label: Optional[str]
    default_model: Optional[str] = None
    default_effort: Optional[str] = None
    model_tiers: list[ProviderModelEffortTier]
    default_model_tier: int
    model_overrides: dict[str, str] = Field(default_factory=dict)
    credential_generation: int = 1
    capacity_scope_ref: str
    model_catalog_evidence: Optional[dict[str, Any]] = None
    credential_source: str
    runtime_materialization_mode: str
    volume_ref: Optional[str]
    volume_mount_path: Optional[str]
    account_label: Optional[str]
    tags: Optional[list[str]] = None
    priority: int = 100
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, Any]] = None
    file_templates: Optional[list[dict[str, Any]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None
    max_parallel_runs: int
    cooldown_after_429_seconds: int
    rate_limit_policy: str
    enabled: bool
    is_default: bool
    max_lease_duration_seconds: int
    auth_state: str
    disabled_reason: Optional[str]
    first_authenticated_at: Optional[str]
    last_validated_at: Optional[str]
    last_auth_method: Optional[str]
    launch_ready: bool
    readiness: "ProviderProfileReadiness"
    authentication_method: Optional[str] = None
    creation_capabilities: ProviderProfileCreationCapabilitiesResponse
    created_at: Optional[str]
    updated_at: Optional[str]

    model_config = {"from_attributes": True}


class ProviderProfileCreationPresetField(BaseModel):
    value: Any
    source: str
    editable: bool
    required: bool
    lock_reason: Optional[str] = None


class ProviderProfileCreationPresetDiagnostic(BaseModel):
    code: str
    severity: str = Field(..., pattern="^(info|warning|error)$")
    message: str
    field: Optional[str] = None
    action: Optional[str] = None


class ProviderProfileCreationPresetResponse(BaseModel):
    version: str
    supported: bool
    runtime_id: str
    provider_id: str
    authentication_method: ProviderProfileAuthenticationMethod
    fields: dict[str, ProviderProfileCreationPresetField]
    diagnostics: list[ProviderProfileCreationPresetDiagnostic]
    manual_creation_allowed: bool = False
    required_manual_fields: list[str] = Field(default_factory=list)


class ProviderProfileTierPreviewStep(BaseModel):
    step_id: str = Field(..., alias="id")
    model_tier: Optional[int] = Field(default=None, ge=1, alias="modelTier")
    tier_fallback: Optional[str] = Field(
        default="clamp",
        pattern="^(clamp|strict)$",
        alias="tierFallback",
    )

    model_config = {"populate_by_name": True}


class ProviderProfileTierPreviewRequest(BaseModel):
    steps: list[ProviderProfileTierPreviewStep] = Field(default_factory=list)


class ProviderProfileTierPreviewItem(BaseModel):
    step_id: str = Field(..., alias="stepId")
    requested_tier: Optional[int] = Field(default=None, alias="requestedTier")
    effective_tier: Optional[int] = Field(default=None, alias="effectiveTier")
    model: Optional[str] = None
    effort: Optional[str] = None
    fallback_reason: Optional[str] = Field(default=None, alias="fallbackReason")

    model_config = {"populate_by_name": True}


class ProviderProfileTierPreviewResponse(BaseModel):
    profile_id: str = Field(..., alias="profileId")
    profile_version: int | str = Field(..., alias="profileVersion")
    advisory: bool = True
    items: list[ProviderProfileTierPreviewItem]

    model_config = {"populate_by_name": True}


class ProviderReadinessCheck(BaseModel):
    id: str
    label: str
    status: str = Field(..., pattern="^(pass|warning|error)$")
    message: str


class ProviderProfileReadiness(BaseModel):
    status: str = Field(..., pattern="^(ready|warning|blocked)$")
    launch_ready: bool
    summary: str
    checks: list[ProviderReadinessCheck]


class ClaudeManualAuthCommitRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=8192)
    account_label: Optional[str] = None


class ProviderApiKeySetupRequest(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=8192)
    account_label: Optional[str] = None
    make_default: bool = False
    enable_after_validation: bool = True


class ClaudeManualAuthReadiness(BaseModel):
    connected: bool
    last_validated_at: str
    backing_secret_exists: bool
    launch_ready: bool
    failure_reason: Optional[str] = None


class ClaudeManualAuthCommitResponse(BaseModel):
    status: str
    status_label: str
    readiness: ClaudeManualAuthReadiness
    profile_id: str
    secret_ref: str


class ProviderApiKeySetupResponse(BaseModel):
    status: str
    status_label: str
    readiness: ClaudeManualAuthReadiness
    profile_id: str
    secret_ref: str


class ProviderCredentialVolumeImportRequest(BaseModel):
    runtime_id: str = Field(..., max_length=64)
    provider_id: str = Field(..., max_length=64)
    volume_ref: str = Field(..., min_length=1, max_length=255)


class ProviderCredentialVolumeImportResponse(BaseModel):
    status: str = Field(..., pattern="^validated$")
    volume_ref: str
    volume_mount_path: str
    source: str = Field(default="validated_import")


# ---------------------------------------------------------------------------
# Dependency: DB session
# ---------------------------------------------------------------------------


def _get_session() -> Any:
    """Return the session dependency. Resolved at import-time from the app."""
    from api_service.db.base import get_async_session

    return get_async_session


def _normalized_create_values(body: ProviderProfileCreate) -> dict[str, Any]:
    values = body.model_dump(
        exclude={"authentication_method", "preset_version"},
        mode="python",
    )
    supplied_fields = set(body.model_fields_set)
    supplied_fields.difference_update({"authentication_method", "preset_version"})

    if body.authentication_method is not None:
        preset = get_provider_profile_creation_preset(
            runtime_id=body.runtime_id,
            provider_id=body.provider_id,
            authentication_method=body.authentication_method,
        )
        try:
            return apply_provider_profile_creation_preset(
                preset=preset,
                requested_version=body.preset_version,
                values=values,
                supplied_fields=supplied_fields,
            )
        except CreationPresetError as exc:
            status_code = (
                409
                if exc.code
                == "provider_profile_creation_preset_version_mismatch"
                else 422
            )
            raise HTTPException(status_code=status_code, detail=exc.as_detail()) from exc

    missing_manual_fields = [
        field_name
        for field_name in ("credential_source", "runtime_materialization_mode")
        if values.get(field_name) is None
    ]
    if missing_manual_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "provider_profile_manual_fields_required",
                "message": (
                    "Manual Provider Profile creation requires explicit launch-policy fields."
                ),
                "required_fields": missing_manual_fields,
            },
        )

    capabilities = provider_profile_creation_capabilities(
        runtime_id=body.runtime_id,
        provider_id=body.provider_id,
    )
    try:
        validate_manual_credential_contract(
            credential_source=values["credential_source"],
            runtime_materialization_mode=values["runtime_materialization_mode"],
            authentication_methods=capabilities["authentication_methods"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    manual_defaults: dict[str, Any] = {
        "priority": 100,
        "max_parallel_runs": 1,
        "cooldown_after_429_seconds": 900,
        "rate_limit_policy": "backoff",
        "enabled": False,
        "is_default": False,
        "max_lease_duration_seconds": 7200,
        "auth_state": "not_configured",
    }
    for field_name, default_value in manual_defaults.items():
        if field_name not in supplied_fields:
            values[field_name] = default_value
    if "disabled_reason" not in supplied_fields:
        values["disabled_reason"] = (
            None if values["enabled"] else "missing_credentials"
        )
    return values


async def _credential_validation_guard(
    profile_id: str,
    request: Request,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> AsyncIterator[object]:
    async for guard in _credential_maintenance_guard(
        profile_id=profile_id,
        purpose="credential_validation",
        request=request,
        session=session,
        current_user=current_user,
    ):
        yield guard


async def _credential_disconnect_guard(
    profile_id: str,
    request: Request,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> AsyncIterator[object]:
    async for guard in _credential_maintenance_guard(
        profile_id=profile_id,
        purpose="oauth_disconnect",
        request=request,
        session=session,
        current_user=current_user,
    ):
        yield guard


async def _validate_imported_credential_volume(
    *,
    runtime_id: str,
    provider_id: str,
    volume_ref: str,
) -> tuple[str, str]:
    preset = get_provider_profile_creation_preset(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method=ProviderProfileAuthenticationMethod.OAUTH,
    )
    mount_path_field = preset.fields.get("volume_mount_path_after_setup")
    if not preset.supported or mount_path_field is None:
        raise HTTPException(
            status_code=422,
            detail="This runtime and provider do not support imported credential volumes.",
        )
    mount_path = mount_path_field.value
    if not isinstance(mount_path, str) or not mount_path:
        raise HTTPException(
            status_code=422,
            detail="The runtime strategy did not derive a credential mount path.",
        )

    try:
        from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
            verify_volume_credentials,
        )

        verification = await verify_volume_credentials(
            runtime_id=runtime_id,
            volume_ref=volume_ref,
            volume_mount_path=mount_path,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Imported credential volume validation is unavailable.",
        ) from exc
    if not verification.get("verified", False):
        reason = redact_sensitive_payload(
            str(verification.get("reason") or "credential identity was not verified")
        )
        raise HTTPException(
            status_code=422,
            detail=f"Imported credential volume validation failed: {reason}",
        )
    return volume_ref, mount_path


def _require_privileged_credential_volume_import(user: Any) -> None:
    """Keep arbitrary Docker credential-volume adoption superuser-only."""

    if bool(getattr(user, "is_superuser", False)):
        return
    raise HTTPException(
        status_code=403,
        detail="Importing an existing credential volume requires superuser authority.",
    )


def _guided_oauth_volume_ref(*, runtime_id: str, profile_id: str) -> str:
    """Return a stable opaque volume name owned by one guided OAuth profile."""

    digest = hashlib.sha256(
        f"{runtime_id.strip()}\0{profile_id.strip()}".encode("utf-8")
    ).hexdigest()[:24]
    return f"moonmind_oauth_{digest}"


def _activate_selected_api_key_refs(
    *,
    body: ProviderProfileCreate,
    values: dict[str, Any],
) -> None:
    """Activate a guided API-key profile when all required refs were selected."""

    if body.authentication_method != ProviderProfileAuthenticationMethod.API_KEY:
        return
    strategy = provider_api_key_strategy(body.runtime_id, body.provider_id)
    if strategy is None:
        return
    secret_refs = values.get("secret_refs")
    if not isinstance(secret_refs, dict) or not all(
        secret_refs.get(role)
        for role in required_secret_roles(body.runtime_id, body.provider_id)
    ):
        return

    activated_at = datetime.now(UTC)
    command_behavior = dict(values.get("command_behavior") or {})
    command_behavior.update(
        {
            "auth_state": "connected",
            "auth_status_label": strategy.ready_label,
            "auth_readiness": {
                "connected": True,
                "last_validated_at": activated_at.isoformat(),
                "backing_secret_exists": True,
                "launch_ready": True,
            },
        }
    )
    values.update(
        {
            "credential_source": ProviderCredentialSource.SECRET_REF.value,
            "enabled": True,
            "auth_state": ProviderProfileAuthState.CONNECTED.value,
            "disabled_reason": None,
            "first_authenticated_at": activated_at,
            "last_validated_at": activated_at,
            "last_auth_method": ProviderProfileAuthMethod.SECRET_REF.value,
            "command_behavior": command_behavior,
        }
    )


async def _reconcile_imported_credential_generation(
    profile: ManagedAgentProviderProfile,
) -> None:
    """Refresh bindings and fence hosts issued for an older credential home."""

    from api_service.db.base import get_async_session_context
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

    repository = OmnigentOAuthHostRepository(get_async_session_context)
    await repository.refresh_binding_generation(profile.profile_id)
    await repository.mark_generation_stale(
        profile_id=profile.profile_id,
        credential_generation=profile.credential_generation,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProviderProfileResponse])
async def list_profiles(
    runtime_id: Optional[str] = None,
    enabled_only: bool = False,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> list[dict[str, Any]]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    stmt = select(ManagedAgentProviderProfile)
    if runtime_id:
        stmt = stmt.where(ManagedAgentProviderProfile.runtime_id == runtime_id)
    if enabled_only:
        stmt = stmt.where(ManagedAgentProviderProfile.enabled.is_(True))
    stmt = stmt.order_by(
        ManagedAgentProviderProfile.is_default.desc(),
        ManagedAgentProviderProfile.priority.desc(),
        ManagedAgentProviderProfile.profile_id.asc(),
    )

    result = await session.execute(stmt)
    rows = result.scalars().all()
    visible_rows = [r for r in rows if _can_view_profile(r, current_user)]
    secret_ref_results = _secret_ref_results_for_rows(visible_rows)
    secret_statuses = await _managed_secret_statuses_for_rows(
        session,
        visible_rows,
        secret_ref_results=secret_ref_results,
    )
    return [
        _row_to_dict(
            r,
            managed_secret_statuses=secret_statuses,
            secret_ref_results=secret_ref_results.get(r.profile_id),
        )
        for r in visible_rows
    ]


@router.get(
    "/creation-preset",
    response_model=ProviderProfileCreationPresetResponse,
)
async def get_creation_preset(
    runtime_id: str,
    provider_id: str,
    authentication_method: ProviderProfileAuthenticationMethod,
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Preview the versioned backend policy used by standard profile creation."""

    _require_provider_profile_permission(current_user, "provider_profiles.read")
    return get_provider_profile_creation_preset(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method=authentication_method,
    ).as_dict()


@router.get(
    "/creation-capabilities",
    response_model=ProviderProfileCreationCapabilitiesResponse,
)
async def get_creation_capabilities(
    runtime_id: str,
    provider_id: str,
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    return provider_profile_creation_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
    )


@router.get(
    "/capabilities",
    response_model=ProviderProfileTierCapabilitiesResponse,
)
async def get_tier_capabilities_for_draft(
    runtime_id: str,
    provider_id: str,
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    from api_service.services.provider_profile_tier_capabilities import (
        tier_capabilities_for_draft,
    )

    return tier_capabilities_for_draft(runtime_id=runtime_id, provider_id=provider_id)


@router.get(
    "/{profile_id}/capabilities",
    response_model=ProviderProfileTierCapabilitiesResponse,
)
async def get_tier_capabilities_for_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    from api_service.services.provider_profile_tier_capabilities import (
        tier_capabilities_for_profile,
    )

    row = await session.get(ManagedAgentProviderProfile, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _can_view_profile(row, current_user):
        raise HTTPException(status_code=403, detail="Not authorized to view this provider profile.")
    return tier_capabilities_for_profile(row)


@router.post(
    "/credential-volume/validate",
    response_model=ProviderCredentialVolumeImportResponse,
)
async def validate_imported_credential_volume(
    body: ProviderCredentialVolumeImportRequest,
    current_user: User = Depends(get_current_user()),
) -> dict[str, str]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_privileged_credential_volume_import(current_user)
    volume_ref, mount_path = await _validate_imported_credential_volume(
        runtime_id=body.runtime_id,
        provider_id=body.provider_id,
        volume_ref=body.volume_ref.strip(),
    )
    return {
        "status": "validated",
        "volume_ref": volume_ref,
        "volume_mount_path": mount_path,
        "source": "validated_import",
    }


@router.get("/{profile_id}", response_model=ProviderProfileResponse)
async def get_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    row = await session.get(ManagedAgentProviderProfile, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _can_view_profile(row, current_user):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this provider profile.",
        )
    secret_ref_results = _secret_ref_results_for_rows([row])
    secret_statuses = await _managed_secret_statuses_for_rows(
        session,
        [row],
        secret_ref_results=secret_ref_results,
    )
    return _row_to_dict(
        row,
        managed_secret_statuses=secret_statuses,
        secret_ref_results=secret_ref_results.get(row.profile_id),
    )


@router.post("", response_model=ProviderProfileResponse, status_code=201)
async def create_profile(
    body: ProviderProfileCreate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    existing = await session.get(ManagedAgentProviderProfile, body.profile_id)
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")
    normalization_body = body
    if body.import_existing_credential_volume:
        normalization_body = body.model_copy(
            update={
                "volume_ref": None,
                "volume_mount_path": None,
                "import_existing_credential_volume": False,
            }
        )
    values = _normalized_create_values(normalization_body)
    if body.import_existing_credential_volume:
        _require_privileged_credential_volume_import(current_user)
        volume_ref, volume_mount_path = await _validate_imported_credential_volume(
            runtime_id=body.runtime_id,
            provider_id=body.provider_id,
            volume_ref=body.volume_ref or "",
        )
        now = datetime.now(UTC)
        command_behavior = dict(values.get("command_behavior") or {})
        command_behavior.update(
            {
                "auth_actions": [
                    "connect_oauth",
                    "validate_oauth",
                    "disconnect_oauth",
                ],
                "auth_state": "connected",
                "auth_status_label": "Connected",
                "auth_readiness": {
                    "connected": True,
                    "backing_secret_exists": True,
                    "launch_ready": True,
                },
            }
        )
        values.update(
            {
                "credential_source": ProviderCredentialSource.OAUTH_VOLUME.value,
                "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME.value,
                "volume_ref": volume_ref,
                "volume_mount_path": volume_mount_path,
                "enabled": True,
                "auth_state": ProviderProfileAuthState.CONNECTED.value,
                "disabled_reason": None,
                "first_authenticated_at": values.get("first_authenticated_at") or now,
                "last_validated_at": now,
                "last_auth_method": ProviderProfileAuthMethod.OAUTH_VOLUME.value,
                "command_behavior": command_behavior,
            }
        )
    elif body.authentication_method == ProviderProfileAuthenticationMethod.OAUTH:
        preset = get_provider_profile_creation_preset(
            runtime_id=body.runtime_id,
            provider_id=body.provider_id,
            authentication_method=ProviderProfileAuthenticationMethod.OAUTH,
        )
        mount_path_field = preset.fields.get("volume_mount_path_after_setup")
        mount_path = mount_path_field.value if mount_path_field is not None else None
        if not isinstance(mount_path, str) or not mount_path:
            raise HTTPException(
                status_code=422,
                detail="The runtime strategy did not derive a credential mount path.",
            )
        values["volume_ref"] = _guided_oauth_volume_ref(
            runtime_id=body.runtime_id,
            profile_id=body.profile_id,
        )
        values["volume_mount_path"] = mount_path

    _activate_selected_api_key_refs(body=body, values=values)
    try:
        model_tiers, default_model_tier = coerce_model_effort_tier_policy(
            model_tiers=(
                [tier.model_dump(mode="json") for tier in body.model_tiers]
                if body.model_tiers is not None
                else None
            ),
            default_model_tier=body.default_model_tier,
            legacy_default_model=body.default_model,
            legacy_default_effort=body.default_effort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    profile = ManagedAgentProviderProfile(
        profile_id=body.profile_id,
        runtime_id=values["runtime_id"],
        provider_id=values["provider_id"],
        provider_label=body.provider_label,
        default_model=body.default_model,
        default_effort=body.default_effort,
        model_tiers=model_tiers,
        default_model_tier=default_model_tier,
        model_overrides=body.model_overrides,
        credential_source=ProviderCredentialSource(values["credential_source"]),
        runtime_materialization_mode=RuntimeMaterializationMode(
            values["runtime_materialization_mode"]
        ),
        volume_ref=values["volume_ref"],
        volume_mount_path=values["volume_mount_path"],
        account_label=body.account_label,
        tags=values["tags"],
        priority=values["priority"],
        secret_refs=values["secret_refs"],
        clear_env_keys=values["clear_env_keys"],
        env_template=values["env_template"],
        file_templates=values["file_templates"],
        home_path_overrides=values["home_path_overrides"],
        command_behavior=values["command_behavior"],
        owner_user_id=getattr(current_user, "id", None),
        max_parallel_runs=values["max_parallel_runs"],
        cooldown_after_429_seconds=values["cooldown_after_429_seconds"],
        rate_limit_policy=ManagedAgentRateLimitPolicy(values["rate_limit_policy"]),
        enabled=values["enabled"],
        is_default=False,
        max_lease_duration_seconds=values["max_lease_duration_seconds"],
        auth_state=ProviderProfileAuthState(values["auth_state"]),
        disabled_reason=(
            ProviderProfileDisabledReason(values["disabled_reason"])
            if values["disabled_reason"] is not None
            else None
        ),
        first_authenticated_at=values["first_authenticated_at"],
        last_validated_at=values["last_validated_at"],
        last_auth_method=(
            ProviderProfileAuthMethod(values["last_auth_method"])
            if values["last_auth_method"] is not None
            else None
        ),
    )
    _validate_profile_tier_policy(profile)
    incomplete_preset_oauth = (
        body.authentication_method == ProviderProfileAuthenticationMethod.OAUTH
        and not profile.enabled
        and profile.auth_state != ProviderProfileAuthState.CONNECTED
        and profile.volume_ref is None
        and profile.volume_mount_path is None
    )
    if not incomplete_preset_oauth:
        _validate_codex_oauth_profile_row(profile)
    session.add(profile)
    await session.flush()
    if profile.enabled:
        secret_ref_results = _secret_ref_results_for_rows([profile])
        secret_statuses = await _managed_secret_statuses_for_rows(
            session,
            [profile],
            secret_ref_results=secret_ref_results,
        )
        _require_enabled_profile_launchable(
            profile,
            managed_secret_statuses=secret_statuses,
            secret_ref_results=secret_ref_results.get(profile.profile_id, {}),
        )
    await normalize_runtime_default_profile(
        session=session,
        runtime_id=profile.runtime_id,
        preferred_profile_id=(
            profile.profile_id if values["is_default"] else None
        ),
    )
    await session.commit()
    await session.refresh(profile)

    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)

    secret_ref_results = _secret_ref_results_for_rows([profile])
    secret_statuses = await _managed_secret_statuses_for_rows(
        session,
        [profile],
        secret_ref_results=secret_ref_results,
    )
    return _row_to_dict(
        profile,
        managed_secret_statuses=secret_statuses,
        secret_ref_results=secret_ref_results.get(profile.profile_id),
    )


@router.patch("/{profile_id}", response_model=ProviderProfileResponse)
async def update_profile(
    profile_id: str,
    body: ProviderProfileUpdate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)

    update_data = body.model_dump(exclude_unset=True)
    import_existing_credential_volume = bool(
        update_data.pop("import_existing_credential_volume", False)
    )
    manual_contract_overridden = bool(
        {"credential_source", "runtime_materialization_mode"}.intersection(update_data)
    )
    credential_contract_changed = (
        import_existing_credential_volume
        or manual_contract_overridden
        or "provider_id" in update_data
    )
    launch_authority_changed = bool(
        {
            "provider_id",
            "credential_source",
            "runtime_materialization_mode",
            "volume_ref",
            "volume_mount_path",
            "secret_refs",
            "clear_env_keys",
            "env_template",
            "file_templates",
            "home_path_overrides",
            "command_behavior",
            "max_parallel_runs",
            "cooldown_after_429_seconds",
            "auth_state",
            "disabled_reason",
            "last_auth_method",
        }.intersection(update_data)
        or update_data.get("enabled") is True
        or import_existing_credential_volume
    )
    requested_is_default = update_data.pop("is_default", None)
    target_provider_id = update_data.get("provider_id") or profile.provider_id
    capabilities = provider_profile_creation_capabilities(
        runtime_id=profile.runtime_id,
        provider_id=target_provider_id,
    )
    if (
        manual_contract_overridden
        and not import_existing_credential_volume
    ):
        try:
            validate_manual_credential_contract(
                credential_source=update_data.get(
                    "credential_source", profile.credential_source
                ),
                runtime_materialization_mode=update_data.get(
                    "runtime_materialization_mode",
                    profile.runtime_materialization_mode,
                ),
                authentication_methods=capabilities["authentication_methods"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if import_existing_credential_volume:
        _require_privileged_credential_volume_import(current_user)
        requested_volume_ref = update_data.get("volume_ref")
        if not isinstance(requested_volume_ref, str) or not requested_volume_ref.strip():
            raise HTTPException(
                status_code=422,
                detail="volume_ref is required for imported credential volumes",
            )
        volume_ref, volume_mount_path = await _validate_imported_credential_volume(
            runtime_id=profile.runtime_id,
            provider_id=update_data.get("provider_id", profile.provider_id),
            volume_ref=requested_volume_ref.strip(),
        )
        validated_at = datetime.now(UTC)
        update_data.update(
            {
                "credential_source": ProviderCredentialSource.OAUTH_VOLUME.value,
                "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME.value,
                "volume_ref": volume_ref,
                "volume_mount_path": volume_mount_path,
                "max_parallel_runs": 1,
                "enabled": True,
                "auth_state": ProviderProfileAuthState.CONNECTED.value,
                "disabled_reason": None,
                "first_authenticated_at": (
                    profile.first_authenticated_at or validated_at
                ),
                "last_validated_at": validated_at,
                "last_auth_method": ProviderProfileAuthMethod.OAUTH_VOLUME.value,
            }
        )
        profile.credential_generation = int(profile.credential_generation) + 1
    model_tiers_supplied = "model_tiers" in update_data
    default_model_tier_supplied = "default_model_tier" in update_data
    legacy_default_supplied = (
        "default_model" in update_data or "default_effort" in update_data
    )
    should_refresh_single_default_tier = (
        legacy_default_supplied
        and not model_tiers_supplied
        and not default_model_tier_supplied
        and (
            is_single_runtime_default_model_effort_tier(profile.model_tiers)
            or (
                profile.default_model_tier == 1
                and is_single_legacy_default_model_effort_tier(
                    profile.model_tiers,
                    legacy_default_model=profile.default_model,
                    legacy_default_effort=profile.default_effort,
                )
            )
        )
    )
    if (
        model_tiers_supplied
        or default_model_tier_supplied
        or should_refresh_single_default_tier
    ):
        raw_model_tiers = update_data.pop("model_tiers", None)
        raw_default_model_tier = update_data.pop("default_model_tier", None)
        legacy_default_model = update_data.get(
            "default_model",
            profile.default_model,
        )
        legacy_default_effort = update_data.get(
            "default_effort",
            profile.default_effort,
        )
        try:
            model_tiers, default_model_tier = coerce_model_effort_tier_policy(
                model_tiers=(
                    raw_model_tiers
                    if model_tiers_supplied
                    else (
                        None
                        if should_refresh_single_default_tier
                        else profile.model_tiers
                    )
                ),
                default_model_tier=(
                    raw_default_model_tier
                    if default_model_tier_supplied
                    else profile.default_model_tier
                ),
                legacy_default_model=legacy_default_model,
                legacy_default_effort=legacy_default_effort,
                empty_as_missing=should_refresh_single_default_tier,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        profile.model_tiers = model_tiers
        profile.default_model_tier = default_model_tier
    for key, value in update_data.items():
        if key == "rate_limit_policy" and value is not None:
            value = ManagedAgentRateLimitPolicy(value)
        elif key == "credential_source" and value is not None:
            value = ProviderCredentialSource(value)
        elif key == "runtime_materialization_mode" and value is not None:
            value = RuntimeMaterializationMode(value)
        elif key == "auth_state" and value is not None:
            value = ProviderProfileAuthState(value)
        elif key == "disabled_reason" and value is not None:
            value = ProviderProfileDisabledReason(value)
        elif key == "last_auth_method" and value is not None:
            value = ProviderProfileAuthMethod(value)
        setattr(profile, key, value)
    try:
        model_tiers, default_model_tier = coerce_model_effort_tier_policy(
            model_tiers=profile.model_tiers,
            default_model_tier=profile.default_model_tier,
            legacy_default_model=profile.default_model,
            legacy_default_effort=profile.default_effort,
            empty_as_missing=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    profile.model_tiers = model_tiers
    profile.default_model_tier = default_model_tier

    if credential_contract_changed:
        try:
            validate_credential_contract(
                credential_source=profile.credential_source,
                runtime_materialization_mode=profile.runtime_materialization_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if infer_authentication_method(
            credential_source=profile.credential_source,
            runtime_materialization_mode=profile.runtime_materialization_mode,
            authentication_methods=capabilities["authentication_methods"],
            auth_state=profile.auth_state,
            last_auth_method=profile.last_auth_method,
        ) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Credential contract does not match a supported "
                    "authentication preset."
                ),
            )

    if profile.enabled and launch_authority_changed:
        if profile.auth_state != ProviderProfileAuthState.CONNECTED:
            raise HTTPException(
                status_code=422,
                detail="Enabled profiles require auth_state=connected",
            )
        profile.disabled_reason = None
        secret_ref_results = _secret_ref_results_for_rows([profile])
        secret_statuses = await _managed_secret_statuses_for_rows(
            session,
            [profile],
            secret_ref_results=secret_ref_results,
        )
        _require_enabled_profile_launchable(
            profile,
            managed_secret_statuses=secret_statuses,
            secret_ref_results=secret_ref_results.get(profile.profile_id, {}),
        )

    try:
        _validate_profile_tier_policy(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if requested_is_default is False:
        profile.is_default = False

    _validate_codex_oauth_profile_row(profile)
    await session.flush()
    await normalize_runtime_default_profile(
        session=session,
        runtime_id=profile.runtime_id,
        preferred_profile_id=profile.profile_id if requested_is_default else None,
    )
    await session.commit()
    await session.refresh(profile)
    if import_existing_credential_volume:
        await _reconcile_imported_credential_generation(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)
    secret_ref_results = _secret_ref_results_for_rows([profile])
    secret_statuses = await _managed_secret_statuses_for_rows(
        session,
        [profile],
        secret_ref_results=secret_ref_results,
    )
    return _row_to_dict(
        profile,
        managed_secret_statuses=secret_statuses,
        secret_ref_results=secret_ref_results.get(profile.profile_id),
    )


@router.post(
    "/{profile_id}/model-tiers:preview",
    response_model=ProviderProfileTierPreviewResponse,
)
async def preview_model_tiers(
    profile_id: str,
    body: ProviderProfileTierPreviewRequest,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _can_view_profile(profile, current_user):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this provider profile.",
        )

    items: list[dict[str, Any]] = []
    for step in body.steps:
        try:
            resolved = resolve_model_effort(
                runtime_id=profile.runtime_id,
                profile=profile,
                requested_model_tier=step.model_tier,
                tier_fallback=step.tier_fallback or "clamp",
                require_launch_ready=False,
            )
        except RequestedModelTierUnavailableError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "requestedModelTier": exc.requested_model_tier,
                    "configuredTierCount": exc.configured_tier_count,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        items.append(
            {
                "stepId": step.step_id,
                "requestedTier": resolved.requested_model_tier,
                "effectiveTier": resolved.effective_model_tier,
                "model": resolved.model,
                "effort": resolved.effort,
                "fallbackReason": resolved.fallback_reason,
            }
        )

    return {
        "profileId": profile.profile_id,
        "profileVersion": provider_profile_version(profile),
        "advisory": True,
        "items": items,
    }


@router.post(
    "/{profile_id}/credentials/api-key",
    response_model=ProviderApiKeySetupResponse,
)
async def setup_provider_api_key(
    profile_id: str,
    body: ProviderApiKeySetupRequest,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
    maintenance_guard: object = Depends(_credential_validation_guard),
) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_profile_management(profile, current_user)
    mapping = _api_key_mapping_for_profile(profile)

    api_key = body.api_key.strip()
    if not _looks_like_provider_api_key(mapping, api_key):
        await _mark_api_key_validation_failed(
            session=session,
            profile=profile,
            reason="API key validation failed.",
        )
        raise HTTPException(status_code=422, detail="API key validation failed.")

    is_opencode = mapping.provider_id in {"opencode-go", "opencode"}
    if not is_opencode:
        try:
            await validate_provider_api_key(profile.provider_id, api_key)
        except HTTPException as exc:
            if exc.status_code in {401, 403, 422}:
                await _mark_api_key_validation_failed(
                    session=session,
                    profile=profile,
                    reason="API key validation failed.",
                )
            raise exc

    validated_at = datetime.now(UTC)
    secret_slug = _provider_api_key_secret_slug(
        profile.profile_id,
        mapping.secret_role,
    )
    secret_ref = f"db://{secret_slug}"
    rotated = mapping.secret_role in (profile.secret_refs or {})
    candidate_generation = int(profile.credential_generation) + (1 if rotated else 0)
    runtime_evidence: dict[str, Any] | None = None
    if is_opencode:
        try:
            from api_service.db.base import async_session_maker
            from moonmind.omnigent.harness_platform.host_classes import (
                get_opencode_host_image_ref,
            )
            from moonmind.omnigent.opencode_runtime_validation import (
                OpenCodeProviderRuntimeValidationService,
            )
            from moonmind.omnigent.production import build_omnigent_secret_resolver

            runtime_evidence = await OpenCodeProviderRuntimeValidationService(
                session_factory=async_session_maker,
                resolver=build_omnigent_secret_resolver(),
                image_ref=get_opencode_host_image_ref(),
            ).validate(
                profile=profile,
                lease=maintenance_guard.lease,
                candidate_secret=api_key,
                candidate_generation=candidate_generation,
            )
        except Exception as exc:
            # Rotation is atomic: the previously validated SecretRef,
            # generation, and launch readiness remain authoritative.
            if not rotated:
                await _mark_api_key_validation_failed(
                    session=session,
                    profile=profile,
                    reason="Pinned OpenCode runtime validation failed.",
                )
            status = 422 if isinstance(exc, ValueError) else 502
            raise HTTPException(
                status_code=status,
                detail="Pinned OpenCode runtime validation failed.",
            ) from exc
    await _upsert_managed_secret(
        session=session,
        slug=secret_slug,
        plaintext=api_key,
        details={
            "provider_profile_id": profile.profile_id,
            "runtime_id": profile.runtime_id,
            "provider_id": profile.provider_id,
            "auth_strategy": mapping.auth_strategy,
            "secret_role": mapping.secret_role,
            "last_validated_at": validated_at.isoformat(),
        },
    )

    _apply_api_key_setup_to_profile(
        profile,
        mapping=mapping,
        secret_ref=secret_ref,
        account_label=body.account_label,
        validated_at=validated_at,
        enabled=body.enable_after_validation,
    )
    profile.credential_generation = candidate_generation

    if is_opencode:
        assert runtime_evidence is not None
        evidence = runtime_evidence
        profile.model_catalog_evidence_json = evidence
        models = [
            str(item.get("qualifiedId") or "")
            for item in evidence.get("models", [])
            if isinstance(item, dict)
        ]
        if not profile.default_model and models:
            profile.default_model = models[0]
        behavior = dict(profile.command_behavior or {})
        behavior["runtime_validation"] = {
            "last_validated_at": evidence["validatedAt"],
            "image_ref": evidence["imageRef"],
            "runtime_versions": evidence["runtimeVersions"],
            "model_count": len(models),
        }
        profile.command_behavior = behavior

    await session.flush()
    secret_ref_results = _secret_ref_results_for_rows([profile])
    secret_statuses = await _managed_secret_statuses_for_rows(
        session,
        [profile],
        secret_ref_results=secret_ref_results,
    )
    if profile.enabled:
        _require_enabled_profile_launchable(
            profile,
            managed_secret_statuses=secret_statuses,
            secret_ref_results=secret_ref_results.get(profile.profile_id, {}),
        )
    await normalize_runtime_default_profile(
        session=session,
        runtime_id=profile.runtime_id,
        preferred_profile_id=profile.profile_id if body.make_default else None,
    )
    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)

    return {
        "status": "ready",
        "status_label": mapping.ready_label,
        "profile_id": profile.profile_id,
        "secret_ref": secret_ref,
        "readiness": {
            "connected": True,
            "last_validated_at": validated_at.isoformat(),
            "backing_secret_exists": True,
            "launch_ready": True,
            "failure_reason": None,
        },
    }


@router.post(
    "/{profile_id}/manual-auth/commit",
    response_model=ClaudeManualAuthCommitResponse,
)
async def commit_claude_manual_auth(
    profile_id: str,
    body: ClaudeManualAuthCommitRequest,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)
    _require_claude_anthropic_profile(profile)

    token = body.token.strip()
    if not _looks_like_claude_manual_token(token):
        raise HTTPException(
            status_code=422,
            detail="Claude token validation failed.",
        )
    await validate_claude_manual_token(token)

    validated_at = datetime.now(UTC)
    secret_slug = _claude_manual_secret_slug(profile.profile_id)
    secret_ref = f"db://{secret_slug}"
    await _upsert_managed_secret(
        session=session,
        slug=secret_slug,
        plaintext=token,
        details={
            "provider_profile_id": profile.profile_id,
            "runtime_id": profile.runtime_id,
            "provider_id": profile.provider_id,
            "auth_strategy": "claude_credential_methods",
            "last_validated_at": validated_at.isoformat(),
        },
    )

    profile.credential_source = ProviderCredentialSource.SECRET_REF
    profile.runtime_materialization_mode = RuntimeMaterializationMode.API_KEY_ENV
    profile.secret_refs = {
        **(profile.secret_refs or {}),
        "anthropic_api_key": secret_ref,
    }
    clear_env_keys = list(profile.clear_env_keys or [])
    for env_key in [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
    ]:
        if env_key not in clear_env_keys:
            clear_env_keys.append(env_key)
    profile.clear_env_keys = clear_env_keys
    profile.env_template = {
        **(profile.env_template or {}),
        "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
    }
    profile.account_label = (
        body.account_label or profile.account_label or "Claude Anthropic"
    )
    profile.enabled = True
    profile.auth_state = ProviderProfileAuthState.CONNECTED
    profile.disabled_reason = None
    if profile.first_authenticated_at is None:
        profile.first_authenticated_at = validated_at
    profile.last_validated_at = validated_at
    profile.last_auth_method = ProviderProfileAuthMethod.SECRET_REF
    behavior = dict(profile.command_behavior or {})
    behavior.update(
        {
            "auth_strategy": "claude_credential_methods",
            "auth_state": "connected",
            "auth_actions": oauth_auth_actions_for_profile(profile),
            "auth_status_label": "Anthropic API key ready",
            "auth_readiness": {
                "connected": True,
                "last_validated_at": validated_at.isoformat(),
                "backing_secret_exists": True,
                "launch_ready": True,
            },
        }
    )
    profile.command_behavior = behavior

    await session.flush()
    await normalize_runtime_default_profile(
        session=session,
        runtime_id=profile.runtime_id,
    )
    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)

    return {
        "status": "ready",
        "status_label": "Anthropic API key ready",
        "profile_id": profile.profile_id,
        "secret_ref": secret_ref,
        "readiness": {
            "connected": True,
            "last_validated_at": validated_at.isoformat(),
            "backing_secret_exists": True,
            "launch_ready": True,
            "failure_reason": None,
        },
    }


@router.post("/{profile_id}/oauth/validate")
async def validate_claude_oauth_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
    _maintenance_guard: object = Depends(_credential_validation_guard),
) -> dict[str, Any]:
    """Validate an OAuth-backed provider profile against its auth volume.

    Generalized across the first-party Claude and Codex runtimes; the
    handler name is retained for OpenAPI operation-id stability.
    """
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)
    mapping = _require_first_party_oauth_profile(profile)
    if not profile.volume_ref or not profile.volume_mount_path:
        raise HTTPException(
            status_code=422,
            detail=f"{mapping.label_prefix} OAuth validation requires OAuth volume metadata.",
        )

    try:
        from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
            verify_volume_credentials,
        )

        verification = await verify_volume_credentials(
            runtime_id=profile.runtime_id,
            volume_ref=profile.volume_ref,
            volume_mount_path=profile.volume_mount_path,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{mapping.label_prefix} OAuth validation unavailable.",
        ) from exc

    if not verification.get("verified", False):
        failed_at = datetime.now(UTC)
        apply_oauth_validation_failure(
            profile,
            mapping=mapping,
            reason=verification.get("reason"),
            failed_at=failed_at,
        )
        await session.commit()
        await session.refresh(profile)
        reason = (
            profile.command_behavior.get("auth_readiness", {}).get("failure_reason")
            if isinstance(profile.command_behavior, dict)
            else None
        ) or "unknown"
        raise HTTPException(
            status_code=400,
            detail=f"{mapping.label_prefix} OAuth validation failed: {reason}",
        )

    validated_at = datetime.now(UTC)
    profile.enabled = True
    profile.auth_state = ProviderProfileAuthState.CONNECTED
    profile.disabled_reason = None
    if profile.first_authenticated_at is None:
        profile.first_authenticated_at = validated_at
    profile.last_validated_at = validated_at
    profile.last_auth_method = ProviderProfileAuthMethod.OAUTH_VOLUME
    apply_oauth_connected_state(
        profile,
        mapping=mapping,
        validated_at=validated_at,
    )
    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)
    return {
        "status": "ready",
        "status_label": f"{mapping.label_prefix} OAuth ready",
        "profile_id": profile.profile_id,
        "readiness": (
            profile.command_behavior.get("auth_readiness")
            if isinstance(profile.command_behavior, dict)
            else None
        ),
    }


@router.post("/{profile_id}/oauth/disconnect")
async def disconnect_claude_oauth_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
    _maintenance_guard: object = Depends(_credential_disconnect_guard),
) -> dict[str, Any]:
    """Disconnect an OAuth-backed provider profile and clear its volume fields.

    Generalized across the first-party Claude and Codex runtimes; the
    handler name is retained for OpenAPI operation-id stability.
    """
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)
    mapping = _require_first_party_oauth_profile(profile)

    if profile.credential_source == ProviderCredentialSource.OAUTH_VOLUME:
        profile.credential_source = ProviderCredentialSource.NONE
        profile.runtime_materialization_mode = RuntimeMaterializationMode.API_KEY_ENV
    profile.volume_ref = None
    profile.volume_mount_path = None
    clear_oauth_home_path_overrides(profile, mapping=mapping)
    disconnected_at = datetime.now(UTC)
    profile.enabled = False
    profile.auth_state = ProviderProfileAuthState.DISCONNECTED
    profile.disabled_reason = ProviderProfileDisabledReason.DISCONNECTED
    profile.last_validated_at = disconnected_at
    update_oauth_command_behavior(
        profile,
        mapping=mapping,
        auth_state="disconnected",
        status_label=f"{mapping.label_prefix} OAuth disconnected",
        readiness={
            "connected": False,
            "last_validated_at": disconnected_at.isoformat(),
            "backing_secret_exists": False,
            "launch_ready": False,
        },
    )
    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)
    return {
        "status": "disconnected",
        "status_label": f"{mapping.label_prefix} OAuth disconnected",
        "profile_id": profile.profile_id,
    }


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> None:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    _require_profile_management(profile, current_user)
    runtime_id = profile.runtime_id
    await session.delete(profile)
    await session.flush()
    await normalize_runtime_default_profile(session=session, runtime_id=runtime_id)
    await session.commit()
    await sync_provider_profile_manager(session=session, runtime_id=runtime_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(user: Any) -> str | None:
    raw = getattr(user, "id", None)
    if raw is None:
        return None
    return str(raw)


def _require_provider_profile_permission(user: Any, permission: str) -> None:
    if has_settings_permission(user, permission):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Missing required provider profile permission: {permission}.",
    )


def _can_view_profile(row: ManagedAgentProviderProfile, user: Any) -> bool:
    user_id = _user_id(user)
    if user_id is None or bool(getattr(user, "is_superuser", False)):
        return True
    owner_id = row.owner_user_id
    return owner_id is None or str(owner_id) == user_id


def _require_profile_management(row: ManagedAgentProviderProfile, user: Any) -> None:
    user_id = _user_id(user)
    if user_id is None or bool(getattr(user, "is_superuser", False)):
        return
    owner_id = row.owner_user_id
    if owner_id is None or str(owner_id) == user_id:
        return
    raise HTTPException(
        status_code=403,
        detail="Not authorized to manage this provider profile.",
    )


def _validate_codex_oauth_profile_row(row: ManagedAgentProviderProfile) -> None:
    try:
        validate_codex_oauth_profile_refs(
            runtime_id=row.runtime_id,
            credential_source=(
                row.credential_source.value if row.credential_source else None
            ),
            runtime_materialization_mode=(
                row.runtime_materialization_mode.value
                if row.runtime_materialization_mode
                else None
            ),
            volume_ref=row.volume_ref,
            volume_mount_path=row.volume_mount_path,
            max_parallel_runs=row.max_parallel_runs,
            volume_ref_field_name="volume_ref",
            volume_mount_path_field_name="volume_mount_path",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_claude_anthropic_profile(row: ManagedAgentProviderProfile) -> None:
    if row.runtime_id != "claude_code" or row.provider_id != "anthropic":
        raise HTTPException(
            status_code=422,
            detail="Manual Claude auth is only supported for claude_code Anthropic profiles.",
        )


def _require_first_party_oauth_profile(
    row: ManagedAgentProviderProfile,
) -> FirstPartyOAuthProfile:
    mapping = get_first_party_oauth_profile(row.runtime_id, row.provider_id)
    if mapping is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "OAuth lifecycle actions are only supported for first-party "
                "Claude and Codex provider profiles."
            ),
        )
    return mapping


def _api_key_mapping_for_profile(
    row: ManagedAgentProviderProfile,
) -> ProviderApiKeyStrategy:
    mapping = provider_api_key_strategy(row.runtime_id, row.provider_id)
    if mapping is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "API-key setup is only supported for first-party Anthropic, "
                "OpenAI, and OpenCode Go profiles."
            ),
        )
    return mapping


def _looks_like_claude_manual_token(token: str) -> bool:
    return token.startswith("sk-ant-") and len(token) >= 12


def _claude_manual_secret_slug(profile_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", profile_id.lower()).strip("-")
    if not normalized:
        normalized = "claude-anthropic"
    digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:16]
    return f"{normalized}-{digest}-token"


def _provider_api_key_secret_slug(profile_id: str, secret_role: str) -> str:
    normalized_profile = re.sub(r"[^a-z0-9]+", "-", profile_id.lower()).strip("-")
    normalized_role = re.sub(r"[^a-z0-9]+", "-", secret_role.lower()).strip("-")
    if not normalized_profile:
        normalized_profile = "provider-profile"
    digest = hashlib.sha256(f"{profile_id}:{secret_role}".encode("utf-8")).hexdigest()[
        :16
    ]
    return f"{normalized_profile}-{normalized_role}-{digest}"


def _looks_like_provider_api_key(
    mapping: ProviderApiKeyStrategy, api_key: str
) -> bool:
    if not api_key:
        return False
    if mapping.provider_id == "anthropic":
        return api_key.startswith("sk-ant-") and len(api_key) >= 12
    if mapping.provider_id == "openai":
        return api_key.startswith("sk-") and len(api_key) >= 12
    if mapping.provider_id in {"opencode-go", "opencode"}:
        # OpenCode API keys are provider-specific; accept common prefixes
        # but require minimum entropy to avoid trivial values.
        return len(api_key.strip()) >= 12 and " " not in api_key.strip()
    return False


def _apply_api_key_setup_to_profile(
    row: ManagedAgentProviderProfile,
    *,
    mapping: ProviderApiKeyStrategy,
    secret_ref: str,
    account_label: str | None,
    validated_at: datetime,
    enabled: bool,
) -> None:
    row.credential_source = ProviderCredentialSource.SECRET_REF
    # OpenCode uses file-based auth (opencode-auth-json@1), not env
    file_materialized = mapping.auth_strategy in {
        "opencode_auth_json",
        "omnigent_provider_config",
    }
    if file_materialized:
        row.runtime_materialization_mode = RuntimeMaterializationMode.COMPOSITE
    else:
        row.runtime_materialization_mode = RuntimeMaterializationMode.API_KEY_ENV
    clear_oauth_home_path_overrides(
        row,
        mapping=get_first_party_oauth_profile(row.runtime_id, row.provider_id),
    )
    row.secret_refs = {
        **(row.secret_refs or {}),
        mapping.secret_role: secret_ref,
    }
    clear_env_keys = list(row.clear_env_keys or [])
    for env_key in mapping.clear_env_keys:
        if env_key not in clear_env_keys:
            clear_env_keys.append(env_key)
    row.clear_env_keys = clear_env_keys
    if file_materialized:
        # OpenCode auth is a file materialized via opencode-auth-json@1 trusted
        # materializer, which knows the exact target path and permissions. Do not
        # store an invalid file_templates contract (RuntimeFileTemplate forbids
        # from_secret_ref/mode/provider_key and rejects absolute paths outside
        # runtime_support_dir). Rely solely on secret_refs + materializer logic.
        if row.file_templates:
            row.file_templates = [
                t
                for t in row.file_templates
                if t.get("path") != "/home/app/.local/share/opencode/auth.json"
            ]
        # Do not pollute env_template with OpenCode key; clear any prior
        if mapping.env_key and mapping.env_key in (row.env_template or {}):
            row.env_template = {
                k: v
                for k, v in (row.env_template or {}).items()
                if k != mapping.env_key
            }
        else:
            row.env_template = row.env_template or {}
    else:
        row.env_template = {
            **(row.env_template or {}),
            mapping.env_key: {"from_secret_ref": mapping.secret_role},
        }
    row.account_label = account_label or row.account_label or row.provider_label
    row.enabled = enabled
    row.auth_state = ProviderProfileAuthState.CONNECTED
    row.disabled_reason = (
        None if enabled else ProviderProfileDisabledReason.USER_DISABLED
    )
    if row.first_authenticated_at is None:
        row.first_authenticated_at = validated_at
    row.last_validated_at = validated_at
    row.last_auth_method = ProviderProfileAuthMethod.SECRET_REF
    behavior = dict(row.command_behavior or {})
    behavior.update(
        {
            "auth_strategy": mapping.auth_strategy,
            "auth_state": "connected",
            "auth_actions": ["use_api_key"],
            "auth_status_label": mapping.ready_label,
            "auth_readiness": {
                "connected": True,
                "last_validated_at": validated_at.isoformat(),
                "backing_secret_exists": True,
                "launch_ready": True,
            },
        }
    )
    row.command_behavior = behavior


async def _mark_api_key_validation_failed(
    *,
    session: AsyncSession,
    profile: ManagedAgentProviderProfile,
    reason: str,
) -> None:
    failed_at = datetime.now(UTC)
    profile.enabled = False
    profile.auth_state = ProviderProfileAuthState.VALIDATION_FAILED
    profile.disabled_reason = ProviderProfileDisabledReason.AUTH_INVALID
    profile.last_validated_at = failed_at
    behavior = dict(profile.command_behavior or {})
    behavior.update(
        {
            "auth_state": "validation_failed",
            "auth_status_label": "API key validation failed",
            "auth_readiness": {
                "connected": False,
                "last_validated_at": failed_at.isoformat(),
                "backing_secret_exists": False,
                "launch_ready": False,
                "failure_reason": redact_sensitive_payload(reason),
            },
        }
    )
    profile.command_behavior = behavior
    await normalize_runtime_default_profile(
        session=session, runtime_id=profile.runtime_id
    )
    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)


async def _upsert_managed_secret(
    *,
    session: AsyncSession,
    slug: str,
    plaintext: str,
    details: dict[str, Any],
) -> ManagedSecret:
    result = await session.execute(
        select(ManagedSecret).where(ManagedSecret.slug == slug)
    )
    secret = result.scalar_one_or_none()
    if secret is None:
        secret = ManagedSecret(
            slug=slug,
            ciphertext=plaintext,
            status=SecretStatus.ACTIVE,
            details=details,
        )
        session.add(secret)
        return secret

    secret.ciphertext = plaintext
    secret.status = SecretStatus.ACTIVE
    secret.details = {**(secret.details or {}), **details}
    secret.updated_at = datetime.now(UTC)
    return secret


async def validate_claude_manual_token(token: str) -> None:
    headers = {
        "x-api-key": token,
        "anthropic-version": "2023-06-01",
    }
    try:
        response = await _get_claude_manual_validation_client().get(
            "https://api.anthropic.com/v1/models",
            headers=headers,
        )
    except httpx.HTTPError as exc:
        logger.warning("claude_manual_auth_validation_failed", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Claude token validation failed.",
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=401,
            detail="Claude token validation failed.",
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="Claude token validation failed.",
        )


async def validate_provider_api_key(provider_id: str, api_key: str) -> None:
    provider_id = provider_id.strip().lower()
    if provider_id == "anthropic":
        await validate_claude_manual_token(api_key)
        return
    if provider_id == "openai":
        await _validate_openai_api_key(api_key)
        return
    if provider_id in {"opencode-go", "opencode"}:
        raise HTTPException(
            status_code=422,
            detail="OpenCode validation requires the pinned Provider Profile runtime path.",
        )
    raise HTTPException(
        status_code=422,
        detail="Unsupported provider API-key setup.",
    )


async def _validate_openai_api_key(api_key: str) -> None:
    try:
        response = await _get_claude_manual_validation_client().get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except httpx.HTTPError as exc:
        logger.warning("openai_api_key_validation_failed")
        raise HTTPException(
            status_code=502, detail="API key validation failed."
        ) from exc
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=401, detail="API key validation failed.")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="API key validation failed.")


def _get_claude_manual_validation_client() -> httpx.AsyncClient:
    global _claude_manual_validation_client
    if (
        _claude_manual_validation_client is None
        or _claude_manual_validation_client.is_closed
    ):
        _claude_manual_validation_client = httpx.AsyncClient(timeout=10.0)
    return _claude_manual_validation_client


async def _managed_secret_statuses_for_rows(
    session: AsyncSession,
    rows: list[ManagedAgentProviderProfile],
    *,
    secret_ref_results: dict[str, dict[str, _SecretRefParseResult]] | None = None,
) -> dict[str, str]:
    secret_ref_results = secret_ref_results or _secret_ref_results_for_rows(rows)
    slugs: set[str] = set()
    for row in rows:
        for result in secret_ref_results.get(row.profile_id, {}).values():
            if result.parsed and result.parsed.backend == SecretBackend.DB_ENCRYPTED:
                slugs.add(result.parsed.locator)
    if not slugs:
        return {}

    result = await session.execute(
        select(ManagedSecret).where(ManagedSecret.slug.in_(slugs))
    )
    return {secret.slug: secret.status.value for secret in result.scalars().all()}


def _secret_ref_results_for_rows(
    rows: list[ManagedAgentProviderProfile],
) -> dict[str, dict[str, _SecretRefParseResult]]:
    results: dict[str, dict[str, _SecretRefParseResult]] = {}
    for row in rows:
        row_results: dict[str, _SecretRefParseResult] = {}
        if not isinstance(row.secret_refs, dict):
            results[row.profile_id] = row_results
            continue
        for role, ref in row.secret_refs.items():
            if not isinstance(ref, str) or not ref:
                row_results[str(role)] = _SecretRefParseResult(
                    error="SecretRef must be a non-empty string"
                )
                continue
            try:
                row_results[str(role)] = _SecretRefParseResult(
                    parsed=parse_secret_ref(ref)
                )
            except SecretReferenceError as exc:
                row_results[str(role)] = _SecretRefParseResult(error=str(exc))
        results[row.profile_id] = row_results
    return results


def _readiness_check(
    check_id: str,
    label: str,
    status: str,
    message: str,
) -> dict[str, str]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "message": redact_sensitive_payload(message),
    }


def _provider_profile_readiness(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str] | None = None,
    secret_ref_results: dict[str, _SecretRefParseResult] | None = None,
) -> dict[str, Any]:
    managed_secret_statuses = managed_secret_statuses or {}
    secret_ref_results = secret_ref_results or _secret_ref_results_for_rows([row]).get(
        row.profile_id,
        {},
    )

    credential_source = row.credential_source.value if row.credential_source else None
    materialization_mode = (
        row.runtime_materialization_mode.value
        if row.runtime_materialization_mode
        else None
    )

    checks = [
        _required_fields_check(row),
        _credential_capability_check(row),
        _enabled_check(row),
        _auth_state_check(row),
        _secret_refs_check(
            row,
            credential_source=credential_source,
            managed_secret_statuses=managed_secret_statuses,
            secret_ref_results=secret_ref_results,
        ),
        _oauth_volume_check(
            row,
            credential_source=credential_source,
            materialization_mode=materialization_mode,
        ),
        _concurrency_check(row),
        _cooldown_check(row),
        _provider_validation_check(row),
    ]

    if any(check["status"] == "error" for check in checks):
        status = "blocked"
        launch_ready = False
        summary = "Provider profile has launch blockers."
    elif any(check["status"] == "warning" for check in checks):
        status = "warning"
        launch_ready = True
        summary = "Provider profile is usable with readiness warnings."
    else:
        status = "ready"
        launch_ready = True
        summary = "Provider profile is ready for launch."

    return {
        "status": status,
        "launch_ready": launch_ready,
        "summary": summary,
        "checks": checks,
    }


def _require_enabled_profile_launchable(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str],
    secret_ref_results: dict[str, _SecretRefParseResult],
) -> None:
    readiness = _provider_profile_readiness(
        row,
        managed_secret_statuses=managed_secret_statuses,
        secret_ref_results=secret_ref_results,
    )
    blockers = [
        check["message"] for check in readiness["checks"] if check["status"] == "error"
    ]
    if blockers:
        raise HTTPException(
            status_code=422,
            detail=(
                "Enabled profiles require connected credentials and launch-ready "
                "credential bindings: " + "; ".join(blockers)
            ),
        )


def _required_fields_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    missing_required = [
        field_name
        for field_name, value in {
            "profile_id": row.profile_id,
            "runtime_id": row.runtime_id,
            "provider_id": row.provider_id,
            "credential_source": row.credential_source,
            "runtime_materialization_mode": row.runtime_materialization_mode,
        }.items()
        if value in {None, ""}
    ]
    return _readiness_check(
        "required_fields",
        "Required fields",
        "error" if missing_required else "pass",
        (
            "Missing required fields: " + ", ".join(missing_required)
            if missing_required
            else "Required provider profile fields are present."
        ),
    )


def _credential_capability_check(
    row: ManagedAgentProviderProfile,
) -> dict[str, str]:
    capabilities = provider_profile_creation_capabilities(
        runtime_id=row.runtime_id,
        provider_id=row.provider_id,
    )
    if not capabilities["supported"]:
        return _readiness_check(
            "credential_capability",
            "Credential capability",
            "error",
            (
                "No authoritative authentication capability is registered for "
                "this profile."
            ),
        )
    authentication_method = infer_authentication_method(
        credential_source=row.credential_source,
        runtime_materialization_mode=row.runtime_materialization_mode,
        authentication_methods=capabilities["authentication_methods"],
        auth_state=row.auth_state,
        last_auth_method=row.last_auth_method,
    )
    launch_ready_after_setup = bool(
        authentication_method
        and authentication_method_launch_ready_after_setup(
            authentication_method=authentication_method,
            authentication_methods=capabilities["authentication_methods"],
        )
    )
    expert_launch_ready = bool(
        authentication_method
        and not launch_ready_after_setup
        and expert_manual_credential_launch_ready(
            runtime_id=row.runtime_id,
            provider_id=row.provider_id,
            credential_source=row.credential_source,
            runtime_materialization_mode=row.runtime_materialization_mode,
            secret_refs=row.secret_refs,
            env_template=row.env_template,
            file_templates=row.file_templates,
            home_path_overrides=row.home_path_overrides,
            command_behavior=row.command_behavior,
        )
    )
    launch_ready = launch_ready_after_setup or expert_launch_ready
    return _readiness_check(
        "credential_capability",
        "Credential capability",
        "pass" if launch_ready else "error",
        (
            f"Credential contract matches the launch-ready {authentication_method} preset."
            if launch_ready
            else (
                "This expert credential preset requires a provider-specific "
                "validation path before it can become launch ready."
                if authentication_method is not None
                else "Credential contract does not match a supported authentication preset."
            )
        ),
    )


def _enabled_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    return _readiness_check(
        "enabled",
        "Enabled state",
        "pass" if row.enabled else "error",
        "Profile is enabled." if row.enabled else "Profile is disabled.",
    )


def _auth_state_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    auth_state = row.auth_state.value if row.auth_state else None
    disabled_reason = row.disabled_reason.value if row.disabled_reason else None
    connected = auth_state == ProviderProfileAuthState.CONNECTED.value
    consistent = not row.enabled or disabled_reason is None
    if connected and consistent:
        message = "Profile credentials are connected."
    elif connected:
        message = f"Profile is enabled but still has disabled reason {disabled_reason}."
    elif disabled_reason:
        message = (
            f"Profile credentials are {auth_state or 'unknown'} ({disabled_reason})."
        )
    else:
        message = f"Profile credentials are {auth_state or 'unknown'}."
    return _readiness_check(
        "auth_state",
        "Activation state",
        "pass" if connected and consistent else "error",
        message,
    )


def _secret_refs_check(
    row: ManagedAgentProviderProfile,
    *,
    credential_source: str | None,
    managed_secret_statuses: dict[str, str],
    secret_ref_results: dict[str, _SecretRefParseResult],
) -> dict[str, str]:
    api_key_setup_pending = (
        row.auth_state == ProviderProfileAuthState.API_KEY_PENDING
    )
    if credential_source != "secret_ref" and not api_key_setup_pending:
        return _readiness_check(
            "secret_refs",
            "SecretRef bindings",
            "pass",
            "SecretRef bindings are not required for this credential source.",
        )
    required_roles = required_secret_roles(row.runtime_id, row.provider_id)
    if not row.secret_refs:
        required_suffix = (
            ": " + ", ".join(required_roles) if required_roles else ""
        )
        return _readiness_check(
            "secret_refs",
            "SecretRef bindings",
            "error",
            "API-key credential setup requires SecretRef bindings"
            f"{required_suffix}.",
        )

    problems: list[str] = [
        f"Missing required SecretRef role {role}"
        for role in required_roles
        if role not in row.secret_refs or not row.secret_refs.get(role)
    ]
    for role, result in secret_ref_results.items():
        if result.error:
            problems.append(f"{role} binding has invalid SecretRef ({result.error})")
            continue
        if not result.parsed or result.parsed.backend != SecretBackend.DB_ENCRYPTED:
            continue
        status = managed_secret_statuses.get(result.parsed.locator)
        if status is None:
            problems.append(
                f"{role}=[REDACTED] binding references managed secret db://{result.parsed.locator} was not found"
            )
        elif status != SecretStatus.ACTIVE.value:
            problems.append(
                f"{role}=[REDACTED] binding references managed secret db://{result.parsed.locator} is {status}"
            )

    return _readiness_check(
        "secret_refs",
        "SecretRef bindings",
        "error" if problems else "pass",
        (
            "; ".join(problems)
            if problems
            else "SecretRef bindings are syntactically valid."
        ),
    )


def _oauth_volume_check(
    row: ManagedAgentProviderProfile,
    *,
    credential_source: str | None,
    materialization_mode: str | None,
) -> dict[str, str]:
    oauth_required = (
        credential_source == "oauth_volume" or materialization_mode == "oauth_home"
    )
    if not oauth_required:
        return _readiness_check(
            "oauth_volume",
            "OAuth volume",
            "pass",
            "OAuth volume metadata is not required for this credential source.",
        )

    missing_oauth = [
        field_name
        for field_name, value in {
            "volume_ref": row.volume_ref,
            "volume_mount_path": row.volume_mount_path,
        }.items()
        if not value
    ]
    return _readiness_check(
        "oauth_volume",
        "OAuth volume",
        "error" if missing_oauth else "pass",
        (
            "Missing OAuth volume metadata: " + ", ".join(missing_oauth)
            if missing_oauth
            else "OAuth volume metadata is present."
        ),
    )


def _concurrency_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    has_capacity = bool(row.max_parallel_runs and row.max_parallel_runs > 0)
    requires_exclusive_capacity = is_codex_oauth_profile(
        runtime_id=row.runtime_id,
        credential_source=row.credential_source,
        materialization_mode=row.runtime_materialization_mode,
    )
    valid_capacity = has_capacity and (
        not requires_exclusive_capacity or row.max_parallel_runs == 1
    )
    if requires_exclusive_capacity and row.max_parallel_runs != 1:
        message = CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR
    elif has_capacity:
        message = f"Profile allows {row.max_parallel_runs} parallel run(s)."
    else:
        message = "Profile has no available configured concurrency."
    return _readiness_check(
        "concurrency",
        "Concurrency",
        "pass" if valid_capacity else "error",
        message,
    )


def _cooldown_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    cooldown = row.cooldown_after_429_seconds
    valid_cooldown = cooldown is not None and cooldown >= 0
    return _readiness_check(
        "cooldown",
        "Cooldown",
        "pass" if valid_cooldown else "error",
        f"Cooldown after provider rate limit is {cooldown}s.",
    )


def _provider_validation_check(row: ManagedAgentProviderProfile) -> dict[str, str]:
    provider_readiness = (
        row.command_behavior.get("auth_readiness")
        if isinstance(row.command_behavior, dict)
        else None
    )
    if not isinstance(provider_readiness, dict):
        return _readiness_check(
            "provider_validation",
            "Provider validation",
            "warning",
            "No provider-specific validation metadata is available.",
        )

    launch_ready = provider_readiness.get("launch_ready")
    if launch_ready is None:
        launch_ready = provider_readiness.get("launchReady")
    failure_reason = provider_readiness.get("failure_reason") or provider_readiness.get(
        "failureReason"
    )
    return _readiness_check(
        "provider_validation",
        "Provider validation",
        "pass" if launch_ready is not False else "error",
        (
            "Provider validation reports launch ready."
            if launch_ready is not False
            else (
                "Provider validation blocks launch: "
                f"{failure_reason or 'unknown reason'}"
            )
        ),
    )


def _row_to_dict(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str] | None = None,
    secret_ref_results: dict[str, _SecretRefParseResult] | None = None,
) -> dict[str, Any]:
    readiness = _provider_profile_readiness(
        row,
        managed_secret_statuses=managed_secret_statuses,
        secret_ref_results=secret_ref_results,
    )
    model_tiers, default_model_tier = coerce_model_effort_tier_policy(
        model_tiers=row.model_tiers,
        default_model_tier=row.default_model_tier,
        legacy_default_model=row.default_model,
        legacy_default_effort=row.default_effort,
        empty_as_missing=True,
    )
    creation_capabilities = provider_profile_creation_capabilities(
        runtime_id=row.runtime_id,
        provider_id=row.provider_id,
    )
    authentication_method = infer_authentication_method(
        credential_source=row.credential_source,
        runtime_materialization_mode=row.runtime_materialization_mode,
        authentication_methods=creation_capabilities["authentication_methods"],
        auth_state=row.auth_state,
        last_auth_method=row.last_auth_method,
    )
    payload = {
        "profile_id": row.profile_id,
        "runtime_id": row.runtime_id,
        "provider_id": row.provider_id,
        "provider_label": row.provider_label,
        "default_model": row.default_model,
        "default_effort": row.default_effort,
        "model_tiers": model_tiers,
        "default_model_tier": default_model_tier,
        "model_overrides": row.model_overrides or {},
        "credential_generation": row.credential_generation,
        "capacity_scope_ref": row.capacity_scope_ref,
        "model_catalog_evidence": row.model_catalog_evidence_json,
        "credential_source": (
            row.credential_source.value if row.credential_source else None
        ),
        "runtime_materialization_mode": (
            row.runtime_materialization_mode.value
            if row.runtime_materialization_mode
            else None
        ),
        "volume_ref": row.volume_ref,
        "volume_mount_path": row.volume_mount_path,
        "account_label": row.account_label,
        "tags": row.tags or [],
        "priority": row.priority,
        "secret_refs": row.secret_refs or {},
        "clear_env_keys": row.clear_env_keys or [],
        "env_template": row.env_template or {},
        "file_templates": row.file_templates or [],
        "home_path_overrides": row.home_path_overrides or {},
        "command_behavior": row.command_behavior or {},
        "max_parallel_runs": row.max_parallel_runs,
        "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
        "rate_limit_policy": (
            row.rate_limit_policy.value if row.rate_limit_policy else None
        ),
        "enabled": row.enabled,
        "is_default": row.is_default,
        "max_lease_duration_seconds": row.max_lease_duration_seconds,
        "auth_state": row.auth_state.value if row.auth_state else None,
        "disabled_reason": (row.disabled_reason.value if row.disabled_reason else None),
        "first_authenticated_at": (
            row.first_authenticated_at.isoformat()
            if row.first_authenticated_at
            else None
        ),
        "last_validated_at": (
            row.last_validated_at.isoformat() if row.last_validated_at else None
        ),
        "last_auth_method": (
            row.last_auth_method.value if row.last_auth_method else None
        ),
        "launch_ready": readiness["launch_ready"],
        "readiness": readiness,
        "authentication_method": authentication_method,
        "creation_capabilities": creation_capabilities,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    for key in ("env_template", "command_behavior"):
        payload[key] = redact_sensitive_payload(payload[key])
    payload["file_templates"] = redact_profile_file_templates(payload["file_templates"])
    return payload


from api_service.services.provider_profile_service import (
    FirstPartyOAuthProfile,
    apply_oauth_connected_state,
    apply_oauth_validation_failure,
    clear_oauth_home_path_overrides,
    get_first_party_oauth_profile,
    normalize_runtime_default_profile,
    oauth_auth_actions_for_profile,
    sync_provider_profile_manager,
    update_oauth_command_behavior,
)

# trigger full CI for opencode generic host wiring
