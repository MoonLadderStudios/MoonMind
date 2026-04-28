from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.system_operations import (
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import Base
from moonmind.config.settings import settings


class FakeTemporalService:
    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0

    async def send_quiesce_pause_signal(self) -> int:
        self.pause_calls += 1
        return 1

    async def send_resume_signal(self) -> int:
        self.resume_calls += 1
        return 1


@pytest.fixture
def system_operations_client(tmp_path) -> Iterator[tuple[TestClient, FakeTemporalService]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/system-ops-api.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    temporal = FakeTemporalService()

    async def _session_dep():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_dep
    app.dependency_overrides[_get_temporal_execution_service] = lambda: temporal
    with TestClient(app) as client:
        yield client, temporal
    asyncio_run(engine.dispose())


def _override_user(app: FastAPI, *, is_superuser: bool) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=is_superuser,
    )
    dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user


def test_get_worker_pause_snapshot_returns_system_metrics_and_audit(
    system_operations_client: tuple[TestClient, FakeTemporalService],
) -> None:
    client, _temporal = system_operations_client
    _override_user(client.app, is_superuser=True)

    response = client.get("/api/system/worker-pause")

    assert response.status_code == 200
    body = response.json()
    assert body["system"]["workersPaused"] is False
    assert body["metrics"]["queued"] == 0
    assert body["metrics"]["isDrained"] is True
    assert body["audit"]["latest"] == []
    assert "signalStatus" in body


def test_post_pause_and_resume_return_snapshots_and_call_subsystem(
    system_operations_client: tuple[TestClient, FakeTemporalService],
) -> None:
    client, temporal = system_operations_client
    _override_user(client.app, is_superuser=True)

    pause = client.post(
        "/api/system/worker-pause",
        json={
            "action": "pause",
            "mode": "quiesce",
            "reason": "Maintenance",
            "confirmation": "Pause workers confirmed",
        },
    )
    resume = client.post(
        "/api/system/worker-pause",
        json={"action": "resume", "reason": "Maintenance complete"},
    )

    assert pause.status_code == 200
    assert pause.json()["system"]["workersPaused"] is True
    assert pause.json()["system"]["mode"] == "quiesce"
    assert resume.status_code == 200
    assert resume.json()["system"]["workersPaused"] is False
    assert temporal.pause_calls == 1
    assert temporal.resume_calls == 1


def test_non_admin_post_is_rejected_without_subsystem_invocation(
    system_operations_client: tuple[TestClient, FakeTemporalService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, temporal = system_operations_client
    _override_user(client.app, is_superuser=False)
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "default")

    response = client.post(
        "/api/system/worker-pause",
        json={
            "action": "pause",
            "mode": "quiesce",
            "reason": "Maintenance",
            "confirmation": "Pause workers confirmed",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "worker_operation_forbidden"
    assert temporal.pause_calls == 0


def test_missing_confirmation_and_invalid_values_are_rejected(
    system_operations_client: tuple[TestClient, FakeTemporalService],
) -> None:
    client, temporal = system_operations_client
    _override_user(client.app, is_superuser=True)

    missing_confirmation = client.post(
        "/api/system/worker-pause",
        json={"action": "pause", "mode": "drain", "reason": "Maintenance"},
    )
    invalid_action = client.post(
        "/api/system/worker-pause",
        json={"action": "shell", "reason": "Maintenance"},
    )
    forced_resume_without_confirmation = client.post(
        "/api/system/worker-pause",
        json={
            "action": "resume",
            "reason": "Maintenance complete",
            "forceResume": True,
        },
    )

    assert missing_confirmation.status_code == 422
    assert missing_confirmation.json()["detail"]["code"] == (
        "worker_operation_confirmation_required"
    )
    assert invalid_action.status_code == 422
    assert forced_resume_without_confirmation.status_code == 422
    assert forced_resume_without_confirmation.json()["detail"]["code"] == (
        "worker_operation_confirmation_required"
    )
    assert temporal.pause_calls == 0
