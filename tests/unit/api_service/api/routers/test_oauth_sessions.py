"""Unit tests for OAuth session API router behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentOAuthSession, OAuthSessionStatus
from api_service.main import app


@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    """Create a single SQLite engine and schema for the module."""
    import asyncio

    tmp = tmp_path_factory.mktemp("integration_db_oauth")
    db_url = f"sqlite+aiosqlite:///{tmp}/shared.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())
    original = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = original
    asyncio.run(_teardown(engine))


@pytest.fixture
def client_app(_module_db) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _oauth_payload(profile_id: str) -> dict[str, object]:
    return {
        "runtime_id": "cursor_cli",
        "profile_id": profile_id,
        "volume_ref": "cursor-auth-volume",
        "account_label": "cursor account",
        "max_parallel_runs": 1,
        "cooldown_after_429_seconds": 300,
        "rate_limit_policy": "backoff",
    }


@pytest.mark.asyncio
async def test_create_oauth_session_expires_stale_active_before_conflict_check(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_profile_id = "cursor-cli-stale-profile"

    async with db_base.async_session_maker() as session:
        stale = ManagedAgentOAuthSession(
            session_id="oas_staleexisting1",
            runtime_id="cursor_cli",
            profile_id=stale_profile_id,
            status=OAuthSessionStatus.PENDING,
            requested_by_user_id="None",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=80),
        )
        session.add(stale)
        await session.commit()

    async def _noop_start(_session_model):
        return None

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _noop_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(stale_profile_id),
        )

    assert response.status_code == 201
    created = response.json()
    assert created["profile_id"] == stale_profile_id
    assert created["status"] == OAuthSessionStatus.PENDING.value

    async with db_base.async_session_maker() as session:
        stale_row = await session.get(ManagedAgentOAuthSession, "oas_staleexisting1")
        assert stale_row is not None
        assert stale_row.status == OAuthSessionStatus.EXPIRED
        assert stale_row.failure_reason is not None


@pytest.mark.asyncio
async def test_create_oauth_session_returns_conflict_for_non_stale_active(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "cursor-cli-busy-profile"

    async with db_base.async_session_maker() as session:
        active = ManagedAgentOAuthSession(
            session_id="oas_activeexisting1",
            runtime_id="cursor_cli",
            profile_id=profile_id,
            status=OAuthSessionStatus.PENDING,
            requested_by_user_id="None",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(active)
        await session.commit()

    async def _unexpected_start(_session_model):
        raise AssertionError("workflow start should not be called when conflict exists")

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _unexpected_start,
    )

    async with client_app as client:
        response = await client.post("/api/v1/oauth-sessions", json=_oauth_payload(profile_id))

    assert response.status_code == 409
    assert response.json()["detail"] == "An active OAuth session already exists for this profile."


@pytest.mark.asyncio
async def test_create_oauth_session_marks_failed_when_workflow_start_fails(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "cursor-cli-start-failure"

    async def _raise_start(_session_model):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _raise_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(profile_id),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to start OAuth session workflow. Please retry."

    async with db_base.async_session_maker() as session:
        query = await session.execute(
            select(ManagedAgentOAuthSession)
            .where(ManagedAgentOAuthSession.profile_id == profile_id)
            .order_by(desc(ManagedAgentOAuthSession.created_at))
        )
        failed_row = query.scalars().first()
        assert failed_row is not None
        assert failed_row.status == OAuthSessionStatus.FAILED
        assert failed_row.failure_reason == "Failed to start OAuth session workflow"
