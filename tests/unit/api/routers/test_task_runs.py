"""Unit tests for task-run observability API router."""

from __future__ import annotations

from typing import Iterator
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_runs import router
from api_service.auth_providers import get_current_user
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth
from api_service.db.models import User


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

    app.dependency_overrides[get_current_user()] = lambda: test_user
    app.dependency_overrides[_require_worker_auth] = lambda: test_worker_auth

    with TestClient(app) as test_client:
        yield test_client, db_mock

    app.dependency_overrides.clear()


# Legacy web_ro session unit tests removed in Phase 6.


# ---------------------------------------------------------------------------
# Observability summary
# ---------------------------------------------------------------------------

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

    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"runId": str(run_id), "status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = True

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{run_id}/observability-summary")

    assert response.status_code == 200
    body = response.json()["summary"]
    assert body["runId"] == str(run_id)
    assert body["status"] == "running"


def test_get_observability_summary_includes_live_stream_fields_for_active_run(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Active run with live_stream_capable=True must return supportsLiveStreaming=True."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = True

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/observability-summary")

    body = response.json()["summary"]
    assert body["supportsLiveStreaming"] is True
    assert body["liveStreamStatus"] == "available"


def test_get_observability_summary_live_stream_ended_for_terminal_run(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Completed run must return supportsLiveStreaming=False and liveStreamStatus=ended."""
    test_client, _ = client
    for terminal_status in ("completed", "failed", "canceled", "timed_out"):
        mock_record = MagicMock()
        mock_record.model_dump.return_value = {"status": terminal_status}
        mock_record.status = terminal_status
        mock_record.live_stream_capable = True  # capable flag ignored for terminal runs

        with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
            response = test_client.get(f"/api/task-runs/{uuid4()}/observability-summary")

        body = response.json()["summary"]
        assert body["supportsLiveStreaming"] is False, f"expected False for status={terminal_status}"
        assert body["liveStreamStatus"] == "ended", f"expected ended for status={terminal_status}"


def test_get_observability_summary_live_stream_unavailable_when_not_capable(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Active run with live_stream_capable=False must return supportsLiveStreaming=False."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = False

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/observability-summary")

    body = response.json()["summary"]
    assert body["supportsLiveStreaming"] is False
    assert body["liveStreamStatus"] == "unavailable"


# ---------------------------------------------------------------------------
# Log artifact retrieval (stdout / stderr)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Merged-tail (artifact-backed and synthesized)
# ---------------------------------------------------------------------------

def test_stream_task_run_log_merged_synthesized_from_stdout_stderr(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """When merged_log_artifact_ref is absent, the endpoint synthesizes from stdout+stderr."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = "run/stdout.log"
    mock_record.stderr_artifact_ref = "run/stderr.log"

    stdout_content = b"hello from stdout"
    stderr_content = b"warning from stderr"

    def fake_is_file(self: object) -> bool:
        return True

    def fake_is_relative_to(self: object, other: object) -> bool:
        return True

    def fake_read_bytes(self: object) -> bytes:
        path_str = str(self)
        if "stdout" in path_str:
            return stdout_content
        return stderr_content

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", fake_is_file):
            with patch("pathlib.Path.is_relative_to", fake_is_relative_to):
                with patch("pathlib.Path.read_bytes", fake_read_bytes):
                    response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-synthesized"] == "true"
    body = response.content
    assert b"stdout" in body
    assert b"stderr" in body
    assert b"hello from stdout" in body
    assert b"warning from stderr" in body


def test_stream_task_run_log_merged_returns_404_when_both_artifacts_absent(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """When merged_log_artifact_ref and both stdout/stderr refs are absent, must return 404."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 404
    assert "no stdout/stderr artifacts" in response.json()["detail"].lower()


def test_stream_task_run_log_merged_uses_prebuilt_artifact_when_available(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """When merged_log_artifact_ref is set, it is served directly as a FileResponse."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = "run/merged.log"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.is_relative_to", return_value=True):
                with patch("api_service.api.routers.task_runs.FileResponse") as mock_file_response:
                    from fastapi.responses import Response
                    mock_file_response.return_value = Response(content=b"merged content", media_type="text/plain")
                    response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert mock_file_response.called
    assert response.status_code == 200
    # Pre-built artifacts should NOT have the synthesized header
    assert "x-merged-synthesized" not in response.headers


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def test_get_task_run_diagnostics_returns_404_when_record_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """404 when the run record itself does not exist."""
    test_client, _ = client
    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=None):
        response = test_client.get(f"/api/task-runs/{uuid4()}/diagnostics")
    assert response.status_code == 404
    assert "artifact not found" in response.json()["detail"].lower()


def test_get_task_run_diagnostics_returns_404_when_diagnostics_ref_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """404 when the run record exists but diagnostics_ref is None (partial run)."""
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

# ---------------------------------------------------------------------------
# SSE Live Streaming (Phase 3)
# ---------------------------------------------------------------------------

# Tests removed due to an AnyIO test transport deadlock with fake streaming generators
