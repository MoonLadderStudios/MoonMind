"""Unit tests for Temporal artifact API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    router,
)
from api_service.auth_providers import get_instance_access_optional
from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError

def _build_artifact() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        artifact_id=f"art_{uuid4().hex[:26].upper()}",
        created_at=now,
        created_by_principal="user-1",
        content_type="text/plain",
        size_bytes=5,
        sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        storage_backend=db_models.TemporalArtifactStorageBackend.LOCAL_FS,
        storage_key="moonmind/artifacts/2026/03/05/demo",
        encryption=db_models.TemporalArtifactEncryption.NONE,
        status=db_models.TemporalArtifactStatus.COMPLETE,
        retention_class=db_models.TemporalArtifactRetentionClass.STANDARD,
        expires_at=None,
        redaction_level=db_models.TemporalArtifactRedactionLevel.NONE,
        metadata_json={"source": "test"},
    )

def _build_link(artifact_id: str) -> SimpleNamespace:
    _ = artifact_id
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="wf-1",
        run_id="run-1",
        link_type="output.primary",
        label="Final output",
        created_at=datetime.now(UTC),
        created_by_activity_type="artifact.write_complete",
        created_by_worker="worker-1",
    )

def _build_upload(artifact_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        mode="single_put",
        upload_url=f"/api/artifacts/{artifact_id}/content",
        upload_id=None,
        expires_at=datetime.now(UTC),
        max_size_bytes=10 * 1024 * 1024,
        required_headers={},
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

def _override_user_dependencies(app: FastAPI) -> None:
    mock_user = SimpleNamespace(
        id=uuid4(), email="artifact@example.com", is_active=True
    )
    app.dependency_overrides[get_instance_access_optional] = lambda: mock_user

def _client_with_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: mock_service
    _override_user_dependencies(app)

    with TestClient(app) as test_client:
        yield test_client, mock_service
    app.dependency_overrides.clear()


def test_artifact_collection_returns_compact_safe_rows() -> None:
    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        link = _build_link(artifact.artifact_id)
        service.list_authorized_collection.return_value = ([(artifact, [link])], True)

        response = test_client.get("/api/artifacts/collection?category=artifacts")

        assert response.status_code == 200
        body = response.json()
        assert body["has_more"] is True
        assert "total" not in body
        row = body["items"][0]
        assert row["artifact_id"] == artifact.artifact_id
        assert row["workflow_id"] == "wf-1"
        assert "view_url" not in row
        assert row["download_url"].endswith("/download")
        assert "storage_key" not in row
        assert "created_by_principal" not in row
        assert "sha256" not in row
        service.list_authorized_collection.assert_awaited_once()

def test_create_artifact_returns_upload_descriptor() -> None:
    """Create endpoint should return ArtifactRef + upload details."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        upload = _build_upload(artifact.artifact_id)
        service.create.return_value = (artifact, upload)

        response = test_client.post(
            "/api/artifacts",
            json={
                "content_type": "text/plain",
                "link": {
                    "namespace": "moonmind",
                    "workflow_id": "wf-1",
                    "run_id": "run-1",
                    "link_type": "output.primary",
                },
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["artifact_ref"]["artifact_id"] == artifact.artifact_id
        assert (
            body["upload"]["upload_url"]
            == f"http://testserver/api/artifacts/{artifact.artifact_id}/content"
        )

def test_create_artifact_returns_attachment_upload_started_diagnostic() -> None:
    """MM-375: create response exposes target-aware upload-start diagnostics."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        artifact.content_type = "image/png"
        artifact.size_bytes = 42
        upload = _build_upload(artifact.artifact_id)
        service.create.return_value = (artifact, upload)

        response = test_client.post(
            "/api/artifacts",
            json={
                "content_type": "image/png",
                "size_bytes": 42,
                "metadata": {
                    "targetKind": "objective",
                    "filename": "objective.png",
                },
            },
        )

        assert response.status_code == 201
        event = response.json()["diagnostics"]["events"][0]
        assert event == {
            "event": "attachment_upload_started",
            "status": "started",
            "targetKind": "objective",
            "artifactId": artifact.artifact_id,
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 42,
        }

def test_upload_content_returns_attachment_upload_completed_diagnostic() -> None:
    """MM-375: upload completion response exposes target-aware diagnostics."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        artifact.content_type = "image/png"
        artifact.size_bytes = 42
        artifact.metadata_json = {
            "targetKind": "step",
            "stepRef": "review-step",
            "filename": "step.png",
        }
        service.write_complete.return_value = artifact

        response = test_client.put(
            f"/api/artifacts/{artifact.artifact_id}/content",
            content=b"image-bytes",
            headers={"content-type": "image/png"},
        )

        assert response.status_code == 200
        event = response.json()["diagnostics"]["events"][0]
        assert event == {
            "event": "attachment_upload_completed",
            "status": "completed",
            "targetKind": "step",
            "stepRef": "review-step",
            "artifactId": artifact.artifact_id,
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 42,
        }

def test_upload_content_maps_validation_to_413() -> None:
    """Upload endpoint should map max-byte validation errors to HTTP 413."""

    for test_client, service in _client_with_service():
        service.write_complete.side_effect = TemporalArtifactValidationError(
            "artifact exceeds max bytes (4)"
        )

        response = test_client.put(
            f"/api/artifacts/{_build_artifact().artifact_id}/content",
            content=b"too-long",
            headers={"content-type": "application/octet-stream"},
        )

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "artifact_too_large"

def test_presign_upload_part_returns_descriptor() -> None:
    """Multipart presign endpoint should surface URL, headers, and part number."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        service.presign_upload_part.return_value = SimpleNamespace(
            part_number=3,
            url=f"https://minio.local/{artifact.artifact_id}?partNumber=3",
            expires_at=datetime.now(UTC),
            required_headers={"x-amz-server-side-encryption": "AES256"},
        )

        response = test_client.post(
            f"/api/artifacts/{artifact.artifact_id}/presign-upload-part",
            json={"part_number": 3},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["part_number"] == 3
        assert "partNumber=3" in body["url"]

def test_get_metadata_include_download() -> None:
    """Metadata endpoint should include download hints when requested."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        link = _build_link(artifact.artifact_id)
        service.get_metadata.return_value = (
            artifact,
            [link],
            False,
            _build_read_policy(artifact),
        )
        service.presign_download.return_value = (
            artifact,
            datetime.now(UTC),
            f"/api/artifacts/{artifact.artifact_id}/download",
        )

        response = test_client.get(
            f"/api/artifacts/{artifact.artifact_id}",
            params={"include_download": "true"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["artifact_id"] == artifact.artifact_id
        assert body["download_url"].endswith(
            f"/api/artifacts/{artifact.artifact_id}/download"
        )

def test_get_metadata_exposes_preview_and_raw_access_policy_fields() -> None:
    """Metadata endpoint should surface preview/default-read policy fields."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        link = _build_link(artifact.artifact_id)
        preview_ref = SimpleNamespace(
            artifact_ref_v=1,
            artifact_id=f"{artifact.artifact_id}_preview",
            sha256=artifact.sha256,
            size_bytes=128,
            content_type="text/plain",
            encryption=artifact.encryption.value,
        )
        default_ref = SimpleNamespace(
            artifact_ref_v=1,
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            content_type=artifact.content_type,
            encryption=artifact.encryption.value,
        )
        service.get_metadata.return_value = (
            artifact,
            [link],
            False,
            SimpleNamespace(
                raw_access_allowed=False,
                preview_artifact_ref=preview_ref,
                default_read_ref=default_ref,
            ),
        )

        response = test_client.get(f"/api/artifacts/{artifact.artifact_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["raw_access_allowed"] is False
        assert body["preview_artifact_ref"]["artifact_id"] == preview_ref.artifact_id
        assert body["default_read_ref"]["artifact_id"] == artifact.artifact_id

def test_list_execution_artifacts_returns_collection() -> None:
    """Execution listing endpoint should return serialized artifact metadata."""

    for test_client, service in _client_with_service():
        artifact = _build_artifact()
        link = _build_link(artifact.artifact_id)
        service.list_for_execution.return_value = [artifact]
        service.get_metadata.return_value = (
            artifact,
            [link],
            True,
            _build_read_policy(artifact),
        )

        response = test_client.get("/api/executions/moonmind/wf-1/run-1/artifacts")

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["artifacts"]) == 1
        assert payload["artifacts"][0]["artifact_id"] == artifact.artifact_id
        assert payload["artifacts"][0]["links"][0]["label"] == "Final output"


def test_download_previews_inert_artifacts_inline_only_when_requested(tmp_path) -> None:
    """MoonLadderStudios/MoonMind#4870: Open previews; Download stays an attachment."""

    for test_client, service in _client_with_service():
        text_artifact = _build_artifact()
        text_path = tmp_path / "evidence.txt"
        text_path.write_text("hello", encoding="utf-8")
        html_artifact = _build_artifact()
        html_artifact.content_type = "text/html"
        html_path = tmp_path / "page.html"
        html_path.write_text("<script>alert(1)</script>", encoding="utf-8")
        paths = {
            text_artifact.artifact_id: (text_artifact, text_path),
            html_artifact.artifact_id: (html_artifact, html_path),
        }
        def read_path(*, artifact_id, principal, recovery_principal=None):
            # This UUID fixture bypasses local admission, so it must not gain
            # the protected operator recovery identity as a side effect.
            assert recovery_principal is None
            return paths[artifact_id]

        service.read_path.side_effect = read_path
        text_url = f"/api/artifacts/{text_artifact.artifact_id}/download"
        html_url = f"/api/artifacts/{html_artifact.artifact_id}/download"

        download = test_client.get(text_url)
        preview = test_client.get(text_url, params={"inline": "true"})
        active = test_client.get(html_url, params={"inline": "true"})

        assert download.headers["content-disposition"].startswith("attachment;")
        assert preview.status_code == 200
        assert preview.text == "hello"
        assert preview.headers["content-disposition"].startswith("inline;")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.headers["content-security-policy"].startswith("sandbox")
        # Active content never renders on the operator's origin.
        assert active.headers["content-disposition"].startswith("attachment;")
        assert active.headers["content-security-policy"].startswith("sandbox")
