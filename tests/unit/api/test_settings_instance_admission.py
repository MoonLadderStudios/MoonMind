"""Settings boot and catalog use real operator admission without a person (#4353)."""

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import (
    omnigent_policies,
    omnigent_runtime_provider_migration,
    omnigent_session_timeline,
    provider_profiles,
    secrets,
    settings,
    system_operations,
    workflow_console,
)
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.services.single_user_conversion import run_guarded_upgrade


@pytest_asyncio.fixture
async def instance(tmp_path, monkeypatch):
    for name in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published
    monkeypatch.setattr(db_base, "async_session_maker", factory)

    async def sessions():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(workflow_console.router)
    app.include_router(settings.router, prefix="/api/v1")
    app.include_router(provider_profiles.router, prefix="/api/v1")
    app.include_router(secrets.router, prefix="/api/v1/secrets")
    for module in (
        system_operations,
        omnigent_policies,
        omnigent_session_timeline,
        omnigent_runtime_provider_migration,
    ):
        app.include_router(module.router)
    app.dependency_overrides[db_base.get_async_session] = sessions
    # No admission or identity dependency override, account row or hidden scope.
    yield app
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_admission_boot_catalog_save_and_stale_client(instance):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        info = await client.get("/api/ui/info")
        assert info.status_code == 200, info.text
        assert "settingsPermissions" not in info.json()
        assert all(
            info.json()["features"][name]
            for name in (
                "settingsProvidersSecrets",
                "settingsInstance",
                "settingsOperations",
            )
        )
        catalog = await client.get("/api/v1/settings/catalog")
        assert catalog.status_code == 200, catalog.text
        descriptors = [
            item for group in catalog.json()["categories"].values() for item in group
        ]
        descriptor = next(
            item
            for item in descriptors
            if item["key"] == "workflow.default_publish_mode"
        )
        change = {
            "changes": {descriptor["key"]: "branch"},
            "expected_versions": {descriptor["key"]: descriptor["value_version"]},
        }
        accepted = await client.patch("/api/v1/settings", json=change)
        assert accepted.status_code == 200, accepted.text
        # A fresh override starts at version one. Both clients then read the
        # same saved version before one commits its next change.
        current = await client.get(f"/api/v1/settings/effective/{descriptor['key']}")
        change["expected_versions"][descriptor["key"]] = current.json()["value_version"]
        newer = await client.patch(
            "/api/v1/settings",
            json={
                **change,
                "changes": {descriptor["key"]: "pr"},
            },
        )
        assert newer.status_code == 200, newer.text
        stale = await client.patch("/api/v1/settings", json=change)
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"] == "version_conflict"
        effective = await client.get(f"/api/v1/settings/effective/{descriptor['key']}")
        assert effective.json()["value"] == "pr"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/ui/info",
        "/api/v1/settings/catalog",
        "/api/v1/provider-profiles",
        "/api/v1/secrets",
        "/api/system/worker-pause",
        "/api/omnigent/policies",
        "/api/omnigent/sessions/unknown/timeline",
        "/api/omnigent/runtime-provider-migration",
    ],
)
async def test_settings_surfaces_reject_unadmitted_machine_peer(instance, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=("172.21.0.9", 1234)),
        base_url="http://localhost",
    ) as client:
        response = await client.get(
            path,
            headers={
                "Authorization": "Bearer invalid-machine-token",
                "Cookie": "moonmind_session=retired-account-session",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_instance_settings_keep_host_and_origin_boundaries(instance):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        host = await client.get(
            "/api/v1/settings/catalog", headers={"Host": "foreign.invalid"}
        )
        assert host.status_code == 401
        origin = await client.patch(
            "/api/v1/settings",
            json={"changes": {}},
            headers={"Origin": "http://foreign.invalid"},
        )
        assert origin.status_code == 403
