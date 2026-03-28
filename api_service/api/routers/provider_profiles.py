"""CRUD API for managed agent provider profiles."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    ManagedAgentProviderProfile,
    ManagedAgentRateLimitPolicy,
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-profiles", tags=["provider-profiles"])


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
    
    credential_source: str = Field(..., pattern="^(oauth_volume|secret_ref|none)$")
    runtime_materialization_mode: str = Field(..., pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$")
    
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    
    tags: Optional[list[str]] = None
    priority: int = Field(default=100)
    
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, str]] = None
    file_templates: Optional[list[dict[str, str]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None

    max_parallel_runs: int = Field(default=1, ge=1)
    cooldown_after_429_seconds: int = Field(default=300, ge=0)
    rate_limit_policy: str = Field(default="backoff", pattern="^(backoff|queue|fail_fast)$")
    enabled: bool = True
    max_lease_duration_seconds: int = Field(default=7200, ge=60)

    @field_validator("env_template", mode="before")
    @classmethod
    def _stringify_runtime_env(
        cls, value: object
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env_template must be a JSON object")
        out: dict[str, str] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if raw_val is None:
                out[key] = ""
            else:
                out[key] = str(raw_val)
        return out

    @field_validator("secret_refs", mode="after")
    @classmethod
    def _validate_secret_refs(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_secret_refs_helper(value)

    @model_validator(mode="after")
    def _validate_runtime_env(self) -> "ProviderProfileCreate":
        return self


class ProviderProfileUpdate(BaseModel):
    provider_id: Optional[str] = Field(default=None, max_length=64)
    provider_label: Optional[str] = None
    credential_source: Optional[str] = Field(default=None, pattern="^(oauth_volume|secret_ref|none)$")
    runtime_materialization_mode: Optional[str] = Field(default=None, pattern="^(oauth_home|api_key_env|env_bundle|config_bundle|composite)$")
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    tags: Optional[list[str]] = None
    priority: Optional[int] = None
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, str]] = None
    file_templates: Optional[list[dict[str, str]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None

    max_parallel_runs: Optional[int] = Field(default=None, ge=1)

    @field_validator("env_template", mode="before")
    @classmethod
    def _stringify_runtime_env_update(
        cls, value: object
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env_template must be a JSON object")
        out: dict[str, str] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if raw_val is None:
                out[key] = ""
            else:
                out[key] = str(raw_val)
        return out
        
    cooldown_after_429_seconds: Optional[int] = Field(default=None, ge=0)
    rate_limit_policy: Optional[str] = Field(
        default=None, pattern="^(backoff|queue|fail_fast)$"
    )
    enabled: Optional[bool] = None
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
    credential_source: str
    runtime_materialization_mode: str
    volume_ref: Optional[str]
    volume_mount_path: Optional[str]
    account_label: Optional[str]
    tags: Optional[list[str]] = None
    priority: int = 100
    secret_refs: Optional[dict[str, str]] = None
    clear_env_keys: Optional[list[str]] = None
    env_template: Optional[dict[str, str]] = None
    file_templates: Optional[list[dict[str, str]]] = None
    home_path_overrides: Optional[dict[str, str]] = None
    command_behavior: Optional[dict[str, Any]] = None
    max_parallel_runs: int
    cooldown_after_429_seconds: int
    rate_limit_policy: str
    enabled: bool
    max_lease_duration_seconds: int
    created_at: Optional[str]
    updated_at: Optional[str]

    model_config = {"from_attributes": True}


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
) -> list[dict[str, Any]]:
    stmt = select(ManagedAgentProviderProfile)
    if runtime_id:
        stmt = stmt.where(ManagedAgentProviderProfile.runtime_id == runtime_id)
    if enabled_only:
        stmt = stmt.where(ManagedAgentProviderProfile.enabled.is_(True))

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [_row_to_dict(r) for r in rows]


@router.get("/{profile_id}", response_model=ProviderProfileResponse)
async def get_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    row = await session.get(ManagedAgentProviderProfile, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _row_to_dict(row)


@router.post("", response_model=ProviderProfileResponse, status_code=201)
async def create_profile(
    body: ProviderProfileCreate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    existing = await session.get(ManagedAgentProviderProfile, body.profile_id)
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")

    profile = ManagedAgentProviderProfile(
        profile_id=body.profile_id,
        runtime_id=body.runtime_id,
        provider_id=body.provider_id,
        provider_label=body.provider_label,
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
        max_parallel_runs=body.max_parallel_runs,
        cooldown_after_429_seconds=body.cooldown_after_429_seconds,
        rate_limit_policy=ManagedAgentRateLimitPolicy(body.rate_limit_policy),
        enabled=body.enabled,
        max_lease_duration_seconds=body.max_lease_duration_seconds,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    await sync_provider_profile_manager(session=session, runtime_id=body.runtime_id)

    return _row_to_dict(profile)


@router.patch("/{profile_id}", response_model=ProviderProfileResponse)
async def update_profile(
    profile_id: str,
    body: ProviderProfileUpdate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "rate_limit_policy" and value is not None:
            value = ManagedAgentRateLimitPolicy(value)
        elif key == "credential_source" and value is not None:
            value = ProviderCredentialSource(value)
        elif key == "runtime_materialization_mode" and value is not None:
            value = RuntimeMaterializationMode(value)
        setattr(profile, key, value)

    await session.commit()
    await session.refresh(profile)
    await sync_provider_profile_manager(session=session, runtime_id=profile.runtime_id)
    return _row_to_dict(profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> None:
    profile = await session.get(ManagedAgentProviderProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    runtime_id = profile.runtime_id
    await session.delete(profile)
    await session.commit()
    await sync_provider_profile_manager(session=session, runtime_id=runtime_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: ManagedAgentProviderProfile) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
        "runtime_id": row.runtime_id,
        "provider_id": row.provider_id,
        "provider_label": row.provider_label,
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
        "max_lease_duration_seconds": row.max_lease_duration_seconds,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


from api_service.services.auth_profile_service import sync_provider_profile_manager
