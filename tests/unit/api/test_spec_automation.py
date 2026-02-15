"""Unit tests for Spec Automation API router."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.spec_automation import _get_repository, router
from api_service.auth_providers import get_current_user
from moonmind.config import settings
from moonmind.workflows.speckit_celery import models

ALLOWED_REPOSITORY = "moonladder/moonmind"


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

    def get_skill_execution_metadata(self) -> dict | None:
        metadata = self.get_metadata() or {}
        selected = metadata.get("selectedSkill")
        execution = metadata.get("executionPath")
        used_skills = metadata.get("usedSkills")
        used_fallback = metadata.get("usedFallback")
        shadow_mode = metadata.get("shadowModeRequested")

        if selected is None and self.phase.value.startswith("speckit_"):
            selected = "speckit"
        if execution is None and selected == "speckit":
            execution = "skill"
        if used_skills is None and execution is not None:
            used_skills = execution != "direct_only"
        if used_fallback is None and execution is not None:
            used_fallback = execution == "direct_fallback"

        if (
            selected is None
            and execution is None
            and used_skills is None
            and used_fallback is None
            and shadow_mode is None
        ):
            return None

        return {
            "selectedSkill": selected,
            "executionPath": execution,
            "usedSkills": used_skills,
            "usedFallback": used_fallback,
            "shadowModeRequested": shadow_mode,
        }


class FakeArtifact:
    """Lightweight stand-in for SpecAutomationArtifact."""

    def __init__(
        self,
        *,
        name: str,
        artifact_type: models.SpecAutomationArtifactType,
        storage_path: str,
        run_id: UUID | None = None,
        run: SimpleNamespace | None = None,
    ) -> None:
        self.id = uuid4()
        self.run_id = run_id or uuid4()
        self.name = name
        self.artifact_type = artifact_type
        self.storage_path = storage_path
        self.content_type = "text/plain"
        self.size_bytes = 42
        self.expires_at = datetime.now(UTC)
        self.source_phase = models.SpecAutomationPhase.SPECKIT_PLAN
        self.run = run


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    """Provide a TestClient with repository dependency overridden."""

    app = FastAPI()
    app.include_router(router)
    mock_repo = AsyncMock()
    app.dependency_overrides[_get_repository] = lambda: mock_repo

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="tester@example.com",
        allowed_repositories={ALLOWED_REPOSITORY},
        is_active=True,
        is_superuser=False,
    )

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
        yield test_client, mock_repo, mock_user
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
        repository=ALLOWED_REPOSITORY,
    )


def test_get_run_detail_success(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, _user = client
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
    assert data["phases"][0]["selected_skill"] == "speckit"
    assert data["phases"][0]["execution_path"] == "skill"
    assert data["artifacts"][0]["artifact_id"] == str(artifact.id)
    repo.get_run_detail.assert_awaited_once_with(run_id)


def test_get_run_detail_uses_explicit_skill_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    run = _build_run(run_id)
    task_state = FakeTaskState(
        phase=models.SpecAutomationPhase.SPECKIT_PLAN,
        status=models.SpecAutomationTaskStatus.SUCCEEDED,
        metadata={
            "selectedSkill": "custom-skill",
            "executionPath": "direct_fallback",
            "usedSkills": True,
            "usedFallback": True,
            "shadowModeRequested": False,
        },
    )
    repo.get_run_detail.return_value = (run, [task_state], [])

    response = http_client.get(f"/api/spec-automation/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    phase_payload = payload["phases"][0]
    assert phase_payload["selected_skill"] == "custom-skill"
    assert phase_payload["execution_path"] == "direct_fallback"
    assert phase_payload["used_skills"] is True
    assert phase_payload["used_fallback"] is True
    assert phase_payload["shadow_mode_requested"] is False


def test_get_run_detail_not_found(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    repo.get_run_detail.return_value = None

    response = http_client.get(f"/api/spec-automation/runs/{run_id}")

    assert response.status_code == 404
    repo.get_run_detail.assert_awaited_once_with(run_id)


def test_get_artifact_detail_success(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    run = _build_run(run_id)
    artifact = FakeArtifact(
        name="phase-speckit_plan.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path="runs/123/artifacts/plan.log",
        run_id=run_id,
        run=run,
    )

    repo.get_artifact.return_value = artifact

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact_id"] == str(artifact.id)
    assert (
        payload["download_url"]
        == f"http://testserver/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}/download"
    )
    repo.get_artifact.assert_awaited_once_with(run_id=run_id, artifact_id=artifact.id)


def test_get_artifact_detail_not_found(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    artifact_id = uuid4()
    repo.get_artifact.return_value = None

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact_id}"
    )

    assert response.status_code == 404
    repo.get_artifact.assert_awaited_once_with(run_id=run_id, artifact_id=artifact_id)


def test_get_run_detail_forbidden(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, user = client
    run_id = uuid4()
    user.allowed_repositories = {"other/repo"}
    run = _build_run(run_id)
    run.repository = "forbidden/repo"
    repo.get_run_detail.return_value = (run, [], [])

    response = http_client.get(f"/api/spec-automation/runs/{run_id}")

    assert response.status_code == 403


def test_get_artifact_detail_forbidden(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    http_client, repo, user = client
    run_id = uuid4()
    user.allowed_repositories = {"other/repo"}
    run = _build_run(run_id)
    run.repository = "forbidden/repo"
    artifact = FakeArtifact(
        name="phase-speckit_plan.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path="runs/123/artifacts/plan.log",
        run_id=run_id,
        run=run,
    )

    repo.get_artifact.return_value = artifact

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}"
    )

    assert response.status_code == 403


def test_download_artifact_success(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    run = _build_run(run_id)
    artifact_path = Path("runs/123/artifacts/stdout.log")
    artifact = FakeArtifact(
        name="phase-speckit_plan.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path=str(artifact_path),
        run_id=run_id,
        run=run,
    )

    repo.get_artifact.return_value = artifact

    root = tmp_path / "artifacts"
    target = root / artifact_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("artifact body", encoding="utf-8")

    monkeypatch.setattr(
        settings.spec_workflow, "artifacts_root", str(root), raising=False
    )

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}/download"
    )

    assert response.status_code == 200
    assert response.content == b"artifact body"
    assert (
        'filename="phase-speckit_plan.stdout"'
        in response.headers["content-disposition"]
    )
    assert response.headers["content-type"].startswith("text/plain")


def test_download_artifact_missing_file(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client, repo, _user = client
    run_id = uuid4()
    run = _build_run(run_id)
    artifact = FakeArtifact(
        name="phase-speckit_plan.stdout",
        artifact_type=models.SpecAutomationArtifactType.STDOUT_LOG,
        storage_path="runs/123/artifacts/missing.log",
        run_id=run_id,
        run=run,
    )
    repo.get_artifact.return_value = artifact

    monkeypatch.setattr(
        settings.spec_workflow, "artifacts_root", str(tmp_path), raising=False
    )

    response = http_client.get(
        f"/api/spec-automation/runs/{run_id}/artifacts/{artifact.id}/download"
    )

    assert response.status_code == 404
