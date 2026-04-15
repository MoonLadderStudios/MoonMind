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
from api_service.db.models import (
    Base,
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    OAuthSessionStatus,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
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
        "runtime_id": "codex_cli",
        "profile_id": profile_id,
        "volume_ref": "codex_auth_volume",
        "account_label": "codex account",
        "max_parallel_runs": 1,
        "cooldown_after_429_seconds": 300,
        "rate_limit_policy": "backoff",
    }


@pytest.mark.asyncio
async def test_create_oauth_session_expires_stale_active_before_conflict_check(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_profile_id = "codex-cli-stale-profile"

    async with db_base.async_session_maker() as session:
        stale = ManagedAgentOAuthSession(
            session_id="oas_staleexisting1",
            runtime_id="codex_cli",
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
async def test_create_codex_oauth_session_applies_durable_auth_volume_defaults(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    async def _capture_start(session_model):
        captured["volume_ref"] = session_model.volume_ref
        captured["volume_mount_path"] = session_model.volume_mount_path
        captured["metadata_json"] = session_model.metadata_json

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    payload = {
        "runtime_id": "codex_cli",
        "profile_id": "codex-cli-default-volume",
        "account_label": "codex account",
    }
    async with client_app as client:
        response = await client.post("/api/v1/oauth-sessions", json=payload)

    assert response.status_code == 201
    assert captured["volume_ref"] == "codex_auth_volume"
    assert captured["volume_mount_path"] == "/home/app/.codex"
    assert captured["metadata_json"]["provider_id"] == "openai"
    assert captured["metadata_json"]["provider_label"] == "OpenAI"


@pytest.mark.asyncio
async def test_create_oauth_session_returns_conflict_for_non_stale_active(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "codex-cli-busy-profile"

    async with db_base.async_session_maker() as session:
        active = ManagedAgentOAuthSession(
            session_id="oas_activeexisting1",
            runtime_id="codex_cli",
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
    profile_id = "codex-cli-start-failure"

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


@pytest.mark.asyncio
async def test_finalize_oauth_session_rejects_failed_volume_verification(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "oas_verifyfailed1"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-cli-failed-verify",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                account_label="codex account",
            )
        )
        await session.commit()

    async def _failed_verify(**_kwargs):
        return {"verified": False, "reason": "no_credentials_found"}

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _failed_verify,
    )

    async with client_app as client:
        response = await client.post(f"/api/v1/oauth-sessions/{session_id}/finalize")

    assert response.status_code == 400
    assert response.json()["detail"] == "Volume verification failed: no_credentials_found"

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.status == OAuthSessionStatus.FAILED
        assert row.failure_reason == "Volume verification failed: no_credentials_found"


@pytest.mark.asyncio
async def test_finalize_oauth_session_registers_oauth_home_codex_profile(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "oas_registercodex1"
    profile_id = "codex-cli-register-oauth"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                account_label="codex account",
                metadata_json={
                    "provider_id": "openai",
                    "provider_label": "OpenAI",
                    "max_parallel_runs": 2,
                    "cooldown_after_429_seconds": 300,
                    "rate_limit_policy": "backoff",
                },
            )
        )
        await session.commit()

    async def _successful_verify(**kwargs):
        assert kwargs["runtime_id"] == "codex_cli"
        assert kwargs["volume_ref"] == "codex_auth_volume"
        assert kwargs["volume_mount_path"] == "/home/app/.codex"
        return {"verified": True, "found": ["auth.json"], "missing": []}

    async def _noop_sync(**_kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _successful_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions.sync_provider_profile_manager",
        _noop_sync,
    )

    async with client_app as client:
        response = await client.post(f"/api/v1/oauth-sessions/{session_id}/finalize")

    assert response.status_code == 200

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        assert profile.runtime_id == "codex_cli"
        assert profile.provider_id == "openai"
        assert profile.provider_label == "OpenAI"
        assert profile.credential_source == ProviderCredentialSource.OAUTH_VOLUME
        assert (
            profile.runtime_materialization_mode
            == RuntimeMaterializationMode.OAUTH_HOME
        )
        assert profile.volume_ref == "codex_auth_volume"
        assert profile.volume_mount_path == "/home/app/.codex"
        assert profile.max_parallel_runs == 2
