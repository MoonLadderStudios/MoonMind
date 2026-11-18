"""API-to-collector delivery contract consumed by the Settings integration test.

Only external workflow transport, container execution and credential lease stores
are substituted. Routes, dispatcher payloads, adapters, activities, database and
response serialization are production code. The shared fixture keeps the
Settings delivery fields, not unrelated creation templates or runtime defaults. This is not live-provider evidence.
Regenerate the sanitized response fixture with UPDATE_USAGE_DELIVERY_FIXTURE=1.
"""

from __future__ import annotations

import itertools
import json
import os
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from api_service.api.routers import provider_profiles as router
from api_service.auth_providers import get_instance_access
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderProfileSlotLease,
)
from api_service.services import provider_account_usage as service_module
from api_service.services.provider_account_usage import (
    ProviderAccountUsageService,
    get_account_usage_dispatcher,
)
from api_service.services.provider_account_usage_collection import (
    TemporalAccountUsageDispatcher,
)
from moonmind.provider_profiles.usage_collection import default_usage_collectors
from moonmind.workflows.temporal.activities import (
    provider_account_usage_activities as activities,
)
from tests.unit.provider_profiles.test_claude_account_usage_collection import (
    _observed as claude_observed,
)
from tests.unit.provider_profiles.test_claude_account_usage_collection import (
    _profile_body,
)
from tests.unit.provider_profiles.test_codex_account_usage_collection import (
    _bucket,
)
from tests.unit.provider_profiles.test_codex_account_usage_collection import (
    _observed as codex_observed,
)
from tests.unit.services.account_usage_support import (
    Clock,
    add_profiles,
    make_profile,
    sqlite_sessions,
)
from tests.unit.workflows.temporal.activities.test_provider_account_usage_activities import (
    FakeBindings,
    FakeHostLeases,
    FakeLeaseClient,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
FIXTURE = (
    Path(__file__).parents[2] / "fixtures/provider/account_usage_settings_delivery.json"
)
NOW = datetime(2026, 11, 18, 20, tzinfo=UTC)
IDS = ["codex-a", "codex-b", "claude-a", "claude-b"]
SETTINGS_FIELDS = (
    "profile_id",
    "runtime_id",
    "provider_id",
    "credential_generation",
    "credential_source",
    "runtime_materialization_mode",
    "volume_ref",
    "volume_mount_path",
    "account_label",
    "model_tiers",
    "default_model_tier",
    "max_parallel_runs",
    "cooldown_after_429_seconds",
    "rate_limit_policy",
    "enabled",
    "is_default",
    "secret_refs",
    "readiness",
    "auth_state",
    "authentication_method",
    "launch_ready",
    "account_usage",
)


def settings_delivery(rows):
    # Keep the complete usage contract. Unrelated launch/creation templates are
    # validated by their owners, not frozen by provider-usage qualification.
    return [{key: row[key] for key in SETTINGS_FIELDS} for row in rows]


class WorkflowTransport:
    """Capture production dispatch until the API transaction has committed."""

    def __init__(self):
        self.pending = []

    async def start_workflow(self, name, request, **options):
        assert name == "MoonMind.ProviderAccountUsageCollection"
        self.pending.append((request, options["id"]))

    async def drain(self):
        outcomes = []
        while self.pending:
            request, owner_id = self.pending.pop(0)
            resolved = await activities.resolve_collection(request)
            assert resolved["route"] == "idle_probe", resolved
            grant = await activities.try_acquire_grant(
                {
                    **request,
                    "owner_id": owner_id,
                    "credentialResourceKey": resolved["credentialResourceKey"],
                    "aliasProfileIds": resolved["aliasProfileIds"],
                }
            )
            scoped = {**request, **grant}
            outcomes.append(await activities.run_idle_probe(scoped))
            assert (await activities.cleanup_probe(scoped))["verified"]
        return outcomes


class ContainerTransport:
    def __init__(self, clock, sessions):
        self.clock, self.sessions = clock, sessions
        self.rebind = None
        self.new_account = False
        self.calls = []

    async def run(self, *, profile_id, **kwargs):
        self.calls.append(profile_id)
        first = profile_id.endswith("-a")
        used = 42 if first else 81
        email = (
            "new-account@example.com"
            if self.new_account and first
            else "person@example.com"
        )
        account = "new-account" if self.new_account and first else "account"
        workspace = "workspace-a" if first else "workspace-b"
        reset = datetime(2026, 11, 20, 9, tzinfo=UTC)
        if profile_id.startswith("claude"):
            output = claude_observed(
                self.clock,
                usage={
                    "seven_day": {"utilization": used, "resets_at": reset.isoformat()},
                    "five_hour": {"utilization": 7, "resets_at": None},
                },
                profile=_profile_body(account, workspace, email),
            )
        else:
            output = codex_observed(
                self.clock,
                account={
                    "account": {
                        "type": "chatgpt",
                        "email": email,
                        "planType": "plus",
                        "workspaceName": workspace,
                    },
                    "userId": account,
                    "workspaceRouting": {"chatgptAccountId": workspace},
                },
                account_id=workspace,
                limits={"rateLimits": _bucket(used, reset=reset)},
            )
        if self.rebind == profile_id:
            async with self.sessions() as session:
                await session.execute(
                    update(ManagedAgentProviderProfile)
                    .where(ManagedAgentProviderProfile.profile_id == profile_id)
                    .values(credential_generation=2, updated_at=NOW)
                )
                await session.commit()
            self.rebind = None
        return 0, json.dumps({"v": 1, **output}).encode()

    async def cleanup(self, lease_id):
        return {"containerName": lease_id, "removed": True}


async def test_two_provider_refresh_delivers_settings_contract_and_fences_rebind(
    tmp_path, monkeypatch
):
    engine, sessions = await sqlite_sessions(tmp_path)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[ProviderProfileSlotLease.__table__]
            )
        )
    clock = Clock(NOW)
    sequence = itertools.count(1)
    monkeypatch.setattr(service_module, "uuid4", lambda: UUID(int=next(sequence)))
    monkeypatch.setattr(
        router,
        "ProviderAccountUsageService",
        partial(ProviderAccountUsageService, clock=clock),
    )
    transport = WorkflowTransport()

    async def workflow_client():
        return transport

    dispatcher = TemporalAccountUsageDispatcher(client_factory=workflow_client)
    container = ContainerTransport(clock, sessions)
    leases = FakeLeaseClient()

    async def launch(_profile_id):
        return {"hostImageRef": "image@sha256:" + "a" * 64}

    activities.use_environment(
        activities.UsageCollectionEnvironment(
            session_factory=sessions,
            collectors=default_usage_collectors,
            lease_client=lambda: leases,
            probe_runner=lambda: container,
            resolve_launch=launch,
            runtime_bindings=FakeBindings,
            host_leases=FakeHostLeases,
            clock=clock,
        )
    )
    profiles = []
    for profile_id in IDS:
        values = dict(
            account_label=f"Editable {profile_id}", created_at=NOW, updated_at=NOW
        )
        if profile_id.startswith("claude"):
            values.update(
                runtime_id="claude_code",
                provider_id="anthropic",
                volume_mount_path="/home/app/.claude",
            )
        profiles.append(make_profile(profile_id, **values))
    await add_profiles(sessions, *profiles)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router.router, prefix="/api/v1")
    app.dependency_overrides[db_base.get_async_session] = session_dependency
    app.dependency_overrides[get_account_usage_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_instance_access] = lambda: SimpleNamespace(
        id=UUID(int=1), email="operator@example.com", is_active=True, is_superuser=False
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:

            async def snapshot():
                response = await client.get("/api/v1/provider-profiles")
                assert response.status_code == 200, response.text
                rows = response.json()
                for row in rows:
                    detail = await client.get(
                        "/api/v1/provider-profiles/" + row["profile_id"]
                    )
                    assert detail.status_code == 200 and detail.json() == row
                return rows

            initial = await snapshot()
            assert all(row["account_usage"]["status"] == "unknown" for row in initial)
            response = await client.post(
                "/api/v1/provider-profiles/account-usage/refresh",
                json={"profile_ids": IDS},
            )
            assert response.status_code == 200, response.text
            refresh = response.json()
            assert all(
                row["outcome"] == "accepted" and row["refresh_state"] == "queued"
                for row in refresh["results"]
            )
            assert container.calls == []  # API never waits on credentials/providers.
            assert all(row["status"] == "observed" for row in await transport.drain())
            refreshed = await snapshot()
            assert len(container.calls) == 4
            for row in refreshed:
                projection = row["account_usage"]
                assert (
                    projection["status"] == "fresh" and projection["binding_verified"]
                )
                assert projection["account"]["email"] == "person@example.com"
                weekly = next(
                    w
                    for w in projection["usage"]["windows"]
                    if w["key"] == projection["usage"]["primary_window_key"]
                )
                assert weekly["remaining_percent"] == (
                    58 if row["profile_id"].endswith("-a") else 19
                )
            # Both providers fence an old response after reconnect, then accept
            # a new-generation response without contaminating the other workspace.
            clock.advance(minutes=2)
            for profile_id in ("codex-a", "claude-a"):
                container.rebind = profile_id
                result = await client.post(
                    "/api/v1/provider-profiles/account-usage/refresh",
                    json={"profile_ids": [profile_id]},
                )
                assert result.json()["results"][0]["outcome"] == "accepted"
                assert (await transport.drain())[0]["status"] == "obsolete"
                invalidated = (
                    await client.get("/api/v1/provider-profiles/" + profile_id)
                ).json()["account_usage"]
                assert invalidated["credential_generation"] == 2
                assert invalidated["account"]["email"] is None
                assert invalidated["usage"]["windows"] == []
            container.new_account = True
            result = await client.post(
                "/api/v1/provider-profiles/account-usage/refresh",
                json={"profile_ids": ["codex-a", "claude-a"]},
            )
            assert all(r["outcome"] == "accepted" for r in result.json()["results"])
            assert all(r["status"] == "observed" for r in await transport.drain())
            rebound = await snapshot()
            for row in rebound:
                assert row["account_usage"]["account"]["email"] == (
                    "new-account@example.com"
                    if row["profile_id"].endswith("-a")
                    else "person@example.com"
                )
            actual = {
                "now": NOW.isoformat().replace("+00:00", "Z"),
                "initial": settings_delivery(initial),
                "refresh": refresh,
                "refreshed": settings_delivery(refreshed),
                "rebound": settings_delivery(rebound),
            }
            if os.environ.get("UPDATE_USAGE_DELIVERY_FIXTURE") == "1":
                FIXTURE.write_text(json.dumps(actual, indent=2) + "\n")
            assert actual == json.loads(FIXTURE.read_text())
    finally:
        activities.use_environment(None)
        await engine.dispose()
