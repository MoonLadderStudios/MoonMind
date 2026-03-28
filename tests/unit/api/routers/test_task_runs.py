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
    return User(id=uuid4(), email="test@example.com")


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
    app.dependency_overrides[get_current_user] = lambda: lambda: test_user
    app.dependency_overrides[_require_worker_auth] = lambda: test_worker_auth

    with TestClient(app) as test_client:
        yield test_client, db_mock
    
    app.dependency_overrides.clear()


def test_get_live_session_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client

    # Mock DB empty
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = None
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.get(f"/api/task-runs/{uuid4()}/live-session")

    assert response.status_code == 404
    assert db_mock.execute.called


def test_get_live_session_returns_200_when_present(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    session_mock = TaskRunLiveSession(
        id=uuid4(),
        task_run_id=task_run_id,
        status=AgentJobLiveSessionStatus.STARTING,
        provider=AgentJobLiveSessionProvider.NONE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = session_mock
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.get(f"/api/task-runs/{task_run_id}/live-session")

    assert response.status_code == 200
    assert response.json()["session"]["taskRunId"] == str(task_run_id)


def test_get_live_session_worker_returns_200_and_encrypted_fields(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    session_mock = TaskRunLiveSession(
        id=uuid4(),
        task_run_id=task_run_id,
        status=AgentJobLiveSessionStatus.STARTING,
        provider=AgentJobLiveSessionProvider.NONE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session_mock.attach_rw_encrypted = "secret_rw"

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = session_mock
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.get(f"/api/task-runs/{task_run_id}/live-session/worker")

    assert response.status_code == 200
    assert response.json()["session"]["attachRw"] == "secret_rw"


def test_report_live_session_creates_new(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    # DB initially empty
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = None
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock
    db_mock.add = MagicMock()

    async def mock_refresh(obj):
        from uuid import uuid4
        from datetime import datetime, UTC
        obj.id = uuid4()
        obj.created_at = datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)
        
    db_mock.refresh.side_effect = mock_refresh

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/report",
        json={
            "workerId": "test-worker-123",
            "provider": "none",
            "status": "starting"
        }
    )

    assert response.status_code == 200
    assert db_mock.add.called
    assert db_mock.commit.called


def test_report_live_session_rejects_missing_provider_on_create(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    # DB initially empty
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = None
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/report",
        json={
            "workerId": "test-worker-123",
            "status": "starting"
        }
    )

    assert response.status_code == 400


def test_task_run_live_session_enums_bind_postgres_values() -> None:
    provider_type = TaskRunLiveSession.__table__.c.provider.type
    status_type = TaskRunLiveSession.__table__.c.status.type
    dialect = postgresql.dialect()

    provider_processor = provider_type.bind_processor(dialect)
    status_processor = status_type.bind_processor(dialect)

    assert provider_processor is not None
    assert status_processor is not None
    assert provider_processor(AgentJobLiveSessionProvider.NONE) == "none"
    assert provider_processor("none") == "none"
    assert status_processor(AgentJobLiveSessionStatus.READY) == "ready"
    assert status_processor("ready") == "ready"


def test_heartbeat_returns_200(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    session_mock = TaskRunLiveSession(
        id=uuid4(),
        task_run_id=task_run_id,
        worker_id="test-worker-123",
        status=AgentJobLiveSessionStatus.STARTING,
        provider=AgentJobLiveSessionProvider.NONE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = session_mock
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/heartbeat",
        json={
            "workerId": "test-worker-123",
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert db_mock.commit.called


def test_heartbeat_rejects_worker_id_mismatch(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client
    task_run_id = uuid4()

    session_mock = TaskRunLiveSession(
        id=uuid4(),
        task_run_id=task_run_id,
        worker_id="different-worker",
        status=AgentJobLiveSessionStatus.STARTING,
        provider=AgentJobLiveSessionProvider.NONE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = session_mock
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/heartbeat",
        json={
            "workerId": "test-worker-123",
        }
    )

    assert response.status_code == 403


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
        with patch("api_service.api.routers.task_runs.Path.exists", return_value=False):
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
        with patch("api_service.api.routers.task_runs.Path.exists", return_value=True):
            with patch("api_service.api.routers.task_runs.Path.is_file", return_value=True):
                with patch("api_service.api.routers.task_runs.FileResponse") as mock_file_response:
                    mock_file_response.return_value = {"mock": "response"}
                    response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")
                    
    # The FastAPI test client doesn't automatically unwrap the manual patched FileResponse 
    # if it's not a real response object. Since FileResponse inherits from Response, FastAPI 
    # evaluates it. If we mock FileResponse itself, it returns a dict causing a generic error
    # Let's verify the mock was called successfully instead.
    assert mock_file_response.called
    assert mock_file_response.call_args[1]["media_type"] == "text/plain"


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

