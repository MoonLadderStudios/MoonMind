"""Claude usage collection through the shared authority (MoonLadderStudios/MoonMind#5365).

The real Claude adapter and the real cache run on SQLite. Only the probe
container is replaced: it returns the probe's allowlisted output for the
recorded, sanitized fixtures.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import update

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderProfileAuthState,
    ProviderProfileSlotLease,
)
from api_service.services.provider_account_usage import ProviderAccountUsageService
from moonmind.provider_profiles import claude_account_usage_probe as probe
from moonmind.provider_profiles.account_usage import ATTEMPT_DEADLINE, RefreshTrigger
from moonmind.provider_profiles.claude_account_usage import ClaudeUsageCollector
from moonmind.provider_profiles.usage_collection import UsageCollectorRegistry
from moonmind.workflows.temporal.activities import (
    provider_account_usage_activities as usage,
)
from tests.unit.services.account_usage_support import (
    Clock,
    FakeDispatcher,
    add_profiles,
    make_profile,
    observation_row,
    profile_snapshot,
    sqlite_sessions,
)
from tests.unit.workflows.temporal.activities.test_provider_account_usage_activities import (
    FakeBindings,
    FakeHostLeases,
    FakeLeaseClient,
)

pytestmark = pytest.mark.asyncio
FIXTURES = Path(__file__).parents[2] / "fixtures" / "provider" / "claude_oauth"


def _observed(clock: Clock, *, usage: dict | None = None, profile: Any = "fixture"):
    body = usage or json.loads((FIXTURES / "usage.json").read_text())
    if profile == "fixture":
        profile = json.loads((FIXTURES / "profile.json").read_text())
    return {
        "outcome": "observed",
        "observedAt": clock.now.timestamp(),
        "usage": probe.project_usage(body),
        "profile": probe.project_profile(profile) if profile else None,
    }


def _profile_body(account: str, workspace: str, email: str) -> dict:
    return {
        "account": {"uuid": account, "email": email},
        "organization": {"uuid": workspace, "name": f"{workspace} name"},
    }


class ProbeContainer:
    """Stands in for the probe container; returns scripted probe output."""

    def __init__(self) -> None:
        self.outputs: list[dict] = []
        self.runs: list[dict[str, Any]] = []

    async def run(self, **kwargs):
        self.runs.append(kwargs)
        return 0, json.dumps({"v": 1, **self.outputs.pop(0)}).encode()

    async def cleanup(self, lease_id: str):
        return {"containerName": lease_id, "removed": True}


def _claude_profile(profile_id: str, **overrides):
    values = {
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "volume_ref": f"{profile_id}-claude-home",
        "volume_mount_path": "/home/app/.claude",
    }
    values.update(overrides)
    return make_profile(profile_id, **values)


@pytest_asyncio.fixture
async def env(tmp_path):
    engine, sessions = await sqlite_sessions(tmp_path)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[ProviderProfileSlotLease.__table__]
            )
        )
    clock = Clock()
    container, leases, bindings = ProbeContainer(), FakeLeaseClient(), FakeBindings()

    async def resolve_launch(profile_id):
        return {"hostImageRef": "image@sha256:" + "a" * 64}

    usage.use_environment(
        usage.UsageCollectionEnvironment(
            session_factory=sessions,
            collectors=lambda: UsageCollectorRegistry((ClaudeUsageCollector(),)),
            lease_client=lambda: leases,
            probe_runner=lambda: container,
            resolve_launch=resolve_launch,
            runtime_bindings=lambda: bindings,
            host_leases=FakeHostLeases,
            clock=clock,
        )
    )
    await add_profiles(sessions, _claude_profile("alpha"), _claude_profile("beta"))

    def service(session):
        return ProviderAccountUsageService(
            session, dispatcher=FakeDispatcher(), clock=clock
        )

    async def collect(profile_id="alpha", *, trigger=RefreshTrigger.MANUAL):
        async with sessions() as session:
            [claim] = await service(session).request_refresh(
                [profile_id], trigger=trigger
            )
        assert claim.outcome.value == "accepted", claim
        request = {
            "profile_id": profile_id,
            "credential_generation": claim.credential_generation,
            "provider_id": "anthropic",
            "runtime_id": "claude_code",
            "attempt_id": claim.request_id,
            "trigger": trigger.value,
            "deadline_at": (clock.now + ATTEMPT_DEADLINE).isoformat(),
        }
        resolved = await usage.resolve_collection(request)
        if resolved["route"] == "active_owner":
            return await usage.collect_from_owner({**request, **resolved})
        grant = await usage.try_acquire_grant(
            {
                **request,
                "owner_id": f"provider-account-usage:{profile_id}:{claim.request_id}",
                "credentialResourceKey": resolved["credentialResourceKey"],
                "aliasProfileIds": resolved["aliasProfileIds"],
            }
        )
        return await usage.run_idle_probe({**request, **grant})

    async def project(profile_id="alpha"):
        async with sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            projections = await service(session).load_projections([profile])
        return projections[profile_id]

    yield dataclasses.make_dataclass(
        "Env", ["sessions", "clock", "container", "bindings", "collect", "project"]
    )(sessions, clock, container, bindings, collect, project)
    usage.use_environment(None)
    await engine.dispose()


async def test_idle_read_reports_email_weekly_allowance_reset_and_scoped_windows(
    env,
) -> None:
    env.container.outputs = [_observed(env.clock)]

    assert (await env.collect())["status"] == "observed"

    [run] = env.container.runs
    assert run["plan"].writes_credentials is False
    assert run["plan"].argv[3:] == ("read", "/home/app/.claude")
    projection = await env.project()
    assert projection["status"] == "fresh"
    assert projection["binding_verified"] is True
    assert projection["account"]["email"] == "person@example.com"
    assert projection["account"]["display_name"] == "Example Workspace"
    windows = {window["key"]: window for window in projection["usage"]["windows"]}
    assert projection["usage"]["primary_window_key"] == "seven_day"
    assert windows["seven_day"]["remaining_percent"] == 58.0
    assert windows["seven_day"]["reset_at"].isoformat() == "2026-11-20T09:00:00+00:00"
    assert (windows["five_hour"]["period"], windows["seven_day_opus"]["scope"]) == (
        "short",
        "model",
    )


async def test_profiles_on_different_accounts_never_exchange_identity_or_usage(
    env,
) -> None:
    env.container.outputs = [
        _observed(
            env.clock,
            usage={"seven_day": {"utilization": 10, "resets_at": None}},
            profile=_profile_body("acct-a", "ws-a", "a@example.com"),
        ),
        _observed(
            env.clock,
            usage={"seven_day": {"utilization": 90, "resets_at": None}},
            profile=_profile_body("acct-b", "ws-b", "b@example.com"),
        ),
    ]
    assert (await env.collect("alpha"))["status"] == "observed"
    assert (await env.collect("beta"))["status"] == "observed"

    alpha, beta = await env.project("alpha"), await env.project("beta")
    assert (alpha["account"]["email"], beta["account"]["email"]) == (
        "a@example.com",
        "b@example.com",
    )
    assert alpha["usage"]["windows"][0]["used_percent"] == 10.0
    assert beta["usage"]["windows"][0]["used_percent"] == 90.0

    # The home now answers for another account: the late result is rejected.
    env.clock.advance(minutes=2)
    env.container.outputs = [
        _observed(
            env.clock,
            usage={"seven_day": {"utilization": 99, "resets_at": None}},
            profile=_profile_body("acct-b", "ws-b", "b@example.com"),
        )
    ]
    result = await env.collect("alpha")
    assert result == {"status": "obsolete", "rejection": "account_mismatch"}
    alpha_after = await env.project("alpha")
    assert alpha_after["account"]["email"] == "a@example.com"
    assert alpha_after["usage"]["windows"][0]["used_percent"] == 10.0


async def test_a_new_credential_generation_rejects_the_old_probe_result(env) -> None:
    async def reconnect_during_probe(**kwargs):
        async with env.sessions() as session:
            await session.execute(
                update(ManagedAgentProviderProfile)
                .where(ManagedAgentProviderProfile.profile_id == "alpha")
                .values(credential_generation=2)
            )
            await session.commit()
        return 0, json.dumps({"v": 1, **_observed(env.clock)}).encode()

    env.container.run = reconnect_during_probe

    assert (await env.collect())["status"] == "obsolete"
    row = await observation_row(env.sessions, "alpha")
    assert not (row or {}).get("usage_json")


@pytest.mark.parametrize(
    ("output", "failure"),
    [
        (
            {"outcome": "failed", "failure": "usage_access_missing"},
            "usage_access_missing",
        ),
        (
            {"outcome": "failed", "failure": "rate_limited", "retryAfter": "0"},
            "rate_limited",
        ),
        ({"outcome": "failed", "failure": "timeout"}, "timeout"),
        (
            {"outcome": "failed", "failure": "provider_unavailable"},
            "provider_unavailable",
        ),
        (
            {
                "outcome": "observed",
                "observedAt": 0,
                "usage": {"windows": {"renamed": {"utilization": 1}}},
            },
            "malformed_response",
        ),
    ],
)
async def test_monitoring_failures_keep_last_good_values_and_the_profile(
    env, output, failure
) -> None:
    env.container.outputs = [_observed(env.clock)]
    await env.collect()
    first = await env.project()
    before = await profile_snapshot(env.sessions, "alpha")

    env.clock.advance(minutes=6)
    env.container.outputs = [output]
    assert (await env.collect())["failure"] == failure

    projection = await env.project()
    # Last-good values keep their original observation times.
    assert projection["usage"]["windows"] == [
        {**window, "freshness": "stale", "last_known": True}
        for window in first["usage"]["windows"]
    ]
    assert projection["account"] == first["account"]
    assert projection["refresh"]["last_failure"] == failure
    # A monitoring failure never disables or reschedules the profile.
    assert await profile_snapshot(env.sessions, "alpha") == before
    row = await observation_row(env.sessions, "alpha")
    if failure == "rate_limited":
        # Retry-After: 0 still backs off for at least the minimum.
        next_allowed = row["next_allowed_at"].replace(tzinfo=UTC)
        assert next_allowed - env.clock.now >= timedelta(minutes=1)
    expected_status = {
        "usage_access_missing": "usage_access_missing",
    }.get(failure, "unavailable")
    assert projection["status"] == expected_status


async def test_expired_token_is_renewed_by_the_cli_under_the_grant_then_read(
    env,
) -> None:
    env.container.outputs = [
        {"outcome": "recovery_required"},
        {**_observed(env.clock), "recovery": "renewed"},
    ]

    assert (await env.collect())["status"] == "observed"

    read, recover = env.container.runs
    assert read["plan"].writes_credentials is False
    assert recover["plan"].writes_credentials is True
    assert recover["plan"].argv[3:] == ("recover", "/home/app/.claude")
    assert recover["exclusive_grant"] is True
    assert (await env.project())["status"] == "fresh"


async def test_unrecoverable_credentials_report_reconnect_without_changing_auth(
    env,
) -> None:
    env.container.outputs = [
        {"outcome": "recovery_required"},
        {"outcome": "failed", "failure": "reconnect_required", "recovery": "rejected"},
    ]
    before = await profile_snapshot(env.sessions, "alpha")

    assert (await env.collect())["failure"] == "reconnect_required"

    assert (await env.project())["status"] == "reconnect_required"
    after = await profile_snapshot(env.sessions, "alpha")
    assert after == before
    assert after["auth_state"] == ProviderProfileAuthState.CONNECTED


async def test_unsupported_recovery_reports_upgrade_or_reconnect_guidance(env) -> None:
    env.container.outputs = [
        {"outcome": "recovery_required"},
        {
            "outcome": "failed",
            "failure": "credential_recovery_unsupported",
            "recovery": "unsupported",
        },
    ]
    assert (await env.collect())["failure"] == "credential_recovery_unsupported"
    projection = await env.project()
    assert projection["refresh"]["last_failure"] == "credential_recovery_unsupported"


async def test_an_active_claude_owner_is_never_probed_or_renewed_by_collection(
    env,
) -> None:
    env.bindings.bindings = [
        SimpleNamespace(
            bindingId="binding-1",
            hostLeaseRef="host-lease-1",
            providerLeases={"primary-model": {"providerProfileRef": "alpha"}},
            phaseResults={"owner": {"workflowId": "run-1"}},
        )
    ]

    assert (await env.collect())["status"] == "deferred"
    assert env.container.runs == []


async def test_reset_rollover_stays_expired_until_fresh_provider_evidence(
    env,
) -> None:
    reset = env.clock.now + timedelta(hours=1)
    env.container.outputs = [
        _observed(
            env.clock,
            usage={"seven_day": {"utilization": 97, "resets_at": reset.isoformat()}},
        )
    ]
    await env.collect()
    env.clock.advance(hours=2)

    projection = await env.project()
    [weekly] = projection["usage"]["windows"]
    assert weekly["freshness"] == "expired" and weekly["last_known"] is True
    # The old reading is kept as last known: no 100% refill, no new deadline.
    assert (weekly["used_percent"], weekly["remaining_percent"]) == (97.0, 3.0)
    assert weekly["reset_at"] == reset
    assert projection["status"] == "stale"

    # One collection is requested for the passed reset; until its evidence
    # arrives the window stays expired.
    async with env.sessions() as session:
        service = ProviderAccountUsageService(
            session, dispatcher=FakeDispatcher(), clock=env.clock
        )
        [first] = await service.request_refresh(
            ["alpha"], trigger=RefreshTrigger.VISIBILITY
        )
    assert first.outcome.value == "accepted"
    assert (await env.project())["usage"]["windows"][0]["freshness"] == "expired"
