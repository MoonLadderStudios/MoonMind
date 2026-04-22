"""Contract tests for Temporal artifact API core surfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db import models as db_models
from moonmind.schemas.temporal_artifact_models import (
    ArtifactListResponse,
    ArtifactMetadataModel,
    CreateArtifactResponse,
    PresignDownloadResponse,
    PresignUploadPartResponse,
)


def _build_artifact() -> SimpleNamespace:
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


def _build_read_policy(artifact: SimpleNamespace) -> SimpleNamespace:
    artifact_ref = SimpleNamespace(
        artifact_ref_v=1,
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        content_type=artifact.content_type,
        encryption=artifact.encryption.value,
    )
    return SimpleNamespace(
        raw_access_allowed=True,
        preview_artifact_ref=None,
        default_read_ref=artifact_ref,
    )


def _build_link(
    *, link_type: str = "output.primary", label: str = "Final output"
) -> SimpleNamespace:
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="wf-1",
        run_id="run-1",
        link_type=link_type,
        label=label,
        created_at=datetime.now(UTC),
        created_by_activity_type=None,
        created_by_worker=None,
    )


def _build_app() -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
    mock_user = SimpleNamespace(
        id=uuid4(), email="contract@example.com", is_active=True
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


def test_temporal_artifact_create_and_presign_contracts() -> None:
    app, service = _build_app()
    artifact = _build_artifact()
    service.create.return_value = (
        artifact,
        SimpleNamespace(
            mode="multipart",
            upload_url=None,
            upload_id="upload-1",
            expires_at=datetime.now(UTC),
            max_size_bytes=10 * 1024 * 1024,
            required_headers={},
        ),
    )
    service.presign_upload_part.return_value = SimpleNamespace(
        part_number=1,
        url="https://example.test/upload-part",
        expires_at=datetime.now(UTC),
        required_headers={},
    )

    with TestClient(app) as client:
        create_response = client.post(
            "/api/artifacts",
            json={"content_type": "text/plain", "size_bytes": 99_999_999},
        )
        assert create_response.status_code == 201
        CreateArtifactResponse.model_validate(create_response.json())

        part_response = client.post(
            f"/api/artifacts/{artifact.artifact_id}/presign-upload-part",
            json={"part_number": 1},
        )
        assert part_response.status_code == 200
        PresignUploadPartResponse.model_validate(part_response.json())


def test_temporal_artifact_get_list_presign_download_contracts() -> None:
    app, service = _build_app()
    artifact = _build_artifact()
    link = _build_link()
    service.get_metadata.return_value = (
        artifact,
        [link],
        False,
        _build_read_policy(artifact),
    )
    service.list_for_execution.return_value = [artifact]
    service.presign_download.return_value = (
        artifact,
        datetime.now(UTC),
        "https://example.test/download",
    )

    with TestClient(app) as client:
        metadata_response = client.get(
            f"/api/artifacts/{artifact.artifact_id}",
            params={"include_download": "true"},
        )
        assert metadata_response.status_code == 200
        ArtifactMetadataModel.model_validate(metadata_response.json())

        list_response = client.get("/api/executions/moonmind/wf-1/run-1/artifacts")
        assert list_response.status_code == 200
        ArtifactListResponse.model_validate(list_response.json())

        download_response = client.post(
            f"/api/artifacts/{artifact.artifact_id}/presign-download"
        )
        assert download_response.status_code == 200
        PresignDownloadResponse.model_validate(download_response.json())


def test_temporal_artifact_latest_report_contract() -> None:
    app, service = _build_app()
    artifact = _build_artifact()
    artifact.content_type = "text/markdown"
    artifact.metadata_json = {
        "title": "Final implementation report",
        "render_hint": "markdown",
    }
    link = _build_link(link_type="report.primary", label="Final report")
    service.list_for_execution.return_value = [artifact]
    service.get_metadata.return_value = (
        artifact,
        [link],
        False,
        _build_read_policy(artifact),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/executions/moonmind/wf-1/run-1/artifacts",
            params={"link_type": "report.primary", "latest_only": "true"},
        )

    assert response.status_code == 200
    payload = ArtifactListResponse.model_validate(response.json())
    assert len(payload.artifacts) == 1
    assert payload.artifacts[0].links[0].link_type == "report.primary"
    assert payload.artifacts[0].links[0].label == "Final report"
    assert payload.artifacts[0].default_read_ref is not None
    assert (
        payload.artifacts[0].default_read_ref.artifact_id
        == payload.artifacts[0].artifact_id
    )
    service.list_for_execution.assert_awaited_once_with(
        namespace="moonmind",
        workflow_id="wf-1",
        run_id="run-1",
        principal=ANY,
        link_type="report.primary",
        latest_only=True,
    )
