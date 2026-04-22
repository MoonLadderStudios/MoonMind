"""CRUD API for managed agent provider profiles."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.models import (
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    ManagedAgentProviderProfile,
    ManagedAgentRateLimitPolicy,
    ManagedSecret,
    SecretStatus,
    User,
)
from moonmind.schemas.agent_runtime_models import validate_codex_oauth_profile_refs
from moonmind.utils.logging import redact_profile_file_templates, redact_sensitive_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-profiles", tags=["provider-profiles"])
_claude_manual_validation_client: httpx.AsyncClient | None = None


def validate_secret_refs_helper(value: dict[str, str] | None) -> dict[str, str] | None:
    if not value:
        return value
    from moonmind.auth.secret_refs import parse_secret_ref, SecretReferenceError
    for k, v in value.items():
        if not v:
            continue
        try:
            parse_secret_ref(v)
        except SecretReferenceError as e:
            raise ValueError(f"Invalid secret reference {v!r} for key {k!r}: {e}")
    return value


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ProviderProfileCreate(BaseModel):
    profile_id: str = Field(..., max_length=128)
    runtime_id: str = Field(..., max_length=64)
    provider_id: str = Field(default="unknown", max_length=64)
    provider_label: Optional[str] = None
    default_model: Optional[str] = None
    model_overrides: Optional[dict[str, str]] = None
    
    credential_source: str = Field(..., pattern="^(oauth_volume|secret_ref|none)$")
    runtime_materialization_mode: str = Field(..., pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$")
    
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    
    tags: Optional[list[str]] = None
    priority: int = Field(default=100)
    
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, Any]] = None
    file_templates: Optional[list[dict[str, Any]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None

    max_parallel_runs: int = Field(default=1, ge=1)
    cooldown_after_429_seconds: int = Field(default=900, ge=0)
    rate_limit_policy: str = Field(default="backoff", pattern="^(backoff|queue|fail_fast)$")
    enabled: bool = True
    is_default: bool = False
    max_lease_duration_seconds: int = Field(default=7200, ge=60)

    @field_validator("env_template", mode="before")
    @classmethod
    def _stringify_runtime_env(
        cls, value: object
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env_template must be a JSON object")
        return value

    @field_validator("secret_refs", mode="after")
    @classmethod
    def _validate_secret_refs(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_secret_refs_helper(value)

    @model_validator(mode="after")
    def _validate_runtime_env(self) -> "ProviderProfileCreate":
        validate_codex_oauth_profile_refs(
            runtime_id=self.runtime_id,
            credential_source=self.credential_source,
            runtime_materialization_mode=self.runtime_materialization_mode,
            volume_ref=self.volume_ref,
            volume_mount_path=self.volume_mount_path,
            volume_ref_field_name="volume_ref",
            volume_mount_path_field_name="volume_mount_path",
        )
        return self


class ProviderProfileUpdate(BaseModel):
    provider_id: Optional[str] = Field(default=None, max_length=64)
    provider_label: Optional[str] = None
    default_model: Optional[str] = None
    model_overrides: Optional[dict[str, str]] = None
    credential_source: Optional[str] = Field(default=None, pattern="^(oauth_volume|secret_ref|none)$")
    runtime_materialization_mode: Optional[str] = Field(default=None, pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$")
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
    def _stringify_runtime_env_update(
        cls, value: object
    ) -> dict[str, Any] | None:
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

    @field_validator("secret_refs", mode="after")
    @classmethod
    def _validate_secret_refs_update(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_secret_refs_helper(value)

    @model_validator(mode="after")
    def _validate_runtime_env_update(self) -> "ProviderProfileUpdate":
        return self

class ProviderProfileResponse(BaseModel):
    profile_id: str
    runtime_id: str
    provider_id: str
    provider_label: Optional[str]
    default_model: Optional[str] = None
    model_overrides: dict[str, str] = Field(default_factory=dict)
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
    created_at: Optional[str]
    updated_at: Optional[str]

    model_config = {"from_attributes": True}


class ClaudeManualAuthCommitRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=8192)
    account_label: Optional[str] = None


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


# ---------------------------------------------------------------------------
# Dependency: DB session
# ---------------------------------------------------------------------------


def _get_session() -> Any:
    """Return the session dependency. Resolved at import-time from the app."""
    from api_service.db.base import get_async_session

    return get_async_session


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
    return [_row_to_dict(r) for r in rows if _can_view_profile(r, current_user)]


@router.get("/{profile_id}", response_model=ProviderProfileResponse)
async def get_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    row = await session.get(ManagedAgentProviderProfile, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _can_view_profile(row, current_user):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this provider profile.",
        )
    return _row_to_dict(row)


@router.post("", response_model=ProviderProfileResponse, status_code=201)
async def create_profile(
    body: ProviderProfileCreate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    existing = await session.get(ManagedAgentProviderProfile, body.profile_id)
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")

    profile = ManagedAgentProviderProfile(
        profile_id=body.profile_id,
        runtime_id=body.runtime_id,
        provider_id=body.provider_id,
        provider_label=body.provider_label,
        default_model=body.default_model,
        model_overrides=body.model_overrides,
        credential_source=ProviderCredentialSource(body.credential_source),
        runtime_materialization_mode=RuntimeMaterializationMode(body.runtime_materialization_mode),
        volume_ref=body.volume_ref,
        volume_mount_path=body.volume_mount_path,
        account_label=body.account_label,
        tags=body.tags,
        priority=body.priority,
        secret_refs=body.secret_refs,
        clear_env_keys=body.clear_env_keys,
        env_template=body.env_template,
        file_templates=body.file_templates,
        home_path_overrides=body.home_path_overrides,
        command_behavior=body.command_behavior,
        owner_user_id=getattr(current_user, "id", None),
        max_parallel_runs=body.max_parallel_runs,
        cooldown_after_429_seconds=body.cooldown_after_429_seconds,
        rate_limit_policy=ManagedAgentRateLimitPolicy(body.rate_limit_policy),
        enabled=body.enabled,
        is_default=False,
        max_lease_duration_seconds=body.max_lease_duration_seconds,
    )
    _validate_codex_oauth_profile_row(profile)
    session.add(profile)
    await session.flush()
    await normalize_runtime_default_profile(
        session=session,
        runtime_id=body.runtime_id,
        preferred_profile_id=profile.profile_id if body.is_default else None,
    )
    await session.commit()
    await session.refresh(profile)

    await sync_provider_profile_manager(session=session, runtime_id=body.runtime_id)

    return _row_to_dict(profile)


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
    _require_profile_management(profile, current_user)

    update_data = body.model_dump(exclude_unset=True)
    requested_is_default = update_data.pop("is_default", None)
    for key, value in update_data.items():
        if key == "rate_limit_policy" and value is not None:
            value = ManagedAgentRateLimitPolicy(value)
        elif key == "credential_source" and value is not None:
            value = ProviderCredentialSource(value)
        elif key == "runtime_materialization_mode" and value is not None:
            value = RuntimeMaterializationMode(value)
        setattr(profile, key, value)

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
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)
    return _row_to_dict(profile)


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
    profile.volume_ref = None
    profile.volume_mount_path = None
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
    behavior = dict(profile.command_behavior or {})
    behavior.update(
        {
            "auth_strategy": "claude_credential_methods",
            "auth_state": "connected",
            "auth_actions": ["use_api_key"],
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
    await normalize_runtime_default_profile(session=session, runtime_id=profile.runtime_id)
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


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
    current_user: User = Depends(get_current_user()),
) -> None:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
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


def _looks_like_claude_manual_token(token: str) -> bool:
    return token.startswith("sk-ant-") and len(token) >= 12


def _claude_manual_secret_slug(profile_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", profile_id.lower()).strip("-")
    if not normalized:
        normalized = "claude-anthropic"
    digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:16]
    return f"{normalized}-{digest}-token"


async def _upsert_managed_secret(
    *,
    session: AsyncSession,
    slug: str,
    plaintext: str,
    details: dict[str, Any],
) -> ManagedSecret:
    result = await session.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
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


def _get_claude_manual_validation_client() -> httpx.AsyncClient:
    global _claude_manual_validation_client
    if (
        _claude_manual_validation_client is None
        or _claude_manual_validation_client.is_closed
    ):
        _claude_manual_validation_client = httpx.AsyncClient(timeout=10.0)
    return _claude_manual_validation_client


def _row_to_dict(row: ManagedAgentProviderProfile) -> dict[str, Any]:
    payload = {
        "profile_id": row.profile_id,
        "runtime_id": row.runtime_id,
        "provider_id": row.provider_id,
        "provider_label": row.provider_label,
        "default_model": row.default_model,
        "model_overrides": row.model_overrides or {},
        "credential_source": row.credential_source.value if row.credential_source else None,
        "runtime_materialization_mode": row.runtime_materialization_mode.value if row.runtime_materialization_mode else None,
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
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    for key in ("env_template", "command_behavior"):
        payload[key] = redact_sensitive_payload(payload[key])
    payload["file_templates"] = redact_profile_file_templates(payload["file_templates"])
    return payload


from api_service.services.provider_profile_service import (
    normalize_runtime_default_profile,
    sync_provider_profile_manager,
)
