"""Unit tests for Spec Automation API router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.spec_automation import router, _get_repository
from moonmind.workflows.speckit_celery import models


class FakeTaskState:
    """Lightweight stand-in for SpecAutomationTaskState."""

    def __init__(
        self,
        *,
        phase: models.SpecAutomationPhase,
        status: models.SpecAutomationTaskStatus,
        attempt: int = 1,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.id = uuid4()
        self.phase = phase
        self.status = status
        self.attempt = attempt
        self.started_at = started_at
        self.completed_at = completed_at
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self._metadata = metadata
        self.created_at = started_at or datetime.now(UTC)

    def get_metadata(self) -> dict | None:
        return self._metadata


class FakeArtifact:
    """Lightweight stand-in for SpecAutomationArtifact."""

    def __init__(
        self,
        *,
        name: str,
        artifact_type: models.SpecAutomationArtifactType,
        storage_path: str,
    ) -> None:
        self.id = uuid4()
        self.run_id = uuid4()
        self.name = name
        self.artifact_type = artifact_type
        self.storage_path = storage_path
        self.content_type = "text/plain"
        self.size_bytes = 42
        self.expires_at = datetime.now(UTC)
        self.source_phase = models.SpecAutomationPhase.SPECKIT_PLAN


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock]]:
    """Provide a TestClient with repository dependency overridden."""

    app = FastAPI()
    app.include_router(router)
    mock_repo = AsyncMock()
    app.dependency_overrides[_get_repository] = lambda: mock_repo
    with TestClient(app) as test_client:
        yield test_client, mock_repo
    app.dependency_overrides.clear()


def _build_run(run_id: UUID | None = None) -> SimpleNamespace:
    run_id = run_id or uuid4()
    return SimpleNamespace(
        id=run_id,
        status=models.SpecAutomationRunStatus.SUCCEEDED,
        branch_name="speckit/branch",
        pull_request_url="https://example.com/pr/1",
        result_summary="Spec automation completed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


def test_get_run_detail_success(client: tuple[TestClient, AsyncMock]) -> None:
    http_client, repo = client
    run_id = uuid4()
    run = _build_run(run_id)
    task_state = FakeTaskState(
        phase=models.SpecAutomationPhase.SPECKIT_SPECIFY,
        status=models.SpecAutomationTaskStatus.SUCCEEDED,
        metadata={"branch": "speckit/branch"},
    )
    artifact = FakeArtifact(
        name="phase-speckit_specify.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path="runs/123/artifacts/stdout.log",
    )
    artifact.run_id = run_id

    repo.get_run_detail.return_value = (run, [task_state], [artifact])

    response = http_client.get(f"/api/spec-automation/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == str(run_id)
    assert data["status"] == models.SpecAutomationRunStatus.SUCCEEDED.value
    assert data["phases"][0]["phase"] == task_state.phase.value
    assert data["phases"][0]["metadata"] == {"branch": "speckit/branch"}
    assert data["artifacts"][0]["artifact_id"] == str(artifact.id)
    repo.get_run_detail.assert_awaited_once_with(run_id)


def test_get_run_detail_not_found(client: tuple[TestClient, AsyncMock]) -> None:
    http_client, repo = client
    run_id = uuid4()
    repo.get_run_detail.return_value = None

    response = http_client.get(f"/api/spec-automation/runs/{run_id}")

    assert response.status_code == 404
    repo.get_run_detail.assert_awaited_once_with(run_id)


def test_get_artifact_detail_success(client: tuple[TestClient, AsyncMock]) -> None:
    http_client, repo = client
    run_id = uuid4()
    artifact = FakeArtifact(
        name="phase-speckit_plan.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path="runs/123/artifacts/plan.log",
    )
    artifact.run_id = run_id

    repo.get_artifact.return_value = artifact

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact_id"] == str(artifact.id)
    assert payload["download_url"] == artifact.storage_path
    repo.get_artifact.assert_awaited_once_with(
        run_id=run_id, artifact_id=artifact.id
    )


def test_get_artifact_detail_not_found(client: tuple[TestClient, AsyncMock]) -> None:
    http_client, repo = client
    run_id = uuid4()
    artifact_id = uuid4()
    repo.get_artifact.return_value = None

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact_id}"
    )

    assert response.status_code == 404
    repo.get_artifact.assert_awaited_once_with(
        run_id=run_id, artifact_id=artifact_id
    )
