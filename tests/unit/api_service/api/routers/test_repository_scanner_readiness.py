"""Readiness is authenticated and read-only through the operator API."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import settings as settings_router
from moonmind.config.container_backend_settings import ContainerBackendConfigError


@pytest.mark.asyncio
async def test_readiness_uses_existing_settings_authority(monkeypatch):
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = lambda: (
        object()
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/settings/repository-scanners/readiness")
    assert response.status_code == 200
    assert response.json()["status"] == "unverified"
    assert response.json()["feedSnapshot"] == "unverified"


@pytest.mark.asyncio
async def test_invalid_configuration_is_bounded(monkeypatch):
    def invalid():
        raise ContainerBackendConfigError("secret configuration content")

    monkeypatch.setattr(settings_router, "resolve_container_backend_settings", invalid)
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = lambda: (
        object()
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/settings/repository-scanners/readiness")
    assert response.status_code == 503
    assert "secret configuration content" not in response.text


@pytest.mark.asyncio
async def test_readiness_cannot_bypass_instance_access(monkeypatch):
    from unittest.mock import Mock

    from fastapi import HTTPException

    def denied():
        raise HTTPException(status_code=401, detail="Authentication required")

    resolver = Mock()
    monkeypatch.setattr(settings_router, "resolve_container_backend_settings", resolver)
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = denied
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/settings/repository-scanners/readiness")
    assert response.status_code == 401
    resolver.assert_not_called()
