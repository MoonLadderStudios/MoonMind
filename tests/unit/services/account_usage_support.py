"""Shared fixtures for provider account-usage service tests (#5362)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    ProviderAccountUsageObservation,
    ProviderCapacityScope,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from api_service.services.provider_account_usage import (
    AccountUsageProfileRef,
    CollectionRequest,
    DispatchResult,
)
from moonmind.provider_profiles.account_usage import AccountUsageObservation

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
TABLES = [
    ManagedAgentProviderProfile.__table__,
    ManagedAgentOAuthSession.__table__,
    ProviderAccountUsageObservation.__table__,
    ProviderCapacityScope.__table__,
]


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


class FakeDispatcher:
    """Collector port double; never collects inline."""

    def __init__(
        self,
        *,
        supported: set[str] | None = None,
        result: DispatchResult = DispatchResult.DISPATCHED,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.supported = supported
        self.result = result
        self.delay = delay
        self.error = error
        self.requests: list[CollectionRequest] = []
        self.supports_calls = 0

    def supports(self, profile: AccountUsageProfileRef) -> bool:
        self.supports_calls += 1
        return self.supported is None or profile.provider_id in self.supported

    async def dispatch(self, request: CollectionRequest) -> DispatchResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


async def sqlite_sessions(tmp_path) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/usage.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=TABLES)
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def make_profile(profile_id: str, **overrides: Any) -> ManagedAgentProviderProfile:
    values: dict[str, Any] = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
        "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
        "volume_ref": f"{profile_id}-volume",
        "volume_mount_path": "/home/app/.codex",
        "account_label": "person@example.com",
        "enabled": True,
        "auth_state": ProviderProfileAuthState.CONNECTED,
        "max_parallel_runs": 1,
        "priority": 100,
    }
    values.update(overrides)
    return ManagedAgentProviderProfile(**values)


async def add_profiles(sessions, *profiles: ManagedAgentProviderProfile) -> None:
    async with sessions() as session:
        session.add_all(profiles)
        await session.commit()


async def profile_snapshot(sessions, profile_id: str) -> dict[str, Any]:
    async with sessions() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile.__table__).where(
                ManagedAgentProviderProfile.profile_id == profile_id
            )
        )
        return dict(result.mappings().one())


async def observation_row(sessions, profile_id: str) -> dict[str, Any] | None:
    async with sessions() as session:
        result = await session.execute(
            select(ProviderAccountUsageObservation.__table__).where(
                ProviderAccountUsageObservation.profile_id == profile_id
            )
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


def observation(
    profile_id: str,
    *,
    generation: int = 1,
    attempt_id: str | None = None,
    owner_expires_at: datetime | None = None,
    account_id: str | None = None,
    workspace_id: str | None = None,
    identity: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> AccountUsageObservation:
    authority = (
        {"kind": "refresh_attempt", "attempt_id": attempt_id}
        if attempt_id
        else {
            "kind": "native_owner",
            "owner_expires_at": owner_expires_at or T0 + timedelta(days=365),
        }
    )
    return AccountUsageObservation(
        profile_id=profile_id,
        credential_generation=generation,
        provider_id="openai",
        runtime_id="codex_cli",
        authority=authority,
        account_id=account_id,
        workspace_id=workspace_id,
        identity=identity,
        usage=usage,
    )


def window(
    key: str,
    *,
    used: Any = 10,
    period: str = "weekly",
    observed_at: datetime | None = None,
    reset_at: datetime | None = None,
    scope: str = "general",
    model_label: str | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "key": key,
        "period": period,
        "scope": scope,
        "used_percent": used,
        "source": "fake_collector",
    }
    if model_label:
        values["model_label"] = model_label
    if observed_at is not None:
        values["observed_at"] = observed_at
    if reset_at is not None:
        values["reset_at"] = reset_at
    return values


def usage(
    completeness: str, observed_at: datetime, *windows: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return {
        "completeness": completeness,
        "observed_at": observed_at,
        "windows": list(windows),
        **extra,
    }
