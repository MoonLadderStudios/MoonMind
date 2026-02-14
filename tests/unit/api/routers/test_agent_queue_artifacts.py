"""Unit tests for agent queue artifact API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.agent_queue import (
    _get_service,
    _require_worker_auth,
    _WorkerRequestAuth,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue.repositories import (
    AgentArtifactJobMismatchError,
    AgentArtifactNotFoundError,
    AgentJobNotFoundError,
)
from moonmind.workflows.agent_queue.service import AgentQueueValidationError


def _build_artifact(job_id=None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        job_id=job_id or uuid4(),
        name="logs/output.log",
        content_type="text/plain",
        size_bytes=5,
        digest="sha256:abc",
        storage_path="job/logs/output.log",
        created_at=now,
    )


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock]]:
    """Provide a TestClient with queue service dependency overridden."""

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[_require_worker_auth] = lambda: _WorkerRequestAuth(
        auth_source="worker_token",
        worker_id="worker-1",
        allowed_repositories=(),
        allowed_job_types=(),
        capabilities=("codex",),
    )

    mock_user = SimpleNamespace(id=uuid4(), email="queue@example.com", is_active=True)

    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user

    with TestClient(app) as test_client:
        yield test_client, mock_service
    app.dependency_overrides.clear()


def test_upload_artifact_success(client: tuple[TestClient, AsyncMock]) -> None:
    """Upload endpoint should accept multipart payload and return metadata."""

    test_client, service = client
    job_id = uuid4()
    artifact = _build_artifact(job_id=job_id)
    service.upload_artifact.return_value = artifact

    response = test_client.post(
        f"/api/queue/jobs/{job_id}/artifacts/upload",
        files={"file": ("output.log", b"hello", "text/plain")},
        data={
            "workerId": "worker-1",
            "name": "logs/output.log",
            "contentType": "text/plain",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(artifact.id)
    assert body["jobId"] == str(job_id)
    assert body["name"] == "logs/output.log"
    service.upload_artifact.assert_awaited_once()
    assert service.upload_artifact.await_args.kwargs["worker_id"] == "worker-1"


def test_list_artifacts_success(client: tuple[TestClient, AsyncMock]) -> None:
    """List endpoint should return artifact collection."""

    test_client, service = client
    job_id = uuid4()
    artifact = _build_artifact(job_id=job_id)
    service.list_artifacts.return_value = [artifact]

    response = test_client.get(f"/api/queue/jobs/{job_id}/artifacts")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["jobId"] == str(job_id)


def test_list_artifacts_job_not_found_maps_404(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Listing artifacts for missing jobs should map to HTTP 404."""

    test_client, service = client
    service.list_artifacts.side_effect = AgentJobNotFoundError(uuid4())

    response = test_client.get(f"/api/queue/jobs/{uuid4()}/artifacts")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_download_artifact_success(client: tuple[TestClient, AsyncMock]) -> None:
    """Download endpoint should stream artifact bytes."""

    test_client, service = client
    job_id = uuid4()
    artifact = _build_artifact(job_id=job_id)
    with NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"artifact-bytes")
        tmp_path = Path(tmp.name)

    service.get_artifact_download.return_value = SimpleNamespace(
        artifact=artifact,
        file_path=tmp_path,
    )

    try:
        response = test_client.get(
            f"/api/queue/jobs/{job_id}/artifacts/{artifact.id}/download"
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert response.content == b"artifact-bytes"


def test_upload_artifact_too_large_maps_413(
    client: tuple[TestClient, AsyncMock]
) -> None:
    """Size validation errors should map to HTTP 413."""

    test_client, service = client
    service.upload_artifact.side_effect = AgentQueueValidationError(
        "artifact exceeds max bytes (1024)"
    )

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/artifacts/upload",
        files={"file": ("big.bin", b"too-big", "application/octet-stream")},
        data={"workerId": "worker-1", "name": "logs/big.bin"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "artifact_too_large"


def test_upload_artifact_limits_memory_before_service_dispatch(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router should reject oversized uploads before invoking service layer."""

    test_client, service = client
    monkeypatch.setattr(
        settings.spec_workflow,
        "agent_job_artifact_max_bytes",
        4,
    )

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/artifacts/upload",
        files={"file": ("big.bin", b"12345", "application/octet-stream")},
        data={"workerId": "worker-1", "name": "logs/big.bin"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "artifact_too_large"
    service.upload_artifact.assert_not_awaited()


def test_download_artifact_job_mismatch_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Artifact-job mismatches should map to HTTP 409."""

    test_client, service = client
    job_id = uuid4()
    artifact_id = uuid4()
    service.get_artifact_download.side_effect = AgentArtifactJobMismatchError(
        artifact_id,
        job_id,
    )

    response = test_client.get(
        f"/api/queue/jobs/{job_id}/artifacts/{artifact_id}/download"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "artifact_job_mismatch"


def test_download_artifact_not_found_maps_404(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Unknown artifact ids should map to HTTP 404."""

    test_client, service = client
    artifact_id = uuid4()
    service.get_artifact_download.side_effect = AgentArtifactNotFoundError(artifact_id)

    response = test_client.get(
        f"/api/queue/jobs/{uuid4()}/artifacts/{artifact_id}/download"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "artifact_not_found"


def test_upload_artifact_worker_mismatch_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Upload endpoint should enforce token worker identity matching."""

    test_client, service = client
    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/artifacts/upload",
        files={"file": ("output.log", b"hello", "text/plain")},
        data={"workerId": "worker-2", "name": "logs/output.log"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "worker_not_authorized"
    service.upload_artifact.assert_not_awaited()
