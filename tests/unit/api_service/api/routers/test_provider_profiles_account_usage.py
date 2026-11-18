"""Cached account-usage projection and refresh command on Provider Profiles (#5362)."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import provider_profiles as provider_profiles_router
from api_service.auth_providers import get_instance_access
from api_service.db import base as db_base
from api_service.db.models import Base, ProviderProfileAuthState
from api_service.services.provider_account_usage import (
    ProviderAccountUsageService,
    get_account_usage_dispatcher,
)
from moonmind.provider_profiles.account_usage import AccountUsageFailureCode
from tests.unit.services.account_usage_support import (
    FakeDispatcher,
    make_profile,
    observation,
    usage,
    window,
)

pytestmark = pytest.mark.asyncio

_SECRET_PATTERNS = re.compile(
    r"(?i)(eyJ[A-Za-z0-9_-]{6,}\.|bearer\s|sk-[A-Za-z0-9]{6}|access_token|"
    r"refresh_token|id_token|authorization|acct-secret|ws-secret)"
)


@pytest_asyncio.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/api.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    async def _session():
        async with sessions() as session:
            yield session

    dispatcher = FakeDispatcher()
    app = FastAPI()
    app.include_router(provider_profiles_router.router, prefix="/api/v1")
    app.dependency_overrides[db_base.get_async_session] = _session
    app.dependency_overrides[get_account_usage_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_instance_access] = lambda: SimpleNamespace(
        id=uuid4(), email="operator@example.com", is_active=True, is_superuser=False
    )
    async with sessions() as session:
        session.add_all(
            [
                make_profile("alpha", priority=300),
                make_profile("beta", priority=200),
                make_profile(
                    "gamma",
                    priority=100,
                    enabled=False,
                    auth_state=ProviderProfileAuthState.DISCONNECTED,
                ),
            ]
        )
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield SimpleNamespace(
            client=client,
            sessions=sessions,
            dispatcher=dispatcher,
            statements=statements,
        )
    await engine.dispose()


async def _ingest(api, obs):
    async with api.sessions() as session:
        result = await ProviderAccountUsageService(
            session, dispatcher=api.dispatcher
        ).ingest_observation(obs)
    assert result.accepted
    return result


def _now() -> datetime:
    return datetime.now(UTC)


async def test_list_and_detail_serve_cached_projection_in_one_batch(api) -> None:
    await _ingest(
        api,
        observation(
            "alpha",
            account_id="acct-secret",
            workspace_id="ws-secret",
            owner_expires_at=_now() + timedelta(hours=1),
            identity={
                "email": "person@example.com",
                "source": "fake_identity",
                "observed_at": _now(),
            },
            usage=usage("full", _now(), window("weekly", used=42)),
        ),
    )
    # A slow, failing collector must never be consulted by cache-only reads.
    api.dispatcher.delay = 30
    api.dispatcher.error = RuntimeError("provider unavailable")
    api.statements.clear()
    started = time.monotonic()
    response = await api.client.get("/api/v1/provider-profiles")
    assert time.monotonic() - started < 5
    assert response.status_code == 200
    observation_queries = [
        s for s in api.statements if "provider_account_usage_observations" in s
    ]
    assert len(observation_queries) == 1
    assert api.dispatcher.requests == []

    by_id = {p["profile_id"]: p for p in response.json()}
    alpha = by_id["alpha"]["account_usage"]
    assert alpha["status"] == "fresh"
    assert alpha["binding_verified"] is True
    assert alpha["account"]["email"] == "person@example.com"
    assert alpha["usage"]["windows"][0]["remaining_percent"] == 58
    assert alpha["usage"]["primary_window_key"] == "weekly"
    assert alpha["refresh"]["state"] == "idle"
    assert by_id["beta"]["account_usage"]["status"] == "unknown"
    assert by_id["beta"]["account_usage"]["snapshot_revision"] == 0
    assert by_id["gamma"]["account_usage"]["status"] == "reconnect_required"

    detail = await api.client.get("/api/v1/provider-profiles/alpha")
    assert detail.status_code == 200
    assert detail.json()["account_usage"] == alpha
    assert api.dispatcher.requests == []
    projections = [p["account_usage"] for p in response.json()]
    assert not _SECRET_PATTERNS.search(json.dumps(projections))
    assert "acct-secret" not in response.text + detail.text
    assert "ws-secret" not in response.text + detail.text


async def test_refresh_command_reports_per_profile_outcomes(api) -> None:
    api.dispatcher.supported = {"openai"}
    response = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh",
        json={"profile_ids": ["beta", "alpha", "gamma", "alpha"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["profile_id"] for r in results] == ["beta", "alpha", "gamma"]
    beta, alpha, gamma = results
    assert beta["outcome"] == "accepted" and beta["refresh_state"] == "queued"
    assert beta["credential_generation"] == 1 and beta["request_id"]
    assert gamma["outcome"] == "not_connected"
    assert sorted(r.profile_id for r in api.dispatcher.requests) == ["alpha", "beta"]

    # Accepted means requested: the cached projection did not change yet.
    detail = (await api.client.get("/api/v1/provider-profiles/beta")).json()
    assert detail["account_usage"]["refresh"]["state"] == "queued"
    assert detail["account_usage"]["usage"]["windows"] == []

    async with api.sessions() as session:
        await ProviderAccountUsageService(
            session, dispatcher=api.dispatcher
        ).ingest_observation(
            observation(
                "beta",
                attempt_id=beta["request_id"],
                usage=usage("full", _now(), window("weekly")),
            )
        )
    again = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh",
        json={"profile_ids": ["beta"], "trigger": "manual"},
    )
    [limited] = again.json()["results"]
    assert limited["outcome"] == "backoff"
    assert 0 < limited["retry_after_seconds"] <= 60
    visible = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh",
        json={"profile_ids": ["beta"], "trigger": "visibility"},
    )
    assert visible.json()["results"][0]["outcome"] == "already_fresh"


async def test_refresh_without_collector_reports_not_supported(api) -> None:
    api.dispatcher.supported = set()
    response = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh",
        json={"profile_ids": ["alpha"]},
    )
    assert response.json()["results"][0]["outcome"] == "not_supported"
    assert api.dispatcher.requests == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"profile_ids": []},
        {"profile_ids": [""]},
        {"profile_ids": ["x" * 129]},
        {"profile_ids": [f"p{i}" for i in range(51)]},
        {"profile_ids": ["alpha"], "trigger": "oauth"},
        {"profile_ids": ["alpha"], "raw": {"token": "x"}},
        {"profile_ids": "alpha"},
    ],
)
async def test_refresh_rejects_invalid_input_deterministically(api, body) -> None:
    response = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh", json=body
    )
    assert response.status_code == 422
    assert api.dispatcher.requests == []


async def test_unknown_profiles_are_rejected_without_side_effects_or_leaks(api) -> None:
    response = await api.client.post(
        "/api/v1/provider-profiles/account-usage/refresh",
        json={"profile_ids": ["alpha", "missing-profile"]},
    )
    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "provider_profiles_not_found",
            "message": "One or more requested provider profiles do not exist.",
            "profile_ids": ["missing-profile"],
        }
    }
    assert "beta" not in response.text and "gamma" not in response.text
    assert api.dispatcher.requests == []
    detail = (await api.client.get("/api/v1/provider-profiles/alpha")).json()
    assert detail["account_usage"]["refresh"]["state"] == "idle"


async def test_monitoring_outcomes_leave_profile_execution_state_unchanged(api) -> None:
    before = (await api.client.get("/api/v1/provider-profiles/alpha")).json()
    [accepted] = (
        await api.client.post(
            "/api/v1/provider-profiles/account-usage/refresh",
            json={"profile_ids": ["alpha"]},
        )
    ).json()["results"]
    async with api.sessions() as session:
        failure = await ProviderAccountUsageService(
            session, dispatcher=api.dispatcher
        ).record_collection_failure(
            profile_id="alpha",
            credential_generation=1,
            attempt_id=accepted["request_id"],
            code=AccountUsageFailureCode.RATE_LIMITED,
            retry_after="3600",
        )
    assert failure.accepted
    after = (await api.client.get("/api/v1/provider-profiles/alpha")).json()
    assert after["account_usage"]["refresh"]["state"] == "backoff"
    assert after["account_usage"]["refresh"]["last_failure"] == "rate_limited"
    for key in (
        "enabled",
        "is_default",
        "priority",
        "auth_state",
        "launch_ready",
        "readiness",
        "cooldown_after_429_seconds",
        "command_behavior",
        "model_tiers",
        "default_model_tier",
        "updated_at",
    ):
        assert after[key] == before[key], key


async def test_openapi_exposes_projection_and_refresh_schemas(api) -> None:
    app = FastAPI()
    app.include_router(provider_profiles_router.router, prefix="/api/v1")
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    profile = schemas["ProviderProfileResponse"]["properties"]["account_usage"]
    assert {"$ref": "#/components/schemas/ProviderAccountUsageProjection"} in profile[
        "anyOf"
    ]
    refresh = spec["paths"]["/api/v1/provider-profiles/account-usage/refresh"]["post"]
    assert refresh["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProviderAccountUsageRefreshResponse"
    }
    assert "404" in refresh["responses"]
    assert set(schemas["RefreshOutcome"]["enum"]) == {
        "already_fresh",
        "accepted",
        "deferred_busy",
        "backoff",
        "not_supported",
        "not_connected",
        "disabled",
    }
    assert "bound_account_id" not in json.dumps(schemas)
