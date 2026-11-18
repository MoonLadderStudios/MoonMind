"""Response and command schemas for cached provider account usage.

The projection is browser-safe: stable provider account/workspace IDs stay
internal (only ``binding_verified`` is exposed) and failures are typed codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    AccountUsageStatus,
    IdentityStatus,
    RefreshOutcome,
    RefreshState,
    UsageCompleteness,
    UsageWindowPeriod,
    UsageWindowScope,
    WindowFreshness,
)

MAX_REFRESH_PROFILE_IDS = 50


class ProviderAccountUsageWindow(BaseModel):
    key: str
    period: UsageWindowPeriod
    scope: UsageWindowScope
    model_label: str | None = None
    duration_seconds: int | None = None
    used_percent: float | None = Field(
        default=None, description="Provider-reported percentage used; may exceed 100."
    )
    remaining_percent: float | None = Field(
        default=None, description="Displayed remaining allowance, clamped to 0-100."
    )
    exhausted: bool | None = None
    reset_at: datetime | None = None
    source: str
    source_version: str | None = None
    observed_at: datetime
    fresh_until: datetime
    freshness: WindowFreshness
    last_known: bool = Field(description="True when the value is not a fresh reading.")


class ProviderAccountUsageIdentity(BaseModel):
    status: IdentityStatus
    email: str | None = None
    display_name: str | None = None
    plan: str | None = None
    source: str | None = None
    observed_at: datetime | None = None


class ProviderAccountUsageSummary(BaseModel):
    completeness: UsageCompleteness | None = None
    primary_window_key: str | None = None
    full_observed_at: datetime | None = None
    windows: list[ProviderAccountUsageWindow] = Field(default_factory=list)


class ProviderAccountUsageRefreshStatus(BaseModel):
    state: RefreshState
    request_id: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_failure: AccountUsageFailureCode | None = None
    next_allowed_at: datetime | None = None


class ProviderAccountUsageProjection(BaseModel):
    schema_version: int
    profile_id: str
    credential_generation: int
    snapshot_revision: int
    provider_id: str
    runtime_id: str
    binding_verified: bool
    status: AccountUsageStatus
    account: ProviderAccountUsageIdentity
    usage: ProviderAccountUsageSummary
    refresh: ProviderAccountUsageRefreshStatus


class ProviderAccountUsageRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_ids: list[str] = Field(min_length=1, max_length=MAX_REFRESH_PROFILE_IDS)
    trigger: Literal["manual", "visibility"] = "manual"

    @field_validator("profile_ids")
    @classmethod
    def _profile_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for profile_id in value:
            text = profile_id.strip()
            if not text or len(text) > 128:
                raise ValueError(
                    "profile_ids must be non-empty and at most 128 characters"
                )
            if text not in cleaned:
                cleaned.append(text)
        return cleaned


class ProviderAccountUsageRefreshResult(BaseModel):
    profile_id: str
    credential_generation: int
    outcome: RefreshOutcome = Field(
        description="accepted means collection was requested, not that data changed."
    )
    refresh_state: RefreshState
    request_id: str | None = None
    coalesced: bool = False
    retry_after_seconds: int | None = None
    next_allowed_at: datetime | None = None


class ProviderAccountUsageRefreshResponse(BaseModel):
    results: list[ProviderAccountUsageRefreshResult]


class ProviderAccountUsageRefreshError(BaseModel):
    code: Literal["provider_profiles_not_found"]
    message: str
    profile_ids: list[str]


class ProviderAccountUsageRefreshErrorResponse(BaseModel):
    detail: ProviderAccountUsageRefreshError
