"""Codex usage collection through the shared authority (MoonLadderStudios/MoonMind#5364).

The real Codex adapter and the real cache run on SQLite. Only the probe
container is replaced: it returns the probe's allowlisted output for the
recorded, sanitized app-server fixtures.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
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
from moonmind.provider_profiles import codex_account_usage_probe as probe
from moonmind.provider_profiles.account_usage import ATTEMPT_DEADLINE, RefreshTrigger
from moonmind.provider_profiles.codex_account_usage import CodexUsageCollector
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
FIXTURES = Path(__file__).parents[2] / "fixtures" / "provider" / "codex_oauth"


def _recorded(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _bucket(weekly_used: float | None, *, reset: datetime | None = None) -> dict:
    return {
        "limitId": "codex",
        "primary": {"usedPercent": 3, "windowDurationMins": 300, "resetsAt": None},
        "secondary": {
            "usedPercent": weekly_used,
            "windowDurationMins": 10080,
            "resetsAt": int(reset.timestamp()) if reset else None,
        },
    }


def _observed(
    clock: Clock,
    *,
    limits: dict | None = None,
    account: dict | None = None,
    account_id: str | None = "acct-1111",
    identity: bool = True,
) -> dict:
    account = account or _recorded("account_read.json")["0.170.0"]
    limits = dict(limits or _recorded("rate_limits.json")["multi_bucket"])
    limits["accountId"] = account_id
    if account.get("workspaceRouting") and account_id:
        routing = {**account["workspaceRouting"], "chatgptAccountId": account_id}
        account = {**account, "workspaceRouting": routing}
    return {
        "outcome": "observed",
        "usage": probe.project(
            account if identity else None,
            limits,
            account if identity else None,
            observed_at=clock.now.timestamp(),
            codex_version="0.170.0",
        ),
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
            collectors=lambda: UsageCollectorRegistry((CodexUsageCollector(),)),
            lease_client=lambda: leases,
            probe_runner=lambda: container,
            resolve_launch=resolve_launch,
            runtime_bindings=lambda: bindings,
            host_leases=FakeHostLeases,
            clock=clock,
        )
    )
    await add_profiles(sessions, make_profile("alpha"), make_profile("beta"))

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
            "provider_id": "openai",
            "runtime_id": "codex_cli",
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


def _windows(projection) -> dict:
    return {window["key"]: window for window in projection["usage"]["windows"]}


async def test_idle_read_reports_email_weekly_allowance_reset_and_other_pools(
    env,
) -> None:
    env.container.outputs = [_observed(env.clock)]

    assert (await env.collect())["status"] == "observed"

    [run] = env.container.runs
    assert run["plan"].argv[3:] == ("read", "/home/app/.codex")
    assert run["plan"].writes_credentials is True
    assert run["exclusive_grant"] is True
    projection = await env.project()
    assert projection["status"] == "fresh"
    assert projection["binding_verified"] is True
    assert projection["account"]["email"] == "person@example.com"
    assert projection["account"]["display_name"] == "Example Workspace"
    assert projection["account"]["plan"] == "plus"
    windows = _windows(projection)
    assert projection["usage"]["primary_window_key"] == "codex.weekly"
    assert windows["codex.weekly"]["remaining_percent"] == 60.0
    assert windows["codex.weekly"]["reset_at"].isoformat() == (
        "2026-11-24T06:00:00+00:00"
    )
    assert windows["codex.5h"]["period"] == "short"
    assert windows["codex_bengalfox.weekly"]["period"] == "other"
    assert windows["codex_bengalfox.weekly"]["remaining_percent"] == 93.0


async def test_legacy_single_bucket_response_reports_the_weekly_allowance(env) -> None:
    legacy = _recorded("rate_limits.json")["legacy"]
    env.container.outputs = [
        _observed(env.clock, limits=legacy, account_id=None, identity=False)
    ]

    assert (await env.collect())["status"] == "observed"

    projection = await env.project()
    assert projection["usage"]["primary_window_key"] == "codex.weekly"
    assert _windows(projection)["codex.weekly"]["used_percent"] == 55.5
    # No identity was read and no account was reported: profile-scoped.
    assert projection["binding_verified"] is False
    assert projection["account"]["email"] is None


async def test_equal_email_in_two_workspaces_never_combines_quotas(env) -> None:
    env.container.outputs = [
        _observed(env.clock, limits={"rateLimits": _bucket(10)}, account_id="acct-a"),
        _observed(env.clock, limits={"rateLimits": _bucket(90)}, account_id="acct-b"),
    ]
    assert (await env.collect("alpha"))["status"] == "observed"
    assert (await env.collect("beta"))["status"] == "observed"

    alpha, beta = await env.project("alpha"), await env.project("beta")
    assert alpha["account"]["email"] == beta["account"]["email"]
    assert _windows(alpha)["codex.weekly"]["used_percent"] == 10.0
    assert _windows(beta)["codex.weekly"]["used_percent"] == 90.0
    alpha_row = await observation_row(env.sessions, "alpha")
    beta_row = await observation_row(env.sessions, "beta")
    assert alpha_row["account_key"] != beta_row["account_key"]

    # The home now answers for the other workspace: the result is rejected.
    env.clock.advance(minutes=2)
    env.container.outputs = [
        _observed(env.clock, limits={"rateLimits": _bucket(99)}, account_id="acct-b")
    ]
    assert await env.collect("alpha") == {
        "status": "obsolete",
        "rejection": "account_mismatch",
    }
    assert _windows(await env.project("alpha"))["codex.weekly"]["used_percent"] == 10


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
    "failure",
    [
        "provider_unavailable",
        "rate_limited",
        "timeout",
        "usage_access_missing",
        "malformed_response",
    ],
)
async def test_monitoring_failures_keep_last_good_values_and_the_profile(
    env, failure
) -> None:
    env.container.outputs = [_observed(env.clock)]
    await env.collect()
    first = await env.project()
    before = await profile_snapshot(env.sessions, "alpha")
    success_at = (await observation_row(env.sessions, "alpha"))["last_success_at"]

    env.clock.advance(minutes=6)
    env.container.outputs = [{"outcome": "failed", "failure": failure}]
    assert (await env.collect())["failure"] == failure

    projection = await env.project()
    assert projection["usage"]["windows"] == [
        {**window, "freshness": "stale", "last_known": True}
        for window in first["usage"]["windows"]
    ]
    assert projection["account"] == first["account"]
    assert projection["refresh"]["last_failure"] == failure
    # A failed read never moves the last success or touches the profile.
    row = await observation_row(env.sessions, "alpha")
    assert row["last_success_at"] == success_at
    assert await profile_snapshot(env.sessions, "alpha") == before


async def test_a_rejected_token_recovers_once_under_the_grant_then_reads(env) -> None:
    env.container.outputs = [{"outcome": "recovery_required"}, _observed(env.clock)]

    assert (await env.collect())["status"] == "observed"

    read, recover = env.container.runs
    assert recover["plan"].argv[3:] == ("recover", "/home/app/.codex")
    assert recover["plan"].writes_credentials and recover["exclusive_grant"]
    assert read["lease_id"] == recover["lease_id"]
    assert (await env.project())["status"] == "fresh"


async def test_unrecoverable_credentials_never_change_auth_or_scheduling(env) -> None:
    env.container.outputs = [
        {"outcome": "recovery_required"},
        {"outcome": "failed", "failure": "credential_refresh_unavailable"},
    ]
    before = await profile_snapshot(env.sessions, "alpha")

    result = await env.collect()

    assert result["failure"] == "credential_refresh_unavailable"
    # No second recovery in the same attempt: no rotation loop.
    assert len(env.container.runs) == 2
    after = await profile_snapshot(env.sessions, "alpha")
    assert after == before
    assert after["auth_state"] == ProviderProfileAuthState.CONNECTED


async def test_an_active_codex_owner_is_never_probed_by_collection(env) -> None:
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


async def test_reset_passage_expires_the_window_without_refilling_it(env) -> None:
    reset = env.clock.now + timedelta(hours=1)
    env.container.outputs = [
        _observed(env.clock, limits={"rateLimits": _bucket(97, reset=reset)})
    ]
    await env.collect()
    env.clock.advance(hours=2)

    weekly = _windows(await env.project())["codex.weekly"]
    assert weekly["freshness"] == "expired" and weekly["last_known"] is True
    assert (weekly["used_percent"], weekly["remaining_percent"]) == (97.0, 3.0)
    assert weekly["reset_at"] == reset.astimezone(UTC)
