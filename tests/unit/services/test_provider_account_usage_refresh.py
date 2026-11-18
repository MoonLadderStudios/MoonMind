"""Refresh coordination for cached provider account usage (#5362)."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from datetime import timedelta
from random import Random

import pytest
import pytest_asyncio
from sqlalchemy import select

from api_service.db.models import (
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    OAuthSessionStatus,
    ProviderCapacityScope,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
)
from api_service.services.provider_account_usage import (
    DispatchResult,
    ProviderAccountUsageService,
)
from moonmind.provider_profiles.account_usage import (
    MAX_AUTOMATIC_FAILURES,
    AccountUsageFailureCode,
    ObservationRejection,
    RefreshOutcome,
    RefreshTrigger,
)
from tests.unit.services.account_usage_support import (
    T0,
    Clock,
    FakeDispatcher,
    add_profiles,
    make_profile,
    observation,
    profile_snapshot,
    sqlite_sessions,
    usage,
    window,
)

pytestmark = pytest.mark.asyncio

MANUAL = RefreshTrigger.MANUAL
VISIBLE = RefreshTrigger.VISIBILITY


@pytest_asyncio.fixture
async def env(tmp_path):
    engine, sessions = await sqlite_sessions(tmp_path)
    clock = Clock()
    dispatcher = FakeDispatcher()

    def service(session, **kwargs):
        kwargs.setdefault("dispatcher", dispatcher)
        return ProviderAccountUsageService(
            session, clock=clock, rng=Random(3), **kwargs
        )

    async def call(method, *args, **kwargs):
        async with sessions() as session:
            return await getattr(service(session), method)(*args, **kwargs)

    async def refresh(*profile_ids, trigger=VISIBLE):
        async with sessions() as session:
            return await service(session).request_refresh(
                list(profile_ids), trigger=trigger
            )

    async def project(profile_id):
        async with sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            return (await service(session).load_projections([profile]))[profile_id]

    async def complete(result, *windows, completeness="full"):
        return await call(
            "ingest_observation",
            observation(
                result.profile_id,
                attempt_id=result.request_id,
                usage=usage(completeness, clock.now, *windows),
            ),
        )

    async def fail(result, code=AccountUsageFailureCode.RATE_LIMITED, retry_after=None):
        return await call(
            "record_collection_failure",
            profile_id=result.profile_id,
            credential_generation=result.credential_generation,
            attempt_id=result.request_id,
            code=code,
            retry_after=retry_after,
        )

    await add_profiles(sessions, make_profile("alpha"))
    yield dataclasses.make_dataclass(
        "Env",
        [
            "sessions",
            "clock",
            "dispatcher",
            "service",
            "call",
            "refresh",
            "project",
            "complete",
            "fail",
        ],
    )(sessions, clock, dispatcher, service, call, refresh, project, complete, fail)
    await engine.dispose()


async def test_staleness_policy_collects_only_when_due(env) -> None:
    [first] = await env.refresh("alpha")
    assert first.outcome is RefreshOutcome.ACCEPTED and not first.coalesced
    assert first.credential_generation == 1
    await env.complete(first, window("weekly"))

    env.clock.advance(minutes=4)
    [fresh] = await env.refresh("alpha")
    assert fresh.outcome is RefreshOutcome.ALREADY_FRESH
    assert fresh.retry_after_seconds == 60

    env.clock.advance(minutes=1)
    [due] = await env.refresh("alpha")
    assert due.outcome is RefreshOutcome.ACCEPTED
    assert len(env.dispatcher.requests) == 2


async def test_manual_refresh_is_limited_to_once_per_minute(env) -> None:
    [first] = await env.refresh("alpha", trigger=MANUAL)
    await env.complete(first, window("weekly"))
    env.clock.advance(seconds=20)
    [limited] = await env.refresh("alpha", trigger=MANUAL)
    assert limited.outcome is RefreshOutcome.BACKOFF
    assert limited.retry_after_seconds == 40
    env.clock.advance(seconds=40)
    [allowed] = await env.refresh("alpha", trigger=MANUAL)
    assert allowed.outcome is RefreshOutcome.ACCEPTED

    # A longer provider backoff wins over the manual allowance.
    await env.fail(allowed, retry_after="900")
    env.clock.advance(minutes=2)
    [backoff] = await env.refresh("alpha", trigger=MANUAL)
    assert backoff.outcome is RefreshOutcome.BACKOFF
    assert 13 * 60 <= backoff.retry_after_seconds <= 900 * 1.2
    assert len(env.dispatcher.requests) == 2


async def test_repeated_requests_coalesce_onto_one_attempt(env) -> None:
    async def tab():
        async with env.sessions() as session:
            return (
                await env.service(session).request_refresh(["alpha"], trigger=VISIBLE)
            )[0]

    results = await asyncio.gather(*(tab() for _ in range(6)))
    assert len(env.dispatcher.requests) == 1
    assert {r.request_id for r in results} == {env.dispatcher.requests[0].attempt_id}
    assert sum(not r.coalesced for r in results) == 1
    assert all(r.outcome is RefreshOutcome.ACCEPTED for r in results)

    # A retried request while the attempt is in flight reuses it too.
    [retry] = await env.refresh("alpha", trigger=MANUAL)
    assert retry.coalesced and len(env.dispatcher.requests) == 1


async def test_verified_shared_account_coalesces_and_shares_one_collection(env) -> None:
    await add_profiles(env.sessions, make_profile("beta"), make_profile("solo"))
    for profile_id in ("alpha", "beta"):
        await env.call(
            "ingest_observation", observation(profile_id, account_id="acct-1")
        )
    await env.call(
        "ingest_observation",
        observation(
            "solo",
            identity={
                "email": "person@example.com",
                "source": "fake",
                "observed_at": T0,
            },
        ),
    )
    alpha, beta, solo = await env.refresh("alpha", "beta", "solo")
    assert not alpha.coalesced and beta.coalesced
    assert beta.request_id == alpha.request_id
    assert not solo.coalesced and solo.request_id != alpha.request_id
    assert [r.profile_id for r in env.dispatcher.requests] == ["alpha", "solo"]

    result = await env.complete(alpha, window("weekly", used=33))
    assert result.shared_with == ("beta",)
    beta_view = await env.project("beta")
    assert beta_view["refresh"]["state"] == "idle"
    assert beta_view["usage"]["windows"][0]["used_percent"] == 33

    # The manual allowance is per verified account, not per profile.
    env.clock.advance(minutes=10)
    [manual] = await env.refresh("alpha", trigger=MANUAL)
    await env.complete(manual, window("weekly", used=34))
    [peer_manual] = await env.refresh("beta", trigger=MANUAL)
    assert peer_manual.outcome is RefreshOutcome.BACKOFF


async def test_peer_claim_between_read_and_write_still_coalesces(
    env, monkeypatch
) -> None:
    await add_profiles(env.sessions, make_profile("beta"))
    for profile_id in ("alpha", "beta"):
        await env.call(
            "ingest_observation", observation(profile_id, account_id="acct-1")
        )
    original = ProviderAccountUsageService._verified_peers
    interleaved: list = []

    async def peers_then_race(self, profile, account_key):
        peers = await original(self, profile, account_key)
        if profile.profile_id == "alpha" and not interleaved:
            # Another worker claims the peer after alpha read it as idle.
            interleaved.extend(await env.refresh("beta", trigger=MANUAL))
        return peers

    monkeypatch.setattr(ProviderAccountUsageService, "_verified_peers", peers_then_race)
    [alpha] = await env.refresh("alpha", trigger=MANUAL)
    [beta] = interleaved
    assert not beta.coalesced and beta.outcome is RefreshOutcome.ACCEPTED
    assert alpha.coalesced and alpha.request_id == beta.request_id
    assert [r.profile_id for r in env.dispatcher.requests] == ["beta"]


async def test_coalesced_attempt_completes_when_usage_is_unchanged(env) -> None:
    await add_profiles(env.sessions, make_profile("beta"))
    cached = usage("full", T0, window("weekly", used=33))
    for profile_id in ("alpha", "beta"):
        await env.call(
            "ingest_observation",
            observation(profile_id, account_id="acct-1", usage=cached),
        )
    env.clock.advance(minutes=6)
    alpha, beta = await env.refresh("alpha", "beta")
    assert beta.coalesced and beta.request_id == alpha.request_id

    # The collector re-delivers the observation both profiles already cache.
    result = await env.call(
        "ingest_observation",
        observation("alpha", attempt_id=alpha.request_id, usage=cached),
    )
    assert result.accepted and not result.changed
    env.clock.advance(minutes=3)
    beta_view = await env.project("beta")
    assert beta_view["refresh"]["state"] == "idle"
    assert beta_view["refresh"]["last_failure"] is None
    assert beta_view["refresh"]["last_success_at"] is not None


async def test_native_full_observation_restores_automatic_collection(env) -> None:
    for _ in range(MAX_AUTOMATIC_FAILURES):
        [attempt] = await env.refresh("alpha")
        assert attempt.outcome is RefreshOutcome.ACCEPTED
        failure = await env.fail(attempt, retry_after=1)
        env.clock.now = failure.next_allowed_at
    [stopped] = await env.refresh("alpha")
    assert stopped.outcome is RefreshOutcome.BACKOFF

    native = await env.call(
        "ingest_observation",
        observation("alpha", usage=usage("full", env.clock.now, window("weekly"))),
    )
    assert native.accepted
    assert (await env.project("alpha"))["refresh"]["last_failure"] is None
    env.clock.advance(minutes=6)
    [automatic] = await env.refresh("alpha")
    assert automatic.outcome is RefreshOutcome.ACCEPTED


async def test_abandoned_attempts_recover_without_stuck_running_state(env) -> None:
    [first] = await env.refresh("alpha", trigger=MANUAL)
    assert await env.call(
        "mark_running",
        profile_id="alpha",
        credential_generation=1,
        attempt_id=first.request_id,
    )
    assert (await env.project("alpha"))["refresh"]["state"] == "running"

    # The executor never acknowledges; nothing is left running forever.
    env.clock.advance(minutes=3)
    projection = await env.project("alpha")
    assert projection["refresh"]["state"] == "idle"
    assert projection["refresh"]["last_failure"] == "attempt_abandoned"

    [second] = await env.refresh("alpha")
    assert second.outcome is RefreshOutcome.ACCEPTED and not second.coalesced
    assert second.request_id != first.request_id
    late = await env.complete(first, window("weekly"))
    assert late.rejection is ObservationRejection.OWNER_EXPIRED
    assert not await env.call(
        "mark_running",
        profile_id="alpha",
        credential_generation=1,
        attempt_id=first.request_id,
    )
    assert (await env.complete(second, window("weekly"))).accepted
    assert len(env.dispatcher.requests) == 2


async def test_dispatch_failures_and_slow_dispatch_never_leave_queued_state(
    env,
) -> None:
    env.dispatcher.error = RuntimeError("executor down")
    [failed] = await env.refresh("alpha", trigger=MANUAL)
    assert failed.outcome is RefreshOutcome.BACKOFF
    projection = await env.project("alpha")
    assert projection["refresh"]["state"] == "backoff"
    assert projection["refresh"]["last_failure"] == "dispatch_failed"

    env.dispatcher.error = None
    env.dispatcher.delay = 30
    env.clock.advance(hours=1)
    started = time.monotonic()
    async with env.sessions() as session:
        [slow] = await env.service(
            session, dispatch_timeout_seconds=0.05
        ).request_refresh(["alpha"], trigger=MANUAL)
    assert time.monotonic() - started < 5
    assert slow.outcome is RefreshOutcome.BACKOFF


async def test_collection_request_is_compact_and_secret_free(env) -> None:
    await env.refresh("alpha", trigger=MANUAL)
    [request] = env.dispatcher.requests
    assert set(dataclasses.asdict(request)) == {
        "profile_id",
        "credential_generation",
        "provider_id",
        "runtime_id",
        "attempt_id",
        "trigger",
        "deadline_at",
    }
    assert request.deadline_at == T0 + timedelta(minutes=2)


async def test_native_observations_are_prompt_and_hints_coalesce(env) -> None:
    accepted = await env.call(
        "ingest_observation",
        observation(
            "alpha",
            usage=usage("partial", T0, window("five-hour", period="short", used=12)),
        ),
    )
    assert accepted.accepted and env.dispatcher.requests == []
    duplicate = await env.call(
        "ingest_observation",
        observation(
            "alpha",
            usage=usage("partial", T0, window("five-hour", period="short", used=12)),
        ),
    )
    assert duplicate.accepted and not duplicate.changed
    assert env.dispatcher.requests == []

    hints = [
        await env.refresh("alpha", trigger=RefreshTrigger.NATIVE_HINT) for _ in range(3)
    ]
    assert len(env.dispatcher.requests) == 1
    assert [h[0].coalesced for h in hints] == [False, True, True]
    await env.complete(hints[0][0], window("weekly"))
    [after] = await env.refresh("alpha", trigger=RefreshTrigger.NATIVE_HINT)
    assert after.outcome is RefreshOutcome.ALREADY_FRESH
    assert len(env.dispatcher.requests) == 1


async def test_retry_after_backoff_and_bounded_automatic_retries(env) -> None:
    [first] = await env.refresh("alpha")
    failure = await env.fail(first, retry_after="Wed, 18 Nov 2026 12:10:00 GMT")
    assert failure.next_allowed_at >= T0 + timedelta(minutes=10)
    [blocked] = await env.refresh("alpha", trigger=MANUAL)
    assert blocked.outcome is RefreshOutcome.BACKOFF
    assert blocked.next_allowed_at == failure.next_allowed_at

    env.clock.now = failure.next_allowed_at
    for _ in range(MAX_AUTOMATIC_FAILURES - 1):
        [attempt] = await env.refresh("alpha")
        assert attempt.outcome is RefreshOutcome.ACCEPTED
        failure = await env.fail(attempt, retry_after=1)
        assert failure.next_allowed_at - env.clock.now >= timedelta(minutes=1)
        env.clock.now = failure.next_allowed_at
    dispatched = len(env.dispatcher.requests)
    [stopped] = await env.refresh("alpha")
    assert stopped.outcome is RefreshOutcome.BACKOFF
    assert len(env.dispatcher.requests) == dispatched

    # Manual refresh still may try; success resets the failure budget.
    [manual] = await env.refresh("alpha", trigger=MANUAL)
    assert manual.outcome is RefreshOutcome.ACCEPTED
    await env.complete(manual, window("weekly"))
    env.clock.advance(minutes=6)
    [automatic] = await env.refresh("alpha")
    assert automatic.outcome is RefreshOutcome.ACCEPTED


async def test_monitoring_failures_never_mutate_execution_state(env) -> None:
    async with env.sessions() as session:
        session.add(
            ProviderCapacityScope(
                scope_ref="provider-profile:alpha",
                runtime_id="codex_cli",
                configured_limit=1,
                effective_limit=1,
            )
        )
        await session.commit()
    profile_before = await profile_snapshot(env.sessions, "alpha")
    async with env.sessions() as session:
        scope_before = dict(
            (await session.execute(select(ProviderCapacityScope.__table__)))
            .mappings()
            .one()
        )

    for code in AccountUsageFailureCode:
        if code in {
            AccountUsageFailureCode.DISPATCH_FAILED,
            AccountUsageFailureCode.ATTEMPT_ABANDONED,
        }:
            continue
        env.clock.advance(hours=2)
        [attempt] = await env.refresh("alpha", trigger=MANUAL)
        assert (await env.fail(attempt, code=code, retry_after="3600")).accepted

    assert await profile_snapshot(env.sessions, "alpha") == profile_before
    async with env.sessions() as session:
        scope_after = dict(
            (await session.execute(select(ProviderCapacityScope.__table__)))
            .mappings()
            .one()
        )
    assert scope_after == scope_before


async def test_busy_profiles_defer_without_a_waiter(env) -> None:
    env.dispatcher.result = DispatchResult.BUSY
    tasks_before = len(asyncio.all_tasks())
    [busy] = await env.refresh("alpha", trigger=MANUAL)
    assert busy.outcome is RefreshOutcome.DEFERRED_BUSY
    assert busy.retry_after_seconds == 60
    assert len(asyncio.all_tasks()) == tasks_before
    assert (await env.project("alpha"))["refresh"]["state"] == "deferred"

    env.clock.advance(seconds=30)
    [again] = await env.refresh("alpha")
    assert again.outcome is RefreshOutcome.DEFERRED_BUSY
    assert len(env.dispatcher.requests) == 1

    # The owning runtime serviced the request with a genuine observation.
    await env.call(
        "ingest_observation",
        observation("alpha", usage=usage("full", env.clock.now, window("weekly"))),
    )
    projection = await env.project("alpha")
    assert projection["refresh"]["state"] == "idle"
    assert projection["status"] == "fresh"


async def test_reset_boundary_expires_and_requests_once(env) -> None:
    # The reset falls inside the staleness window, so it alone makes a
    # collection due.
    reset_at = T0 + timedelta(minutes=2)
    [first] = await env.refresh("alpha")
    await env.complete(first, window("weekly", used=97, reset_at=reset_at))

    env.clock.now = reset_at + timedelta(seconds=1)
    projection = await env.project("alpha")
    weekly = projection["usage"]["windows"][0]
    assert weekly["freshness"] == "expired" and weekly["last_known"] is True
    assert weekly["used_percent"] == 97 and weekly["remaining_percent"] == 3
    assert weekly["reset_at"] == reset_at  # No fabricated replenishment or +7 days.

    [boundary] = await env.refresh("alpha")
    assert boundary.outcome is RefreshOutcome.ACCEPTED
    # The provider has not rolled over yet and re-reports the old reset.
    await env.complete(boundary, window("weekly", used=97, reset_at=reset_at))
    for _ in range(3):
        env.clock.advance(seconds=30)
        [again] = await env.refresh("alpha")
        assert again.outcome is RefreshOutcome.ALREADY_FRESH
    assert len(env.dispatcher.requests) == 2


async def test_reset_boundary_request_respects_backoff(env) -> None:
    reset_at = T0 + timedelta(minutes=1)
    [first] = await env.refresh("alpha")
    await env.complete(first, window("weekly", used=50, reset_at=reset_at))
    [manual] = await env.refresh("alpha", trigger=MANUAL)
    failure = await env.fail(manual, retry_after="600")
    env.clock.now = reset_at + timedelta(seconds=1)
    [blocked] = await env.refresh("alpha")
    assert blocked.outcome is RefreshOutcome.BACKOFF
    env.clock.now = failure.next_allowed_at
    [boundary] = await env.refresh("alpha")
    assert boundary.outcome is RefreshOutcome.ACCEPTED


async def test_no_polling_and_lifecycle_policy(env) -> None:
    await add_profiles(
        env.sessions,
        make_profile(
            "off",
            enabled=False,
            disabled_reason=ProviderProfileDisabledReason.USER_DISABLED,
        ),
        make_profile(
            "policy",
            enabled=False,
            disabled_reason=ProviderProfileDisabledReason.POLICY_DISABLED,
        ),
        make_profile(
            "gone",
            enabled=False,
            auth_state=ProviderProfileAuthState.DISCONNECTED,
            disabled_reason=ProviderProfileDisabledReason.DISCONNECTED,
        ),
        make_profile("other", provider_id="anthropic", runtime_id="claude_code"),
    )
    env.dispatcher.supported = {"openai"}
    for _ in range(5):
        env.clock.advance(minutes=10)
        for profile_id in ("alpha", "off", "gone"):
            await env.project(profile_id)
    assert env.dispatcher.requests == []

    outcomes = {
        r.profile_id: r.outcome
        for r in await env.refresh("off", "policy", "gone", "other")
    }
    assert outcomes == {
        "off": RefreshOutcome.DISABLED,
        "policy": RefreshOutcome.DISABLED,
        "gone": RefreshOutcome.NOT_CONNECTED,
        "other": RefreshOutcome.NOT_SUPPORTED,
    }
    manual = {
        r.profile_id: r.outcome
        for r in await env.refresh("off", "policy", "gone", trigger=MANUAL)
    }
    assert manual == {
        "off": RefreshOutcome.ACCEPTED,  # Still-connected credentials, operator asked.
        "policy": RefreshOutcome.DISABLED,
        "gone": RefreshOutcome.NOT_CONNECTED,
    }
    assert [r.profile_id for r in env.dispatcher.requests] == ["off"]


async def test_oauth_success_requests_initial_collection_after_release(
    env, monkeypatch
) -> None:
    from contextlib import asynccontextmanager

    from api_service.services import provider_account_usage
    from moonmind.workflows.temporal.activities import oauth_session_activities

    async with env.sessions() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id="oauth-1",
                runtime_id="codex_cli",
                profile_id="alpha",
                status=OAuthSessionStatus.REGISTERING_PROFILE,
            )
        )
        await session.commit()

    @asynccontextmanager
    async def session_context():
        async with env.sessions() as session:
            yield session

    monkeypatch.setattr(
        oauth_session_activities, "get_async_session_context", session_context
    )
    monkeypatch.setattr(provider_account_usage, "_dispatcher", env.dispatcher)

    await oauth_session_activities.oauth_session_update_status(
        {"session_id": "oauth-1", "status": "verifying"}
    )
    assert env.dispatcher.requests == []
    await oauth_session_activities.oauth_session_update_status(
        {"session_id": "oauth-1", "status": "succeeded"}
    )
    [request] = env.dispatcher.requests
    assert (request.profile_id, request.trigger) == ("alpha", "oauth")

    # A failing collection request never fails the OAuth status transition.
    env.dispatcher.error = RuntimeError("down")
    env.clock.advance(minutes=1)
    result = await oauth_session_activities.oauth_session_update_status(
        {"session_id": "oauth-1", "status": "succeeded"}
    )
    assert result["status"] == "succeeded"
