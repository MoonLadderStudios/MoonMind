"""CRUD API for managed agent auth profiles."""

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
    ManagedAgentAuthMode,
    ManagedAgentAuthProfile,
    ManagedAgentRateLimitPolicy,
)
from moonmind.schemas.agent_runtime_models import _contains_sensitive_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth-profiles", tags=["auth-profiles"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AuthProfileCreate(BaseModel):
    profile_id: str = Field(..., max_length=128)
    runtime_id: str = Field(..., max_length=64)
    auth_mode: str = Field(..., pattern="^(oauth|api_key)$")
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    api_key_ref: Optional[str] = None
    runtime_env_overrides: Optional[dict[str, str]] = None
    api_key_env_var: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Env var to receive the resolved API key (e.g. ANTHROPIC_AUTH_TOKEN for MiniMax).",
    )
    max_parallel_runs: int = Field(default=1, ge=1)

    @field_validator("runtime_env_overrides", mode="before")
    @classmethod
    def _stringify_runtime_env(
        cls, value: object
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("runtime_env_overrides must be a JSON object")
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
    cooldown_after_429_seconds: int = Field(default=300, ge=0)
    rate_limit_policy: str = Field(default="backoff", pattern="^(backoff|queue|fail_fast)$")
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_runtime_env(self) -> "AuthProfileCreate":
        if self.runtime_env_overrides is not None and _contains_sensitive_key(
            self.runtime_env_overrides
        ):
            raise ValueError(
                "runtime_env_overrides must not contain credential-like key names or nested secrets"
            )
        if self.api_key_env_var is not None:
            candidate = str(self.api_key_env_var).strip()
            if not candidate:
                self.api_key_env_var = None
            elif not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", candidate):
                raise ValueError(
                    "api_key_env_var must look like an environment variable name "
                    "(e.g. ANTHROPIC_AUTH_TOKEN)"
                )
            else:
                self.api_key_env_var = candidate
        return self


class AuthProfileUpdate(BaseModel):
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    account_label: Optional[str] = None
    api_key_ref: Optional[str] = None
    runtime_env_overrides: Optional[dict[str, str]] = None
    api_key_env_var: Optional[str] = Field(
        default=None,
        max_length=64,
    )
    max_parallel_runs: Optional[int] = Field(default=None, ge=1)

    @field_validator("runtime_env_overrides", mode="before")
    @classmethod
    def _stringify_runtime_env_update(
        cls, value: object
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("runtime_env_overrides must be a JSON object")
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

    @model_validator(mode="after")
    def _validate_runtime_env_update(self) -> "AuthProfileUpdate":
        if self.runtime_env_overrides is not None and _contains_sensitive_key(
            self.runtime_env_overrides
        ):
            raise ValueError(
                "runtime_env_overrides must not contain credential-like key names or nested secrets"
            )
        if self.api_key_env_var is not None:
            candidate = str(self.api_key_env_var).strip()
            if not candidate:
                self.api_key_env_var = None
            elif not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", candidate):
                raise ValueError(
                    "api_key_env_var must look like an environment variable name "
                    "(e.g. ANTHROPIC_AUTH_TOKEN)"
                )
            else:
                self.api_key_env_var = candidate
        return self


class AuthProfileResponse(BaseModel):
    profile_id: str
    runtime_id: str
    auth_mode: str
    volume_ref: Optional[str]
    volume_mount_path: Optional[str]
    account_label: Optional[str]
    api_key_ref: Optional[str] = None
    runtime_env_overrides: Optional[dict[str, str]] = None
    api_key_env_var: Optional[str] = None
    max_parallel_runs: int
    cooldown_after_429_seconds: int
    rate_limit_policy: str
    enabled: bool
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


@router.get("", response_model=list[AuthProfileResponse])
async def list_profiles(
    runtime_id: Optional[str] = None,
    enabled_only: bool = False,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    stmt = select(ManagedAgentAuthProfile)
    if runtime_id:
        stmt = stmt.where(ManagedAgentAuthProfile.runtime_id == runtime_id)
    if enabled_only:
        stmt = stmt.where(ManagedAgentAuthProfile.enabled.is_(True))

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [_row_to_dict(r) for r in rows]


@router.get("/{profile_id}", response_model=AuthProfileResponse)
async def get_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    row = await session.get(ManagedAgentAuthProfile, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _row_to_dict(row)


@router.post("", response_model=AuthProfileResponse, status_code=201)
async def create_profile(
    body: AuthProfileCreate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    existing = await session.get(ManagedAgentAuthProfile, body.profile_id)
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")

    profile = ManagedAgentAuthProfile(
        profile_id=body.profile_id,
        runtime_id=body.runtime_id,
        auth_mode=ManagedAgentAuthMode(body.auth_mode),
        volume_ref=body.volume_ref,
        volume_mount_path=body.volume_mount_path,
        account_label=body.account_label,
        api_key_ref=body.api_key_ref,
        runtime_env_overrides=body.runtime_env_overrides,
        api_key_env_var=body.api_key_env_var,
        max_parallel_runs=body.max_parallel_runs,
        cooldown_after_429_seconds=body.cooldown_after_429_seconds,
        rate_limit_policy=ManagedAgentRateLimitPolicy(body.rate_limit_policy),
        enabled=body.enabled,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    await sync_auth_profile_manager(session=session, runtime_id=body.runtime_id)

    return _row_to_dict(profile)


@router.patch("/{profile_id}", response_model=AuthProfileResponse)
async def update_profile(
    profile_id: str,
    body: AuthProfileUpdate,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> dict[str, Any]:
    profile = await session.get(ManagedAgentAuthProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "rate_limit_policy" and value is not None:
            value = ManagedAgentRateLimitPolicy(value)
        setattr(profile, key, value)

    await session.commit()
    await session.refresh(profile)
    await sync_auth_profile_manager(session=session, runtime_id=profile.runtime_id)
    return _row_to_dict(profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    session: AsyncSession = Depends(_get_session()),  # type: ignore[assignment]
) -> None:
    profile = await session.get(ManagedAgentAuthProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    runtime_id = profile.runtime_id
    await session.delete(profile)
    await session.commit()
    await sync_auth_profile_manager(session=session, runtime_id=runtime_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: ManagedAgentAuthProfile) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
        "runtime_id": row.runtime_id,
        "auth_mode": row.auth_mode.value if row.auth_mode else None,
        "volume_ref": row.volume_ref,
        "volume_mount_path": row.volume_mount_path,
        "account_label": row.account_label,
        "api_key_ref": row.api_key_ref,
        "runtime_env_overrides": row.runtime_env_overrides or {},
        "api_key_env_var": row.api_key_env_var,
        "max_parallel_runs": row.max_parallel_runs,
        "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
        "rate_limit_policy": (
            row.rate_limit_policy.value if row.rate_limit_policy else None
        ),
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


from api_service.services.auth_profile_service import sync_auth_profile_manager
