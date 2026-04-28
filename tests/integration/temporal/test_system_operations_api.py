from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.system_operations import _get_temporal_execution_service
from api_service.db.base import get_async_session
from api_service.db.models import Base
from api_service.main import app


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


class FakeTemporalService:
    async def send_quiesce_pause_signal(self) -> int:
        return 0

    async def send_resume_signal(self) -> int:
        return 0


def _override_user() -> object:
    return SimpleNamespace(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=True,
    )


def test_worker_pause_route_matches_settings_runtime_contract(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/system-ops-int.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session_dep():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_dep
    app.dependency_overrides[_get_temporal_execution_service] = FakeTemporalService
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dependency in dependant.dependencies:
            if getattr(dependency.call, "__name__", "") == "_current_user_fallback":
                app.dependency_overrides[dependency.call] = _override_user
    try:
        with TestClient(app) as client:
            settings_page = client.get("/tasks/settings?section=operations")
            snapshot = client.get("/api/system/worker-pause")
            command = client.post(
                "/api/system/worker-pause",
                json={
                    "action": "pause",
                    "mode": "drain",
                    "reason": "Integration maintenance",
                    "confirmation": "Pause workers confirmed",
                },
            )
    finally:
        app.dependency_overrides.clear()
        asyncio_run(engine.dispose())

    assert settings_page.status_code == 200
    assert snapshot.status_code == 200
    assert snapshot.json()["system"]["workersPaused"] is False
    assert command.status_code == 200
    assert command.json()["system"]["workersPaused"] is True
    assert command.json()["audit"]["latest"][0]["action"] == "pause"
