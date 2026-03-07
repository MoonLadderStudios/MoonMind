from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import (
    execution_integrations as execution_integrations_router,
)
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.workflows.temporal.client import TemporalClientAdapter, WorkflowStartResult

CURRENT_USER_DEP = get_current_user()


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _mock_temporal_client_adapter(monkeypatch):
    async def _mock_start_workflow(self, *args, **kwargs):
        workflow_id = kwargs.get("workflow_id") or "mm:dummy"
        return WorkflowStartResult(
            workflow_id=str(workflow_id),
            run_id=str(uuid4()),
        )

    monkeypatch.setattr(TemporalClientAdapter, "start_workflow", _mock_start_workflow)


@pytest.fixture(autouse=True)
def _reset_callback_rate_limiter():
    execution_integrations_router._callback_rate_limiter = (
        execution_integrations_router._CallbackRateLimiter()
    )
    yield
    execution_integrations_router._callback_rate_limiter = (
        execution_integrations_router._CallbackRateLimiter()
    )


async def _create_monitored_execution(
    client: AsyncClient,
    *,
    execution_suffix: str = "default",
) -> tuple[str, str]:
    create_response = await client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.Run",
            "title": "Integration callback test",
            "idempotencyKey": f"execution-integrations-create-{execution_suffix}",
        },
    )
    assert create_response.status_code == 201
    workflow_id = create_response.json()["workflowId"]

    configure_response = await client.post(
        f"/api/executions/{workflow_id}/integration",
        json={
            "integrationName": "jules",
            "externalOperationId": f"task-{execution_suffix}",
            "normalizedStatus": "running",
            "providerStatus": "running",
            "callbackSupported": True,
        },
    )
    assert configure_response.status_code == 202
    callback_key = configure_response.json()["integration"]["callbackCorrelationKey"]
    return workflow_id, callback_key


@pytest.mark.asyncio
async def test_callback_rejects_missing_configured_token(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/execution_integrations.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", "callback-secret")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key = await _create_monitored_execution(client)
            response = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "completed",
                    "normalizedStatus": "succeeded",
                },
            )

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "integration_callback_unauthorized"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_callback_accepts_matching_bearer_token(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/execution_integrations_auth.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", "callback-secret")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key = await _create_monitored_execution(client)
            response = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                headers={"Authorization": "Bearer callback-secret"},
                json={
                    "eventType": "completed",
                    "providerEventId": "evt-1",
                    "normalizedStatus": "succeeded",
                },
            )

        assert response.status_code == 202
        assert response.json()["integration"]["normalizedStatus"] == "succeeded"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_callback_rejects_payload_over_limit(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/execution_integrations_size.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_max_payload_bytes", 32)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key = await _create_monitored_execution(client)
            response = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "completed",
                    "providerSummary": {"message": "x" * 128},
                },
            )

        assert response.status_code == 413
        assert (
            response.json()["detail"]["code"]
            == "integration_callback_payload_too_large"
        )
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_callback_rejects_rate_limited_bursts(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/execution_integrations_rate_limit.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_per_window", 1)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_window_seconds", 60)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key = await _create_monitored_execution(client)
            first = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "progress",
                    "providerEventId": "evt-rate-1",
                    "normalizedStatus": "running",
                },
            )
            second = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "progress",
                    "providerEventId": "evt-rate-2",
                    "normalizedStatus": "running",
                },
            )

        assert first.status_code == 202
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "integration_callback_rate_limited"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_callback_rate_limit_applies_across_callback_keys(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = (
        f"sqlite+aiosqlite:///{tmp_path}/execution_integrations_shared_rate_limit.db"
    )
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_per_window", 1)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_window_seconds", 60)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key_one = await _create_monitored_execution(
                client,
                execution_suffix="one",
            )
            _workflow_id, callback_key_two = await _create_monitored_execution(
                client,
                execution_suffix="two",
            )
            first = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key_one}",
                json={
                    "eventType": "progress",
                    "providerEventId": "evt-shared-1",
                    "normalizedStatus": "running",
                },
            )
            second = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key_two}",
                json={
                    "eventType": "progress",
                    "providerEventId": "evt-shared-2",
                    "normalizedStatus": "running",
                },
            )

        assert first.status_code == 202
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "integration_callback_rate_limited"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


def test_callback_rate_limiter_evicts_idle_buckets(monkeypatch):
    limiter = execution_integrations_router._CallbackRateLimiter()
    ticks = iter((10.0, 11.0, 80.0))
    monkeypatch.setattr(
        execution_integrations_router.time,
        "monotonic",
        lambda: next(ticks),
    )

    assert limiter.allow(key="jules:callback-1", limit=1, window_seconds=30) is True
    assert limiter.allow(key="jules:callback-2", limit=1, window_seconds=30) is True
    assert set(limiter._buckets) == {"jules:callback-1", "jules:callback-2"}

    assert limiter.allow(key="jules:callbacks", limit=1, window_seconds=30) is True
    assert set(limiter._buckets) == {"jules:callbacks"}


@pytest.mark.asyncio
async def test_callback_can_capture_raw_payload_artifact(tmp_path, monkeypatch):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/execution_integrations_artifact.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_artifact_capture_enabled", True)
    monkeypatch.setattr(settings.spec_workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.spec_workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            _workflow_id, callback_key = await _create_monitored_execution(client)
            response = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "completed",
                    "providerEventId": "evt-artifact",
                    "normalizedStatus": "succeeded",
                },
            )

        assert response.status_code == 202
        assert any(
            str(ref).startswith("art_")
            for ref in response.json().get("artifactRefs", [])
        )
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker
