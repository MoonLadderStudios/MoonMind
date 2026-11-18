"""Native Claude status-line quota publication (MoonLadderStudios/MoonMind#5365)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from api_service.db.models import Base, ProviderProfileSlotLease
from moonmind.omnigent.native_account_usage import NativeAccountUsagePublisher
from tests.unit.services.account_usage_support import (
    T0,
    Clock,
    add_profiles,
    make_profile,
    observation_row,
    sqlite_sessions,
)

pytestmark = pytest.mark.asyncio
AUTHORIZATION = {
    "providerProfileId": "alpha",
    "providerLeaseRef": "provider-profile-lease:lease-1",
    "credentialGeneration": 1,
}


def _event(**windows) -> dict:
    return {
        "type": "session.usage",
        "conversation_id": "session-1",
        "rate_limits": {**windows, "claude_code_version": "2.1.281"},
    }


def _window(used, *, at, reset=None) -> dict:
    return {
        "used_percentage": used,
        "resets_at": int(reset.timestamp()) if reset else None,
        "observed_at": at.timestamp(),
    }


@pytest_asyncio.fixture
async def env(tmp_path):
    engine, sessions = await sqlite_sessions(tmp_path)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[ProviderProfileSlotLease.__table__]
            )
        )
    await add_profiles(
        sessions,
        make_profile(
            "alpha",
            runtime_id="claude_code",
            provider_id="anthropic",
            volume_mount_path="/home/app/.claude",
        ),
        make_profile("codex"),
    )
    clock = Clock()

    async def lease(profile_id="alpha", **overrides):
        values = {
            "runtime_id": "claude_code",
            "workflow_id": "agent-run-1",
            "profile_id": profile_id,
            "lease_id": "lease-1",
            "owner_id": "agent-run-1",
            "credential_generation": 1,
            "lease_state": "held",
            "expires_at": T0 + timedelta(hours=1),
        }
        values.update(overrides)
        async with sessions() as session:
            session.add(ProviderProfileSlotLease(**values))
            await session.commit()

    def publisher(**authorization):
        return NativeAccountUsagePublisher(
            profile_authorization={**AUTHORIZATION, **authorization},
            session_id="session-1",
            session_factory=sessions,
            clock=clock,
        )

    yield sessions, clock, lease, publisher
    await engine.dispose()


async def _windows(sessions) -> dict:
    row = await observation_row(sessions, "alpha")
    return ((row or {}).get("usage_json") or {}).get("windows") or {}


async def test_weekly_quota_is_published_for_the_held_lease(env) -> None:
    sessions, clock, lease, publisher = env
    await lease()
    reset = T0 + timedelta(days=2)

    outcome = await publisher().observe(
        _event(seven_day=_window(42, at=T0, reset=reset))
    )

    assert outcome == "accepted"
    weekly = (await _windows(sessions))["seven_day"]
    assert weekly["used_percent"] == 42.0
    assert weekly["period"] == "weekly" and weekly["scope"] == "general"
    assert weekly["source"] == "claude_status_line"
    assert weekly["source_version"] == "2.1.281"
    assert weekly["reset_at"] == reset.isoformat()
    assert weekly["observed_at"] == T0.isoformat()


async def test_cached_telemetry_keeps_its_time_and_partial_updates_stay_separate(
    env,
) -> None:
    sessions, clock, lease, publisher = env
    await lease()
    native = publisher()
    early = T0 - timedelta(minutes=30)
    await native.observe(
        _event(seven_day=_window(40, at=early), five_hour=_window(10, at=early))
    )

    # The same cached reading arrives again later: nothing is refreshed.
    clock.advance(minutes=10)
    again = _event(seven_day=_window(40, at=early), five_hour=_window(10, at=early))
    assert await native.observe(again) == "duplicate"
    assert await publisher().observe(again) == "accepted"
    windows = await _windows(sessions)
    assert windows["seven_day"]["observed_at"] == early.isoformat()

    # Only the short window changed: the weekly reading keeps its old time.
    newer = T0 + timedelta(minutes=5)
    await native.observe(
        _event(seven_day=_window(40, at=early), five_hour=_window(11, at=newer))
    )
    windows = await _windows(sessions)
    assert windows["five_hour"]["used_percent"] == 11.0
    assert windows["five_hour"]["observed_at"] == newer.isoformat()
    assert windows["seven_day"]["observed_at"] == early.isoformat()


async def test_weekly_quota_without_context_window_fields_still_counts(env) -> None:
    sessions, _, lease, publisher = env
    await lease()
    event = _event(seven_day=_window(5, at=T0))
    assert "context_window" not in event and "context_tokens" not in event
    assert await publisher().observe(event) == "accepted"


@pytest.mark.parametrize(
    "overrides",
    [
        {"lease_state": "released"},
        {"expires_at": T0 - timedelta(seconds=1)},
        {"profile_id": "codex"},
    ],
)
async def test_an_old_or_foreign_owner_cannot_publish(env, overrides) -> None:
    sessions, _, lease, publisher = env
    await lease(**overrides)

    assert await publisher().observe(_event(seven_day=_window(5, at=T0))) == (
        "owner_expired"
    )
    assert await _windows(sessions) == {}


async def test_a_reconnected_profile_rejects_the_old_sessions_quota(env) -> None:
    sessions, _, lease, publisher = env
    await lease(credential_generation=2)
    assert (
        await publisher().observe(_event(seven_day=_window(5, at=T0)))
        == "stale_generation"
    )
    assert (
        await publisher(credentialGeneration=2).observe(
            _event(seven_day=_window(5, at=T0))
        )
        == "stale_generation"
    )
    assert await _windows(sessions) == {}


async def test_only_this_sessions_claude_profile_publishes(env) -> None:
    sessions, _, lease, publisher = env
    await lease(profile_id="codex", runtime_id="codex_cli")
    child = {**_event(seven_day=_window(5, at=T0)), "conversation_id": "child"}
    assert await publisher().observe(child) == "ignored"
    assert (
        await publisher(providerProfileId="codex").observe(
            _event(seven_day=_window(5, at=T0))
        )
        == "not_supported"
    )
    assert await publisher(providerLeaseRef=None).observe(
        _event(seven_day=_window(5, at=T0))
    ) == ("unbound")
    # Usage events without quota are not this publisher's business.
    assert (
        await publisher().observe({"type": "session.usage", "context_tokens": 3})
        is None
    )


async def test_a_storage_failure_never_reaches_the_event_stream(env) -> None:
    _, clock, _, _ = env

    def broken():
        raise RuntimeError("database unavailable")

    publisher = NativeAccountUsagePublisher(
        profile_authorization=AUTHORIZATION,
        session_id="session-1",
        session_factory=broken,
        clock=clock,
    )
    assert await publisher.observe(_event(seven_day=_window(5, at=T0))) == "failed"


# -- Native Codex owner (MoonLadderStudios/MoonMind#5364) ----------------------------


def _codex_event(used=40, *, at=T0, account_id="acct-1111", **extra) -> dict:
    bucket = {
        "limit_id": "codex",
        "limit_name": None,
        "model": None,
        "primary": {"used_percent": 5, "window_duration_mins": 300, "resets_at": None},
        "secondary": {
            "used_percent": used,
            "window_duration_mins": 10080,
            "resets_at": int((T0 + timedelta(days=3)).timestamp()),
        },
    }
    return {
        "type": "session.usage",
        "conversation_id": "session-1",
        "codex_account_usage": {
            "observed_at": at.timestamp(),
            "codex_version": "0.170.0",
            "account": {
                "type": "chatgpt",
                "email": "person@example.com",
                "plan_type": "plus",
                "workspace_name": None,
            },
            "routing_account_id": account_id,
            "account_id": account_id,
            "rate_limits": bucket,
            "rate_limits_by_limit_id": {"codex": bucket},
            **extra,
        },
    }


@pytest_asyncio.fixture
async def codex_env(env):
    sessions, clock, lease, publisher = env
    await lease(profile_id="codex", runtime_id="codex_cli")

    def codex_publisher(**authorization):
        return publisher(providerProfileId="codex", **authorization)

    async def row():
        return await observation_row(sessions, "codex") or {}

    return clock, codex_publisher, row


async def test_codex_owner_read_is_a_full_snapshot_bound_to_its_account(
    codex_env,
) -> None:
    clock, publisher, row = codex_env

    assert await publisher().observe(_codex_event()) == "accepted"

    stored = await row()
    usage = stored["usage_json"]
    assert usage["completeness"] == "full"
    assert usage["primary_window_key"] == "codex.weekly"
    assert usage["windows"]["codex.weekly"]["used_percent"] == 40
    assert usage["windows"]["codex.weekly"]["source"] == "codex_rate_limits"
    assert stored["identity_json"]["email"] == "person@example.com"
    assert stored["bound_account_id"] == "acct-1111"


async def test_codex_owner_reads_never_go_backwards_or_cross_accounts(
    codex_env,
) -> None:
    clock, publisher, row = codex_env
    native = publisher()
    later = T0 + timedelta(minutes=2)
    assert await native.observe(_codex_event(41, at=later)) == "accepted"
    assert await native.observe(_codex_event(41, at=later)) == "duplicate"

    # A late response from an earlier read cannot replace the newer snapshot.
    assert await native.observe(_codex_event(10, at=T0)) == "out_of_order"
    # A switched login reports another account: rejected, nothing written.
    switched = _codex_event(90, at=later + timedelta(minutes=1), account_id="acct-2222")
    assert await native.observe(switched) == "account_mismatch"
    windows = (await row())["usage_json"]["windows"]
    assert windows["codex.weekly"]["used_percent"] == 41


async def test_codex_events_only_publish_for_a_codex_profile(codex_env, env) -> None:
    _, publisher, row = codex_env
    _, _, lease, claude_publisher = env
    await lease(lease_id="lease-claude")

    claude = claude_publisher(providerLeaseRef="provider-profile-lease:lease-claude")
    assert await claude.observe(_codex_event()) == "not_supported"
    assert await publisher().observe(_event(seven_day=_window(5, at=T0))) == (
        "not_supported"
    )
    unreadable = _codex_event()
    unreadable["codex_account_usage"]["observed_at"] = "soon"
    assert await publisher().observe(unreadable) == "unreadable"
    assert await row() == {} or (await row()).get("usage_json") is None
