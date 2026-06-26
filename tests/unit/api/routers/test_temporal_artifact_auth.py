"""Router auth-mode behavior tests for Temporal artifact endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.config.settings import settings

@pytest.fixture
def _restore_auth_provider() -> Iterator[None]:
    original = settings.oidc.AUTH_PROVIDER
    try:
        yield
    finally:
        settings.oidc.AUTH_PROVIDER = original

def _override_user_dependencies(app: FastAPI) -> None:
    mock_user = SimpleNamespace(
        id=uuid4(), email="artifact@example.com", is_active=True
    )
    user_dependencies = {
        dep.call
        for route_item in router.routes
        if route_item.dependant is not None
        for dep in route_item.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user

def test_disabled_mode_attributes_to_default_local_principal(
    _restore_auth_provider,
) -> None:
    """Disabled auth should allow calls and map to default local principal."""

    settings.oidc.AUTH_PROVIDER = "disabled"

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.create.return_value = (
        SimpleNamespace(
            artifact_id="art_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256=None,
            size_bytes=None,
            content_type=None,
            encryption=SimpleNamespace(value="none"),
        ),
        SimpleNamespace(
            mode="single_put",
            upload_url="/api/artifacts/art_01ARZ3NDEKTSV4RRFFQ69G5FAV/content",
            upload_id=None,
            expires_at=datetime.now(UTC),
            max_size_bytes=10485760,
            required_headers={},
        ),
    )
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
    _override_user_dependencies(app)

    with TestClient(app) as client:
        response = client.post("/api/artifacts", json={"content_type": "text/plain"})

    assert response.status_code == 201
    kwargs = service.create.await_args.kwargs
    assert kwargs["principal"] is not None

def test_authenticated_mode_requires_identity(_restore_auth_provider) -> None:
    """Authenticated mode should reject unauthenticated artifact route calls."""

    settings.oidc.AUTH_PROVIDER = "local"

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_temporal_artifact_service] = AsyncMock

    with TestClient(app) as client:
        response = client.post("/api/artifacts", json={"content_type": "text/plain"})

    assert response.status_code in {401, 403}
