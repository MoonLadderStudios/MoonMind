"""Collection authority for provider account usage (MoonLadderStudios/MoonMind#5363).

The activities run against a real SQLite cache with fake provider adapters,
lease client, probe runner, and consumer stores.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderProfileAuthState,
    ProviderProfileSlotLease,
)
from api_service.services.provider_account_usage import ProviderAccountUsageService
from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    RefreshTrigger,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeaseMode,
    CredentialLeasePurpose,
)
from moonmind.provider_profiles.usage_collection import (
    OwnerUsageReport,
    ProbeResult,
    UsageCollectorRegistry,
    UsageProbePlan,
    credential_resource_key,
)
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

pytestmark = pytest.mark.asyncio
TOKEN = "sk-live-ABCDEFGHIJKLMNOP"


def _report(used: float = 42.0, *, clock: Clock) -> OwnerUsageReport:
    return OwnerUsageReport(
        account_id="acct-1",
        identity={
            "email": "person@example.com",
            "account_id": "acct-1",
            "verified": True,
            "source": "fake_collector",
            "observed_at": clock.now,
        },
        usage={
            "completeness": "full",
            "observed_at": clock.now,
            "windows": [
                {
                    "key": "weekly",
                    "period": "weekly",
                    "used_percent": used,
                    "source": "fake_collector",
                }
            ],
        },
    )


class FakeAdapter:
    runtime_id = "codex_cli"

    def __init__(self) -> None:
        self.owner_report: OwnerUsageReport | None = None
        self.owner_requests: list[Any] = []
        self.parses: list[Any] = []
        self.recovery = True

    def supports(self, *, provider_id: str) -> bool:
        return provider_id == "openai"

    async def request_from_owner(self, request):
        self.owner_requests.append(request)
        return self.owner_report

    def idle_probe(self, context):
        return UsageProbePlan(argv=("usage-status", context.credential_home_path))

    def recovery_probe(self, context):
        if not self.recovery:
            return None
        return UsageProbePlan(argv=("native-refresh",), writes_credentials=True)

    def parse_probe(self, plan, exit_code, stdout):
        result = self.parses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeRunner:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.cleanups: list[str] = []
        self.block: asyncio.Event | None = None
        self.on_run = None

    async def run(self, **kwargs):
        self.runs.append(kwargs)
        if self.on_run is not None:
            await self.on_run()
        if self.block is not None:
            await self.block.wait()
        return 0, f"raw provider body {TOKEN}".encode()

    async def cleanup(self, lease_id: str):
        self.cleanups.append(lease_id)
        return {"containerName": f"probe-{lease_id}", "removed": True}


class FakeLeaseClient:
    """Only the calls collection may make; a cooldown report would fail."""

    def __init__(self) -> None:
        self.grant = True
        self.active = True
        self.fence = 7
        self.acquires: list[dict[str, Any]] = []

    async def try_acquire_usage_observation_lease(self, **kwargs):
        self.acquires.append(kwargs)
        if not self.grant:
            return None
        return CredentialLease(
            profile_id=kwargs["profile_id"],
            runtime_id=kwargs["runtime_id"],
            lease_id=kwargs["owner_id"],
            owner_id=kwargs["owner_id"],
            purpose=CredentialLeasePurpose.USAGE_OBSERVATION,
            mode=CredentialLeaseMode.EXCLUSIVE_MAINTENANCE,
            fencing_generation=self.fence,
        )

    async def inspect_lease(self, lease):
        return {
            "active": self.active,
            "lease_id": lease.lease_id,
            "profile_id": lease.profile_id,
            "purpose": CredentialLeasePurpose.USAGE_OBSERVATION.value,
            "fencingGeneration": self.fence,
        }


class FakeBindings:
    def __init__(self) -> None:
        self.bindings: list[Any] = []

    async def list_recoverable(self, *, stale_before):
        return list(self.bindings)


class FakeHostLeases:
    async def get(self, ref):
        return SimpleNamespace(status="ready")


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine, sessions = await sqlite_sessions(tmp_path)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[ProviderProfileSlotLease.__table__]
            )
        )
    clock = Clock()
    adapter, runner, leases, bindings = (
        FakeAdapter(),
        FakeRunner(),
        FakeLeaseClient(),
        FakeBindings(),
    )
    launches: list[str] = []

    async def resolve_launch(profile_id):
        launches.append(profile_id)
        return {"hostImageRef": "image@sha256:" + "a" * 64}

    usage.use_environment(
        usage.UsageCollectionEnvironment(
            session_factory=sessions,
            collectors=lambda: UsageCollectorRegistry((adapter,)),
            lease_client=lambda: leases,
            probe_runner=lambda: runner,
            resolve_launch=resolve_launch,
            runtime_bindings=lambda: bindings,
            host_leases=FakeHostLeases,
            clock=clock,
        )
    )

    def drain_forbidden(*_args, **_kwargs):
        raise AssertionError("usage collection must never drain or migrate hosts")

    monkeypatch.setattr(
        "moonmind.omnigent.generic_host_janitor.GenericOmnigentHostJanitor.drain_profile",
        drain_forbidden,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activities.oauth_session_activities."
        "_drain_generic_profile_hosts",
        drain_forbidden,
    )
    await add_profiles(
        sessions,
        make_profile("alpha", volume_ref="shared-home"),
        make_profile("alias", volume_ref="shared-home"),
    )

    async def claim(profile_id="alpha"):
        async with sessions() as session:
            [result] = await ProviderAccountUsageService(
                session, dispatcher=FakeDispatcher(), clock=clock
            ).request_refresh([profile_id], trigger=RefreshTrigger.MANUAL)
        return {
            "profile_id": profile_id,
            "credential_generation": result.credential_generation,
            "provider_id": "openai",
            "runtime_id": "codex_cli",
            "attempt_id": result.request_id,
            "trigger": "manual",
            "deadline_at": (clock.now + usage_deadline()).isoformat(),
        }

    yield dataclasses.make_dataclass(
        "Env",
        [
            "sessions",
            "clock",
            "adapter",
            "runner",
            "leases",
            "bindings",
            "claim",
            "launches",
        ],
    )(sessions, clock, adapter, runner, leases, bindings, claim, launches)
    usage.use_environment(None)
    await engine.dispose()


def usage_deadline():
    from moonmind.provider_profiles.account_usage import ATTEMPT_DEADLINE

    return ATTEMPT_DEADLINE


async def _granted(env, request):
    resolved = await usage.resolve_collection(request)
    assert resolved["route"] == "idle_probe"
    grant = await usage.try_acquire_grant(
        {
            **request,
            "owner_id": "provider-account-usage:alpha:1",
            "credentialResourceKey": resolved["credentialResourceKey"],
            "aliasProfileIds": resolved["aliasProfileIds"],
        }
    )
    assert grant["granted"] is True
    return {**request, **grant}


async def test_idle_attempt_probes_read_only_under_the_grant(env) -> None:
    env.adapter.parses = [ProbeResult(report=_report(clock=env.clock))]
    request = await env.claim()

    resolved = await usage.resolve_collection(request)
    assert resolved == {
        "status": "ready",
        "route": "idle_probe",
        "credentialResourceKey": credential_resource_key(
            runtime_id="codex_cli", volume_ref="shared-home"
        ),
        "aliasProfileIds": ["alias", "alpha"],
        "owners": [],
    }
    probe_request = await _granted(env, request)
    result = await usage.run_idle_probe(probe_request)

    assert result == {"status": "observed", "snapshotRevision": 1}
    [run] = env.runner.runs
    assert run["exclusive_grant"] is True
    assert run["plan"].writes_credentials is False
    assert (run["home"].volume_ref, run["home"].target_path) == (
        "shared-home",
        "/home/app/.codex",
    )
    [acquire] = env.leases.acquires
    assert acquire["metadata"]["credentialAliasProfileIds"] == ["alias", "alpha"]
    row = await observation_row(env.sessions, "alpha")
    assert row["refresh_state"] == "idle" and row["usage_json"]["windows"]


async def test_active_generic_binding_routes_to_its_owner_with_trusted_binding(
    env,
) -> None:
    env.bindings.bindings = [
        SimpleNamespace(
            bindingId="binding-1",
            hostLeaseRef="host-lease-1",
            providerLeases={"codex": {"providerProfileRef": "alias"}},
            phaseResults={"owner": {"workflowId": "agent-run-1"}},
        )
    ]
    env.adapter.owner_report = _report(clock=env.clock)
    request = await env.claim()

    resolved = await usage.resolve_collection(request)
    assert resolved["route"] == "active_owner"
    assert resolved["owners"][0]["owner_ref"] == "binding-1"
    result = await usage.collect_from_owner({**request, "owners": resolved["owners"]})

    assert result["status"] == "observed"
    [owner_request] = env.adapter.owner_requests
    assert owner_request.target.attempt_id == request["attempt_id"]
    assert owner_request.owner.kind == "runtime_binding"
    assert env.runner.runs == [] and env.leases.acquires == []
    # A report cannot name its own profile, generation, or authority.
    with pytest.raises(ValidationError):
        OwnerUsageReport(profile_id="other", credential_generation=9)


async def test_owner_that_cannot_answer_defers_without_a_second_process(env) -> None:
    env.bindings.bindings = [
        SimpleNamespace(
            bindingId="binding-1",
            hostLeaseRef="host-lease-1",
            providerLeases={"codex": {"providerProfileRef": "alpha"}},
            phaseResults={},
        )
    ]
    request = await env.claim()
    resolved = await usage.resolve_collection(request)

    result = await usage.collect_from_owner({**request, "owners": resolved["owners"]})

    assert result == {"status": "deferred"}
    assert env.runner.runs == [] and env.leases.acquires == []
    assert (await observation_row(env.sessions, "alpha"))["refresh_state"] == "deferred"


async def test_ledger_lease_is_an_active_consumer_not_idleness(env) -> None:
    async with env.sessions() as session:
        session.add(
            ProviderProfileSlotLease(
                runtime_id="codex_cli",
                workflow_id="oauth-session:1",
                profile_id="alias",
                lease_id="oauth-session:1",
                purpose="oauth_reconnect",
                lease_state="held",
            )
        )
        await session.commit()
    request = await env.claim()

    resolved = await usage.resolve_collection(request)
    assert resolved["route"] == "active_owner"
    assert resolved["owners"][0]["kind"] == "ledger_lease"
    assert await usage.collect_from_owner(
        {**request, "owners": resolved["owners"]}
    ) == {"status": "deferred"}
    assert env.adapter.owner_requests == []


async def test_busy_grant_defers_immediately_and_never_probes(env) -> None:
    env.leases.grant = False
    request = await env.claim()
    resolved = await usage.resolve_collection(request)

    grant = await usage.try_acquire_grant(
        {
            **request,
            "owner_id": "usage-1",
            "credentialResourceKey": resolved["credentialResourceKey"],
            "aliasProfileIds": resolved["aliasProfileIds"],
        }
    )

    assert grant == {"granted": False, "status": "deferred"}
    assert env.runner.runs == []


async def test_reconnect_during_probe_rejects_the_obsolete_result(env) -> None:
    env.adapter.parses = [ProbeResult(report=_report(clock=env.clock))]
    request = await _granted(env, await env.claim())

    async def reconnect():
        async with env.sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, "alpha")
            profile.credential_generation += 1
            await session.commit()

    env.runner.on_run = reconnect
    result = await usage.run_idle_probe(request)

    assert result["status"] == "obsolete"
    row = await observation_row(env.sessions, "alpha")
    assert row["usage_json"] is None


async def test_lost_grant_rejects_the_result_before_persistence(env) -> None:
    env.adapter.parses = [ProbeResult(report=_report(clock=env.clock))]
    request = await _granted(env, await env.claim())

    async def lose_grant():
        env.leases.active = False

    env.runner.on_run = lose_grant
    assert await usage.run_idle_probe(request) == {"status": "obsolete"}
    assert (await observation_row(env.sessions, "alpha"))["usage_json"] is None


async def test_expired_token_runs_native_recovery_under_the_grant(env) -> None:
    env.adapter.parses = [
        ProbeResult(recovery_required=True),
        ProbeResult(report=_report(clock=env.clock)),
    ]
    request = await _granted(env, await env.claim())

    result = await usage.run_idle_probe(request)

    assert result["status"] == "observed"
    first, recovery = env.runner.runs
    assert first["plan"].writes_credentials is False
    assert recovery["plan"].writes_credentials is True
    assert recovery["exclusive_grant"] is True


async def test_unsupported_recovery_fails_truthfully_and_keeps_auth_state(env) -> None:
    env.adapter.recovery = False
    env.adapter.parses = [ProbeResult(recovery_required=True)]
    request = await _granted(env, await env.claim())
    before = await profile_snapshot(env.sessions, "alpha")

    result = await usage.run_idle_probe(request)

    assert result == {
        "status": "failed",
        "failure": AccountUsageFailureCode.CREDENTIAL_REFRESH_UNAVAILABLE.value,
    }
    assert len(env.runner.runs) == 1
    assert await profile_snapshot(env.sessions, "alpha") == before
    assert before["auth_state"] == ProviderProfileAuthState.CONNECTED


async def test_monitoring_failures_change_observation_state_only(env) -> None:
    env.adapter.parses = [
        ProbeResult(failure=AccountUsageFailureCode.RATE_LIMITED, retry_after="120")
    ]
    request = await _granted(env, await env.claim())
    before = await profile_snapshot(env.sessions, "alpha")

    result = await usage.run_idle_probe(request)

    assert result == {"status": "failed", "failure": "rate_limited"}
    assert await profile_snapshot(env.sessions, "alpha") == before
    row = await observation_row(env.sessions, "alpha")
    assert row["last_failure_code"] == "rate_limited"
    # The fake lease client has no cooldown surface; reaching for one fails.
    assert not hasattr(env.leases, "record_cooldown")


async def test_no_credential_payload_crosses_the_activity_boundary(env, caplog) -> None:
    env.adapter.parses = [
        ValueError(f"unexpected body {TOKEN}"),
    ]
    request = await _granted(env, await env.claim())

    with caplog.at_level(logging.DEBUG):
        result = await usage.run_idle_probe(request)

    assert result == {"status": "failed", "failure": "internal_error"}
    assert TOKEN not in repr(result) and TOKEN not in caplog.text
    row = await observation_row(env.sessions, "alpha")
    assert TOKEN not in repr(row)
    # Token-shaped account facts are refused by the contract itself.
    with pytest.raises(ValidationError):
        OwnerUsageReport(
            identity={
                "display_name": TOKEN,
                "source": "x",
                "observed_at": env.clock.now,
            }
        )


async def test_cancelled_probe_proves_cleanup_before_cancellation_completes(
    env,
) -> None:
    env.adapter.parses = [ProbeResult(report=_report(clock=env.clock))]
    request = await _granted(env, await env.claim())
    env.runner.block = asyncio.Event()

    task = asyncio.ensure_future(usage.run_idle_probe(request))
    for _ in range(200):
        if env.runner.runs:
            break
        await asyncio.sleep(0.01)
    assert env.runner.runs and env.runner.cleanups == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert env.runner.cleanups == [request["leaseId"]]


async def test_cleanup_defers_a_preempted_attempt(env) -> None:
    request = await _granted(env, await env.claim())

    result = await usage.cleanup_probe({**request, "deferAttempt": True})

    assert result["verified"] is True
    assert env.runner.cleanups == [request["leaseId"]]
    assert (await observation_row(env.sessions, "alpha"))["refresh_state"] == "deferred"


async def test_stale_attempt_is_obsolete_before_any_credential_access(env) -> None:
    request = await env.claim()
    env.clock.advance(minutes=10)

    assert await usage.resolve_collection(request) == {"status": "obsolete"}
    assert env.leases.acquires == [] and env.runner.runs == []


async def test_profile_without_an_enrolled_home_fails_typed(env) -> None:
    async with env.sessions() as session:
        profile = await session.get(ManagedAgentProviderProfile, "alpha")
        profile.volume_ref = None
        await session.commit()
    request = await env.claim()

    assert await usage.resolve_collection(request) == {
        "status": "failed",
        "failure": "usage_access_missing",
    }
    assert env.leases.acquires == [] and env.runner.runs == []
