"""Contract tests for Temporal artifact lifecycle endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db import models as db_models
from moonmind.schemas.temporal_artifact_models import ArtifactMetadataModel


def _artifact() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        artifact_id=f"art_{uuid4().hex[:26].upper()}",
        created_at=now,
        created_by_principal="user-1",
        content_type="text/plain",
        size_bytes=5,
        sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        storage_backend=db_models.TemporalArtifactStorageBackend.S3,
        storage_key="moonmind/artifacts/2026/03/05/demo",
        encryption=db_models.TemporalArtifactEncryption.NONE,
        status=db_models.TemporalArtifactStatus.COMPLETE,
        retention_class=db_models.TemporalArtifactRetentionClass.STANDARD,
        expires_at=None,
        redaction_level=db_models.TemporalArtifactRedactionLevel.NONE,
        metadata_json={},
    )


def _policy(artifact: SimpleNamespace) -> SimpleNamespace:
    ref = SimpleNamespace(
        artifact_ref_v=1,
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        content_type=artifact.content_type,
        encryption=artifact.encryption.value,
    )
    return SimpleNamespace(
        raw_access_allowed=True, preview_artifact_ref=None, default_read_ref=ref
    )


def _build_app() -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
    mock_user = SimpleNamespace(
        id=uuid4(), email="lifecycle@example.com", is_active=True
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
    return app, service


def test_pin_unpin_delete_contracts() -> None:
    app, service = _build_app()
    artifact = _artifact()
    service.get_metadata.return_value = (artifact, [], True, _policy(artifact))

    with TestClient(app) as client:
        pin_response = client.post(
            f"/api/artifacts/{artifact.artifact_id}/pin", json={"reason": "keep"}
        )
        assert pin_response.status_code == 200
        ArtifactMetadataModel.model_validate(pin_response.json())

        unpin_response = client.delete(f"/api/artifacts/{artifact.artifact_id}/pin")
        assert unpin_response.status_code == 200
        ArtifactMetadataModel.model_validate(unpin_response.json())

        delete_response = client.delete(f"/api/artifacts/{artifact.artifact_id}")
        assert delete_response.status_code == 200
        ArtifactMetadataModel.model_validate(delete_response.json())
