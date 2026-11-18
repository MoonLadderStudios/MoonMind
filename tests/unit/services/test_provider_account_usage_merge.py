"""Merge, fencing, sharing and state semantics of account usage (#5362)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
import pytest_asyncio

from api_service.db.models import ProviderProfileAuthState
from api_service.services.provider_account_usage import ProviderAccountUsageService
from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    ObservationRejection,
    RefreshTrigger,
    UsageInput,
    merge_usage,
)
from tests.unit.services.account_usage_support import (
    T0,
    Clock,
    FakeDispatcher,
    add_profiles,
    make_profile,
    observation,
    observation_row,
    profile_snapshot,
    sqlite_sessions,
    usage,
    window,
)


@pytest_asyncio.fixture
async def env(tmp_path):
    engine, sessions = await sqlite_sessions(tmp_path)
    clock = Clock()
    dispatcher = FakeDispatcher()

    async def service_call(method, *args, **kwargs):
        async with sessions() as session:
            service = ProviderAccountUsageService(
                session, dispatcher=dispatcher, clock=clock
            )
            return await getattr(service, method)(*args, **kwargs)

    async def project(profile_id):
        from api_service.db.models import ManagedAgentProviderProfile

        async with sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            service = ProviderAccountUsageService(
                session, dispatcher=dispatcher, clock=clock
            )
            return (await service.load_projections([profile]))[profile_id]

    await add_profiles(sessions, make_profile("alpha"))
    yield {
        "sessions": sessions,
        "clock": clock,
        "dispatcher": dispatcher,
        "call": service_call,
        "project": project,
    }
    await engine.dispose()


def _merge(stored, completeness, observed_at, *windows, **extra):
    return merge_usage(
        stored, UsageInput(**usage(completeness, observed_at, *windows, **extra))
    )


# ---------------------------------------------------------------------------
# Pure merge semantics
# ---------------------------------------------------------------------------


def test_full_snapshot_removes_omitted_windows_and_partial_never_erases() -> None:
    first = _merge(
        None,
        "full",
        T0,
        window("weekly", used=10),
        window("five-hour", period="short", used=40),
    )
    assert set(first.usage["windows"]) == {"weekly", "five-hour"}
    assert first.usage["primary_window_key"] == "weekly"

    partial = _merge(
        first.usage,
        "partial",
        T0 + timedelta(minutes=1),
        window("five-hour", period="short", used=55),
    )
    assert partial.changed
    assert partial.usage["windows"]["weekly"] == first.usage["windows"]["weekly"]
    assert partial.usage["windows"]["five-hour"]["used_percent"] == 55
    assert partial.usage["full_observed_at"] == first.usage["full_observed_at"]

    full = _merge(
        partial.usage, "full", T0 + timedelta(minutes=2), window("weekly", used=12)
    )
    assert set(full.usage["windows"]) == {"weekly"}


def test_full_snapshot_keeps_a_window_updated_after_it_was_taken() -> None:
    base = _merge(
        None, "full", T0, window("weekly"), window("five-hour", period="short")
    )
    newer_partial = _merge(
        base.usage,
        "partial",
        T0 + timedelta(minutes=5),
        window("five-hour", period="short", used=70),
    )
    # A full snapshot taken before that partial cannot erase its newer reading.
    snapshot = _merge(
        newer_partial.usage,
        "full",
        T0 + timedelta(minutes=3),
        window("weekly", used=11),
    )
    assert snapshot.usage["windows"]["five-hour"]["used_percent"] == 70
    assert snapshot.usage["windows"]["weekly"]["used_percent"] == 11


def test_older_partial_cannot_restore_a_window_a_full_snapshot_removed() -> None:
    base = _merge(
        None, "full", T0, window("weekly"), window("five-hour", period="short")
    )
    removed = _merge(
        base.usage, "full", T0 + timedelta(minutes=2), window("weekly", used=12)
    )
    assert set(removed.usage["windows"]) == {"weekly"}

    delayed = _merge(
        removed.usage,
        "partial",
        T0 + timedelta(minutes=1),
        window("five-hour", period="short", used=40),
    )
    assert not delayed.changed
    assert set(delayed.usage["windows"]) == {"weekly"}

    later = _merge(
        removed.usage,
        "partial",
        T0 + timedelta(minutes=3),
        window("five-hour", period="short", used=45),
    )
    assert later.changed
    assert later.usage["windows"]["five-hour"]["used_percent"] == 45


def test_out_of_order_and_duplicate_observations_do_not_replace_newer_values() -> None:
    newest = _merge(None, "full", T0 + timedelta(minutes=10), window("weekly", used=30))
    older_full = _merge(newest.usage, "full", T0, window("weekly", used=5))
    assert older_full.rejection is ObservationRejection.OUT_OF_ORDER

    older_partial = _merge(newest.usage, "partial", T0, window("weekly", used=5))
    assert not older_partial.changed
    assert older_partial.usage["windows"]["weekly"]["used_percent"] == 30

    duplicate = _merge(
        newest.usage, "full", T0 + timedelta(minutes=10), window("weekly", used=30)
    )
    assert duplicate.rejection is None and not duplicate.changed


def test_scoped_and_short_windows_stay_separate_from_the_general_weekly() -> None:
    merged = _merge(
        None,
        "full",
        T0,
        window("weekly", used=20),
        window("weekly-opus", used=90, scope="model", model_label="Opus"),
        window("five-hour", period="short", used=60),
    )
    windows = merged.usage["windows"]
    assert {key: w["used_percent"] for key, w in windows.items()} == {
        "weekly": 20,
        "weekly-opus": 90,
        "five-hour": 60,
    }
    assert merged.usage["primary_window_key"] == "weekly"

    only_scoped = _merge(
        None,
        "full",
        T0,
        window("weekly-opus", used=90, scope="model", model_label="Opus"),
        window("five-hour", period="short", used=60),
    )
    assert only_scoped.usage["primary_window_key"] is None

    ambiguous = _merge(None, "full", T0, window("weekly-a"), window("weekly-b"))
    assert ambiguous.usage["primary_window_key"] is None


# ---------------------------------------------------------------------------
# Service ingestion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projection_carries_binding_account_usage_and_windows(env) -> None:
    result = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            account_id="acct-1",
            workspace_id="ws-1",
            identity={
                "email": "person@example.com",
                "display_name": "Person",
                "plan": "pro",
                "account_id": "acct-1",
                "workspace_id": "ws-1",
                "verified": True,
                "source": "fake_identity",
                "observed_at": T0,
            },
            usage=usage(
                "full",
                T0,
                window("weekly", used=1, reset_at=T0 + timedelta(days=3)),
                window("five-hour", period="short", used=None),
            ),
        ),
    )
    assert result.accepted and result.changed and result.snapshot_revision == 1

    projection = await env["project"]("alpha")
    assert projection["schema_version"] == 1
    assert projection["credential_generation"] == 1
    assert projection["snapshot_revision"] == 1
    assert projection["provider_id"] == "openai"
    assert projection["runtime_id"] == "codex_cli"
    assert projection["binding_verified"] is True
    assert projection["status"] == "fresh"
    assert projection["account"] == {
        "status": "verified",
        "email": "person@example.com",
        "display_name": "Person",
        "plan": "pro",
        "source": "fake_identity",
        "observed_at": T0,
    }
    assert projection["usage"]["completeness"] == "full"
    assert projection["usage"]["primary_window_key"] == "weekly"
    weekly, short = projection["usage"]["windows"]
    assert weekly["key"] == "weekly"
    assert weekly["used_percent"] == 1
    assert weekly["remaining_percent"] == 99
    assert weekly["exhausted"] is False
    assert weekly["reset_at"] == T0 + timedelta(days=3)
    assert weekly["fresh_until"] == T0 + timedelta(minutes=5)
    assert weekly["freshness"] == "fresh" and weekly["last_known"] is False
    assert short["used_percent"] is None and short["remaining_percent"] is None
    assert short["exhausted"] is None
    # Stable provider IDs stay internal.
    rendered = json.dumps(projection, default=str)
    assert "acct-1" not in rendered and "ws-1" not in rendered


@pytest.mark.asyncio
async def test_identity_windows_and_refresh_update_independently(env) -> None:
    await env["call"](
        "ingest_observation",
        observation("alpha", usage=usage("full", T0, window("weekly", used=10))),
    )
    env["clock"].advance(minutes=30)
    # An identity success does not refresh the older quota measurement.
    await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            identity={
                "email": "person@example.com",
                "source": "fake_identity",
                "observed_at": env["clock"].now,
            },
        ),
    )
    projection = await env["project"]("alpha")
    assert projection["account"]["observed_at"] == env["clock"].now
    assert projection["account"]["status"] == "reported"
    weekly = projection["usage"]["windows"][0]
    assert weekly["observed_at"] == T0
    assert weekly["freshness"] == "stale" and weekly["last_known"] is True
    assert projection["status"] == "stale"

    # Re-delivered cached telemetry keeps its original observation time.
    redelivered = await env["call"](
        "ingest_observation",
        observation("alpha", usage=usage("full", T0, window("weekly", used=10))),
    )
    assert redelivered.accepted and not redelivered.changed
    projection = await env["project"]("alpha")
    assert projection["usage"]["windows"][0]["observed_at"] == T0
    assert projection["snapshot_revision"] == 2


@pytest.mark.asyncio
async def test_fencing_rejects_stale_generation_account_owner_and_order(env) -> None:
    sessions = env["sessions"]
    accepted = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            account_id="acct-1",
            usage=usage("full", T0 + timedelta(minutes=1), window("weekly", used=30)),
        ),
    )
    assert accepted.accepted
    before = await observation_row(sessions, "alpha")

    rejections = [
        (
            observation(
                "alpha", generation=2, usage=usage("full", T0, window("weekly"))
            ),
            ObservationRejection.STALE_GENERATION,
        ),
        (
            observation(
                "alpha",
                account_id="acct-2",
                usage=usage("full", T0 + timedelta(minutes=2), window("weekly")),
            ),
            ObservationRejection.ACCOUNT_MISMATCH,
        ),
        (
            # An identity read for another account races the quota read.
            observation(
                "alpha",
                identity={
                    "email": "other@example.com",
                    "account_id": "acct-2",
                    "verified": True,
                    "source": "fake_identity",
                    "observed_at": T0 + timedelta(minutes=3),
                },
            ),
            ObservationRejection.ACCOUNT_MISMATCH,
        ),
        (
            observation(
                "alpha",
                owner_expires_at=T0 - timedelta(seconds=1),
                usage=usage("full", T0 + timedelta(minutes=4), window("weekly")),
            ),
            ObservationRejection.OWNER_EXPIRED,
        ),
        (
            observation(
                "alpha",
                attempt_id="never-claimed",
                usage=usage("full", T0 + timedelta(minutes=4), window("weekly")),
            ),
            ObservationRejection.OWNER_EXPIRED,
        ),
        (
            observation("alpha", usage=usage("full", T0, window("weekly", used=5))),
            ObservationRejection.OUT_OF_ORDER,
        ),
    ]
    for candidate, rejection in rejections:
        result = await env["call"]("ingest_observation", candidate)
        assert (result.accepted, result.rejection) == (False, rejection)
    # Nothing was persisted by any rejected publication.
    assert await observation_row(sessions, "alpha") == before


@pytest.mark.asyncio
async def test_attempt_results_expire_with_their_owner_deadline(env) -> None:
    [claimed] = await env["call"](
        "request_refresh", ["alpha"], trigger=RefreshTrigger.MANUAL
    )
    env["clock"].advance(minutes=3)  # Past ATTEMPT_DEADLINE: the owner lost authority.
    late = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            attempt_id=claimed.request_id,
            usage=usage("full", env["clock"].now, window("weekly")),
        ),
    )
    assert late.rejection is ObservationRejection.OWNER_EXPIRED

    env["clock"].advance(minutes=1)
    [reclaimed] = await env["call"](
        "request_refresh", ["alpha"], trigger=RefreshTrigger.MANUAL
    )
    assert reclaimed.request_id != claimed.request_id
    ok = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            attempt_id=reclaimed.request_id,
            usage=usage("full", env["clock"].now, window("weekly")),
        ),
    )
    assert ok.accepted
    projection = await env["project"]("alpha")
    assert projection["refresh"]["state"] == "idle"
    assert projection["refresh"]["last_success_at"] == env["clock"].now


@pytest.mark.asyncio
async def test_account_switch_rebinding_invalidates_and_fences_old_reads(env) -> None:
    from api_service.db.models import ManagedAgentProviderProfile

    sessions = env["sessions"]
    await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            account_id="acct-1",
            identity={"email": "a@example.com", "source": "fake", "observed_at": T0},
            usage=usage("full", T0, window("weekly", used=40)),
        ),
    )
    async with sessions() as session:
        profile = await session.get(ManagedAgentProviderProfile, "alpha")
        profile.credential_generation = 2
        await session.commit()
    before = await profile_snapshot(sessions, "alpha")

    projection = await env["project"]("alpha")
    assert projection["credential_generation"] == 2
    assert projection["status"] == "unknown"
    assert projection["account"]["email"] is None
    assert projection["usage"]["windows"] == []

    # A quota read started under the old binding cannot land afterwards.
    stale = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            account_id="acct-1",
            usage=usage("full", T0 + timedelta(minutes=1), window("weekly")),
        ),
    )
    assert stale.rejection is ObservationRejection.STALE_GENERATION

    # The new binding may report a different account.
    fresh = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            generation=2,
            account_id="acct-2",
            usage=usage("full", T0 + timedelta(minutes=1), window("weekly", used=3)),
        ),
    )
    assert fresh.accepted and fresh.snapshot_revision == 1
    row = await observation_row(sessions, "alpha")
    assert row["credential_generation"] == 2
    assert row["bound_account_id"] == "acct-2"
    assert row["identity_json"] is None
    assert await profile_snapshot(sessions, "alpha") == before


@pytest.mark.asyncio
async def test_observation_writes_never_touch_profile_configuration(env) -> None:
    sessions = env["sessions"]
    before = await profile_snapshot(sessions, "alpha")
    await env["call"](
        "ingest_observation",
        observation("alpha", usage=usage("full", T0, window("weekly", used=100))),
    )
    [claimed] = await env["call"](
        "request_refresh", ["alpha"], trigger=RefreshTrigger.MANUAL
    )
    await env["call"](
        "record_collection_failure",
        profile_id="alpha",
        credential_generation=1,
        attempt_id=claimed.request_id,
        code=AccountUsageFailureCode.RATE_LIMITED,
        retry_after="600",
    )
    assert await profile_snapshot(sessions, "alpha") == before


@pytest.mark.asyncio
async def test_sharing_requires_verified_provider_account_identity(env) -> None:
    sessions = env["sessions"]
    await add_profiles(
        sessions,
        make_profile("beta"),
        make_profile("gamma"),
        make_profile("delta"),
    )
    same_email = {"email": "person@example.com", "source": "fake", "observed_at": T0}
    for profile_id in ("alpha", "beta"):
        await env["call"](
            "ingest_observation", observation(profile_id, identity=same_email)
        )
    await env["call"](
        "ingest_observation",
        observation("alpha", usage=usage("full", T0, window("weekly", used=50))),
    )
    # Equal email alone never shares cached usage.
    assert (await env["project"]("beta"))["usage"]["windows"] == []

    for profile_id, workspace in (("gamma", "ws-1"), ("delta", "ws-2")):
        await env["call"](
            "ingest_observation",
            observation(
                profile_id,
                account_id="acct-9",
                workspace_id=workspace,
                identity=same_email,
            ),
        )
    await env["call"](
        "ingest_observation",
        observation("alpha", generation=1, account_id="acct-9", workspace_id="ws-1"),
    )
    shared = await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            usage=usage("full", T0 + timedelta(minutes=1), window("weekly", used=60)),
        ),
    )
    assert shared.shared_with == ("gamma",)
    gamma = await env["project"]("gamma")
    assert gamma["usage"]["windows"][0]["used_percent"] == 60
    assert (await env["project"]("delta"))["usage"]["windows"] == []


@pytest.mark.asyncio
async def test_workspace_learned_after_account_binding_splits_sharing(env) -> None:
    sessions = env["sessions"]
    await add_profiles(sessions, make_profile("beta"))
    for profile_id in ("alpha", "beta"):
        await env["call"](
            "ingest_observation", observation(profile_id, account_id="acct-1")
        )
    for profile_id, workspace in (("alpha", "ws-1"), ("beta", "ws-2")):
        learned = await env["call"](
            "ingest_observation",
            observation(profile_id, account_id="acct-1", workspace_id=workspace),
        )
        assert learned.accepted
    alpha_row = await observation_row(sessions, "alpha")
    beta_row = await observation_row(sessions, "beta")
    assert alpha_row["bound_workspace_id"] == "ws-1"
    assert beta_row["bound_workspace_id"] == "ws-2"
    assert alpha_row["account_key"] != beta_row["account_key"]

    shared = await env["call"](
        "ingest_observation",
        observation("alpha", usage=usage("full", T0, window("weekly", used=60))),
    )
    assert shared.accepted and shared.shared_with == ()
    assert (await env["project"]("beta"))["usage"]["windows"] == []

    mismatch = await env["call"](
        "ingest_observation",
        observation("alpha", account_id="acct-1", workspace_id="ws-2"),
    )
    assert mismatch.rejection is ObservationRejection.ACCOUNT_MISMATCH


@pytest.mark.asyncio
async def test_safe_states_retain_last_known_values(env) -> None:
    from api_service.db.models import ManagedAgentProviderProfile

    sessions = env["sessions"]
    dispatcher = env["dispatcher"]
    assert (await env["project"]("alpha"))["status"] == "unknown"
    dispatcher.supported = set()
    assert (await env["project"]("alpha"))["status"] == "not_supported"
    dispatcher.supported = None

    await env["call"](
        "ingest_observation", observation("alpha", usage=usage("full", T0))
    )
    assert (await env["project"]("alpha"))["status"] == "not_reported"

    await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            usage=usage("full", T0 + timedelta(seconds=1), window("weekly", used=25)),
        ),
    )
    assert (await env["project"]("alpha"))["status"] == "fresh"

    env["clock"].advance(minutes=10)
    [claimed] = await env["call"](
        "request_refresh", ["alpha"], trigger=RefreshTrigger.MANUAL
    )
    await env["call"](
        "record_collection_failure",
        profile_id="alpha",
        credential_generation=1,
        attempt_id=claimed.request_id,
        code=AccountUsageFailureCode.PROVIDER_UNAVAILABLE,
    )
    unavailable = await env["project"]("alpha")
    assert unavailable["status"] == "unavailable"
    assert unavailable["refresh"]["state"] == "backoff"
    assert unavailable["refresh"]["last_failure"] == "provider_unavailable"
    kept = unavailable["usage"]["windows"][0]
    assert kept["used_percent"] == 25
    assert kept["observed_at"] == T0 + timedelta(seconds=1)
    assert kept["last_known"] is True

    for code, status in (
        (AccountUsageFailureCode.USAGE_ACCESS_MISSING, "usage_access_missing"),
        (AccountUsageFailureCode.RECONNECT_REQUIRED, "reconnect_required"),
    ):
        env["clock"].advance(hours=3)
        [claimed] = await env["call"](
            "request_refresh", ["alpha"], trigger=RefreshTrigger.MANUAL
        )
        await env["call"](
            "record_collection_failure",
            profile_id="alpha",
            credential_generation=1,
            attempt_id=claimed.request_id,
            code=code,
        )
        assert (await env["project"]("alpha"))["status"] == status

    async with sessions() as session:
        profile = await session.get(ManagedAgentProviderProfile, "alpha")
        profile.auth_state = ProviderProfileAuthState.DISCONNECTED
        await session.commit()
    assert (await env["project"]("alpha"))["status"] == "reconnect_required"


@pytest.mark.asyncio
async def test_stored_snapshot_contains_only_allowlisted_fields(env) -> None:
    await env["call"](
        "ingest_observation",
        observation(
            "alpha",
            account_id="acct-1",
            identity={
                "email": "person@example.com",
                "source": "fake",
                "observed_at": T0,
            },
            usage=usage("full", T0, window("weekly", used=10)),
        ),
    )
    row = await observation_row(env["sessions"], "alpha")
    assert set(row["identity_json"]) == {
        "status",
        "email",
        "display_name",
        "plan",
        "account_id",
        "workspace_id",
        "verified",
        "source",
        "observed_at",
    }
    assert set(row["usage_json"]) == {
        "completeness",
        "full_observed_at",
        "primary_window_key",
        "reported",
        "windows",
    }
    assert set(row["usage_json"]["windows"]["weekly"]) == {
        "key",
        "period",
        "scope",
        "model_label",
        "duration_seconds",
        "used_percent",
        "reset_at",
        "source",
        "source_version",
        "observed_at",
    }
