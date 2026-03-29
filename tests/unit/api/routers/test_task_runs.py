"""Unit tests for task-run live session API router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterator
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from api_service.api.routers.task_runs import router
from api_service.db.base import get_async_session
from api_service.auth_providers import get_current_user
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth
from api_service.db.models import AgentJobLiveSessionStatus, AgentJobLiveSessionProvider, TaskRunLiveSession, User


@pytest.fixture
def test_user() -> User:
    return User(id=uuid4(), email="test@example.com", is_superuser=True)


@pytest.fixture
def test_worker_auth() -> _WorkerRequestAuth:
    return _WorkerRequestAuth(
        auth_source="worker_token",
        worker_id="test-worker-123",
        allowed_repositories=(),
        allowed_job_types=(),
        capabilities=(),
    )


@pytest.fixture
def client(test_user: User, test_worker_auth: _WorkerRequestAuth) -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    db_mock = AsyncMock()
    
    app.dependency_overrides[get_async_session] = lambda: db_mock
    app.dependency_overrides[get_current_user()] = lambda: test_user
    app.dependency_overrides[_require_worker_auth] = lambda: test_worker_auth

    with TestClient(app) as test_client:
        yield test_client, db_mock
    
    app.dependency_overrides.clear()


# Legacy web_ro session unit tests removed in Phase 6.


from unittest.mock import patch

def test_get_observability_summary_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=None):
        response = test_client.get(f"/api/task-runs/{uuid4()}/observability-summary")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_observability_summary_returns_200(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    run_id = uuid4()
    
    # Mocking the dictionary returned by record.model_dump()
    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"runId": str(run_id), "status": "running"}

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{run_id}/observability-summary")
    
    assert response.status_code == 200
    assert response.json()["summary"]["runId"] == str(run_id)
    assert response.json()["summary"]["status"] == "running"


def test_stream_task_run_log_returns_400_for_invalid_stream(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    response = test_client.get(f"/api/task-runs/{uuid4()}/logs/invalid_stream")
    assert response.status_code == 400


def test_stream_task_run_log_returns_404_when_record_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=None):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")
    assert response.status_code == 404


def test_stream_task_run_log_returns_404_when_artifact_ref_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.stdout_artifact_ref = None
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")
        
    assert response.status_code == 404
    assert "artifact not found" in response.json()["detail"].lower()


def test_stream_task_run_log_returns_404_when_file_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.stdout_artifact_ref = "test/stdout.log"
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", return_value=False):
            response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")
            
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"].lower()


def test_stream_task_run_log_returns_file_response(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.stdout_artifact_ref = "test/stdout.log"
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.is_relative_to", return_value=True):
                with patch("api_service.api.routers.task_runs.FileResponse") as mock_file_response:
                    from fastapi.responses import Response
                    mock_file_response.return_value = Response(content=b"mock_log_data", media_type="text/plain")
                    response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")
                    
    assert mock_file_response.called
    assert mock_file_response.call_args[1]["media_type"] == "text/plain"
    assert response.status_code == 200
    assert response.content == b"mock_log_data"


def test_get_task_run_diagnostics_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.diagnostics_ref = None
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/diagnostics")
        
    assert response.status_code == 404
    assert "artifact not found" in response.json()["detail"].lower()


def test_get_task_run_diagnostics_returns_404_when_file_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.diagnostics_ref = "test/diagnostics.json"
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", return_value=False):
            response = test_client.get(f"/api/task-runs/{uuid4()}/diagnostics")
            
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"].lower()


def test_get_task_run_diagnostics_returns_file_response(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.diagnostics_ref = "test/diagnostics.json"
    
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.is_relative_to", return_value=True):
                with patch("api_service.api.routers.task_runs.FileResponse") as mock_file_response:
                    from fastapi.responses import Response
                    mock_file_response.return_value = Response(content=b'{"mock":"diag"}', media_type="application/json")
                    response = test_client.get(f"/api/task-runs/{uuid4()}/diagnostics")
                    
    assert mock_file_response.called
    assert mock_file_response.call_args[1]["media_type"] == "application/json"
    assert response.status_code == 200
    assert response.content == b'{"mock":"diag"}'

