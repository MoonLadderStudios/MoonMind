"""Unit tests for task-run observability API router."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Iterator
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_runs import router
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.auth_providers import get_current_user
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth
from api_service.db import models as db_models
from api_service.db.models import User
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord


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
    artifact_service = AsyncMock()

    app.dependency_overrides[get_current_user()] = lambda: test_user
    app.dependency_overrides[_require_worker_auth] = lambda: test_worker_auth
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: artifact_service

    with TestClient(app) as test_client:
        yield test_client, artifact_service

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


def test_get_observability_summary_allows_owner_access() -> None:
    owner_id = uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=owner_id,
        email="owner@example.com",
        is_superuser=False,
    )

    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = True
    mock_record.workflow_id = "mm:wf-1"

    with TestClient(app) as test_client:
        with patch(
            "api_service.api.routers.task_runs.ManagedRunStore.load",
            return_value=mock_record,
        ):
            with patch(
                "api_service.api.routers.task_runs._load_execution_owner_binding",
                new=AsyncMock(return_value=("user", str(owner_id))),
            ):
                response = test_client.get(
                    f"/api/task-runs/{uuid4()}/observability-summary"
                )

    assert response.status_code == 200
    assert response.json()["summary"]["supportsLiveStreaming"] is True


def test_get_observability_summary_forbids_cross_owner_access() -> None:
    owner_id = uuid4()
    other_id = uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=other_id,
        email="other@example.com",
        is_superuser=False,
    )

    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = True
    mock_record.workflow_id = "mm:wf-1"

    with TestClient(app) as test_client:
        with patch(
            "api_service.api.routers.task_runs.ManagedRunStore.load",
            return_value=mock_record,
        ):
            with patch(
                "api_service.api.routers.task_runs._load_execution_owner_binding",
                new=AsyncMock(return_value=("user", str(owner_id))),
            ):
                response = test_client.get(
                    f"/api/task-runs/{uuid4()}/observability-summary"
                )

    assert response.status_code == 403


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


@pytest.mark.parametrize(
    ("env_root_name", "artifact_root_name"),
    [
        ("agent_jobs", "artifacts"),
        ("artifacts", "."),
    ],
)
def test_stream_task_run_log_uses_supported_artifact_root_layouts(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    env_root_name: str,
    artifact_root_name: str,
) -> None:
    test_client, _ = client
    env_root = tmp_path / env_root_name
    artifacts_root = env_root if artifact_root_name == "." else env_root / artifact_root_name
    run_dir = artifacts_root / "run"
    run_dir.mkdir(parents=True)
    expected_content = "stdout from durable artifact\n"
    (run_dir / "stdout.log").write_text(expected_content, encoding="utf-8")
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(env_root))

    mock_record = MagicMock()
    mock_record.stdout_artifact_ref = "run/stdout.log"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stdout")

    assert response.status_code == 200
    assert response.text == expected_content


# ---------------------------------------------------------------------------
# Merged-tail (artifact-backed and synthesized)
# ---------------------------------------------------------------------------

def test_stream_task_run_log_merged_synthesized_from_spool(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    """When spool metadata exists, merged synthesis preserves spool ordering across streams."""
    test_client, _ = client
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    spool_path = workspace_path / "live_streams.spool"
    spool_path.write_text(
        "\n".join(
            [
                json.dumps({"sequence": 1, "stream": "stdout", "text": "hello from stdout\n", "timestamp": "2026-03-31T00:00:00Z"}),
                json.dumps({"sequence": 2, "stream": "stderr", "text": "warning from stderr\n", "timestamp": "2026-03-31T00:00:01Z"}),
                json.dumps({"sequence": 3, "stream": "stdout", "text": "goodbye from stdout\n", "timestamp": "2026-03-31T00:00:02Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None
    mock_record.workspace_path = str(workspace_path)

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-synthesized"] == "true"
    assert response.headers["x-merged-order-source"] == "spool"
    body = response.text
    assert body.index("--- stdout ---") < body.index("hello from stdout")
    assert body.index("--- stderr ---") > body.index("hello from stdout")
    assert body.index("warning from stderr") > body.index("--- stderr ---")
    assert body.index("goodbye from stdout") > body.index("warning from stderr")


def test_stream_task_run_log_merged_falls_back_when_spool_metadata_missing(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.log").write_text("hello from stdout\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("warning from stderr\n", encoding="utf-8")

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = "run/stdout.log"
    mock_record.stderr_artifact_ref = "run/stderr.log"
    mock_record.workspace_path = str(tmp_path / "workspace-without-spool")

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-order-source"] == "artifact-fallback"
    assert "[merged-order unavailable: spool metadata missing]" in response.text


def test_stream_task_run_log_merged_fallback_includes_system_annotations(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run"
    run_dir.mkdir(parents=True)

    (run_dir / "stdout.log").write_text("hello from stdout\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("warning from stderr\n", encoding="utf-8")
    diagnostics = {
        "annotations": [
            {
                "sequence": 20,
                "text": "Supervisor: run classified as completed.",
                "annotation_type": "run_classified_completed",
            },
            {
                "sequence": 10,
                "text": "Supervisor: managed run started.",
                "annotation_type": "run_started",
            },
        ]
    }
    (run_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics),
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = "run/stdout.log"
    mock_record.stderr_artifact_ref = "run/stderr.log"
    mock_record.diagnostics_ref = "run/diagnostics.json"
    mock_record.workspace_path = str(tmp_path / "workspace-without-spool")

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-order-source"] == "artifact-fallback"
    body = response.text
    assert "[sequence=10] Supervisor: managed run started." in body
    assert "[sequence=20] Supervisor: run classified as completed." in body
    assert body.index("[sequence=10] Supervisor: managed run started.") > body.index(
        "[merged-order unavailable: spool metadata missing]"
    )


def test_stream_task_run_log_merged_falls_back_to_legacy_log_artifact(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "combined.log").write_text(
        "legacy combined output\nwarning\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None
    mock_record.log_artifact_ref = "run/combined.log"
    mock_record.workspace_path = str(tmp_path / "workspace-without-spool")

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-order-source"] == "legacy-log-artifact"
    assert response.text == "legacy combined output\nwarning\n"


def test_stream_task_run_log_merged_returns_404_when_both_artifacts_absent(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """When merged, split, and legacy log artifacts are absent, must return 404."""
    test_client, _ = client
    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None
    mock_record.log_artifact_ref = None

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 404
    assert "no stdout/stderr or legacy log artifacts" in response.json()["detail"].lower()


def test_stream_task_run_log_merged_uses_spool_when_stdout_stderr_refs_are_absent(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "live_streams.spool").write_text(
        json.dumps(
            {
                "sequence": 1,
                "stream": "stdout",
                "text": "active run output\n",
                "timestamp": "2026-03-31T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None
    mock_record.workspace_path = str(workspace_path)

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-order-source"] == "spool"
    assert "active run output" in response.text


def test_stream_task_run_log_merged_filters_stale_spool_entries_from_previous_runs(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "live_streams.spool").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sequence": 1757,
                        "stream": "stderr",
                        "text": "old gemini capacity error\n",
                        "timestamp": "2026-04-02T07:17:52+00:00",
                    }
                ),
                json.dumps(
                    {
                        "sequence": 96,
                        "stream": "stderr",
                        "text": "current claude warning\n",
                        "timestamp": "2026-04-02T22:16:37+00:00",
                    }
                ),
                json.dumps(
                    {
                        "sequence": 177,
                        "stream": "stdout",
                        "text": "current claude completion\n",
                        "timestamp": "2026-04-02T22:26:29+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.merged_log_artifact_ref = None
    mock_record.stdout_artifact_ref = None
    mock_record.stderr_artifact_ref = None
    mock_record.workspace_path = str(workspace_path)
    mock_record.started_at = datetime(2026, 4, 2, 22, 16, 37, tzinfo=UTC)

    with patch(
        "api_service.api.routers.task_runs.ManagedRunStore.load",
        return_value=mock_record,
    ):
        response = test_client.get(f"/api/task-runs/{uuid4()}/logs/merged")

    assert response.status_code == 200
    assert response.headers["x-merged-order-source"] == "spool"
    assert "old gemini capacity error" not in response.text
    assert "current claude warning" in response.text
    assert "current claude completion" in response.text


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


@pytest.mark.parametrize(
    ("env_root_name", "artifact_root_name"),
    [
        ("agent_jobs", "artifacts"),
        ("artifacts", "."),
    ],
)
def test_get_task_run_diagnostics_uses_supported_artifact_root_layouts(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    env_root_name: str,
    artifact_root_name: str,
) -> None:
    test_client, _ = client
    env_root = tmp_path / env_root_name
    artifacts_root = env_root if artifact_root_name == "." else env_root / artifact_root_name
    run_dir = artifacts_root / "run"
    run_dir.mkdir(parents=True)
    expected_content = '{"kind":"diagnostics"}'
    (run_dir / "diagnostics.json").write_text(expected_content, encoding="utf-8")
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(env_root))

    mock_record = MagicMock()
    mock_record.diagnostics_ref = "run/diagnostics.json"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/diagnostics")

    assert response.status_code == 200
    assert response.text == expected_content

# ---------------------------------------------------------------------------
# SSE Live Streaming (Phase 3)
# ---------------------------------------------------------------------------

# Tests removed due to an AnyIO test transport deadlock with fake streaming generators


def _build_session_record() -> CodexManagedSessionRecord:
    now = datetime.now(UTC)
    return CodexManagedSessionRecord(
        sessionId="sess:wf-task-1:codex_cli",
        sessionEpoch=2,
        taskRunId="wf-task-1",
        containerId="container-123",
        threadId="thread-2",
        runtimeId="codex_cli",
        imageRef="moonmind:latest",
        controlUrl="docker-exec://container-123",
        status="ready",
        workspacePath="/work/agent_jobs/wf-task-1/repo",
        sessionWorkspacePath="/work/agent_jobs/wf-task-1/session",
        artifactSpoolPath="/work/agent_jobs/wf-task-1/artifacts",
        stdoutArtifactRef="art_stdout",
        stderrArtifactRef="art_stderr",
        diagnosticsRef="art_diag",
        latestSummaryRef="art_summary",
        latestCheckpointRef="art_checkpoint",
        latestControlEventRef="art_control",
        latestResetBoundaryRef="art_reset",
        startedAt=now,
        updatedAt=now,
    )


def _build_artifact(artifact_id: str, link_type: str, *, label: str) -> tuple[SimpleNamespace, list[SimpleNamespace], bool, SimpleNamespace]:
    now = datetime.now(UTC)
    artifact = SimpleNamespace(
        artifact_id=artifact_id,
        created_at=now,
        created_by_principal="system:agent_runtime",
        content_type="application/json",
        size_bytes=128,
        sha256=None,
        storage_backend=db_models.TemporalArtifactStorageBackend.LOCAL_FS,
        storage_key=f"moonmind/artifacts/2026/04/07/{artifact_id}",
        encryption=db_models.TemporalArtifactEncryption.NONE,
        status=db_models.TemporalArtifactStatus.COMPLETE,
        retention_class=db_models.TemporalArtifactRetentionClass.STANDARD,
        expires_at=None,
        redaction_level=db_models.TemporalArtifactRedactionLevel.NONE,
        metadata_json={"label": label},
    )
    links = [
        SimpleNamespace(
            namespace="moonmind",
            workflow_id="wf-step-1",
            run_id="run-step-1",
            link_type=link_type,
            label=label,
            created_at=now,
            created_by_activity_type="activity:agent_runtime.publish_artifacts",
            created_by_worker="worker-1",
        )
    ]
    read_policy = SimpleNamespace(
        raw_access_allowed=True,
        preview_artifact_ref=None,
        default_read_ref=SimpleNamespace(
            artifact_ref_v=1,
            artifact_id=artifact_id,
            sha256=None,
            size_bytes=128,
            content_type="application/json",
            encryption="none",
        ),
    )
    return artifact, links, False, read_policy


def test_get_task_run_artifact_session_projection_returns_grouped_projection(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, artifact_service = client
    record = _build_session_record()

    artifact_payloads = {
        "art_stdout": _build_artifact("art_stdout", "runtime.stdout", label="stdout"),
        "art_stderr": _build_artifact("art_stderr", "runtime.stderr", label="stderr"),
        "art_diag": _build_artifact("art_diag", "runtime.diagnostics", label="diagnostics"),
        "art_summary": _build_artifact("art_summary", "session.summary", label="summary"),
        "art_checkpoint": _build_artifact("art_checkpoint", "session.step_checkpoint", label="checkpoint"),
        "art_control": _build_artifact("art_control", "session.control_event", label="control"),
        "art_reset": _build_artifact("art_reset", "session.reset_boundary", label="reset"),
    }

    async def _get_metadata(*, artifact_id: str, principal: str):
        assert principal == "service:task_runs"
        return artifact_payloads[artifact_id]

    artifact_service.get_metadata.side_effect = _get_metadata

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=record):
        response = test_client.get(
            "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["task_run_id"] == "wf-task-1"
    assert body["session_id"] == "sess:wf-task-1:codex_cli"
    assert body["session_epoch"] == 2
    assert body["latest_summary_ref"]["artifact_id"] == "art_summary"
    assert body["latest_checkpoint_ref"]["artifact_id"] == "art_checkpoint"
    assert body["latest_control_event_ref"]["artifact_id"] == "art_control"
    assert body["latest_reset_boundary_ref"]["artifact_id"] == "art_reset"
    assert [group["group_key"] for group in body["grouped_artifacts"]] == [
        "runtime",
        "continuity",
        "control",
    ]
    assert [artifact["artifact_id"] for artifact in body["grouped_artifacts"][0]["artifacts"]] == [
        "art_stdout",
        "art_stderr",
        "art_diag",
    ]
    assert [artifact["artifact_id"] for artifact in body["grouped_artifacts"][2]["artifacts"]] == [
        "art_control",
        "art_reset",
    ]


def test_get_task_run_artifact_session_projection_reads_durable_state_only(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, artifact_service = client
    record = _build_session_record().model_copy(update={"status": "degraded"})
    artifact_payloads = {
        "art_stdout": _build_artifact("art_stdout", "runtime.stdout", label="stdout"),
        "art_stderr": _build_artifact("art_stderr", "runtime.stderr", label="stderr"),
        "art_diag": _build_artifact("art_diag", "runtime.diagnostics", label="diagnostics"),
        "art_summary": _build_artifact("art_summary", "session.summary", label="summary"),
        "art_checkpoint": _build_artifact("art_checkpoint", "session.step_checkpoint", label="checkpoint"),
        "art_control": _build_artifact("art_control", "session.control_event", label="control"),
        "art_reset": _build_artifact("art_reset", "session.reset_boundary", label="reset"),
    }

    async def _get_metadata(*, artifact_id: str, principal: str):
        assert principal == "service:task_runs"
        return artifact_payloads[artifact_id]

    artifact_service.get_metadata.side_effect = _get_metadata

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=record):
        response = test_client.get(
            "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
        )

    assert response.status_code == 200
    assert response.json()["session_epoch"] == 2


def test_get_task_run_artifact_session_projection_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _artifact_service = client

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=None):
        response = test_client.get(
            "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_projection_not_found"


def test_get_task_run_artifact_session_projection_returns_404_for_task_mismatch(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _artifact_service = client
    record = _build_session_record().model_copy(update={"task_run_id": "wf-task-2"})

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=record):
        response = test_client.get(
            "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_projection_not_found"


def test_get_task_run_artifact_session_projection_allows_owner_access() -> None:
    owner_id = uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    artifact_service = AsyncMock()
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=owner_id,
        email="owner@example.com",
        is_superuser=False,
    )
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: artifact_service

    artifact_payloads = {
        "art_stdout": _build_artifact("art_stdout", "runtime.stdout", label="stdout"),
        "art_stderr": _build_artifact("art_stderr", "runtime.stderr", label="stderr"),
        "art_diag": _build_artifact("art_diag", "runtime.diagnostics", label="diagnostics"),
        "art_summary": _build_artifact("art_summary", "session.summary", label="summary"),
        "art_checkpoint": _build_artifact("art_checkpoint", "session.step_checkpoint", label="checkpoint"),
        "art_control": _build_artifact("art_control", "session.control_event", label="control"),
        "art_reset": _build_artifact("art_reset", "session.reset_boundary", label="reset"),
    }

    async def _get_metadata(*, artifact_id: str, principal: str):
        assert principal == "service:task_runs"
        return artifact_payloads[artifact_id]

    artifact_service.get_metadata.side_effect = _get_metadata

    with TestClient(app) as test_client:
        with patch(
            "api_service.api.routers.task_runs.ManagedSessionStore.load",
            return_value=_build_session_record(),
        ):
            with patch(
                "api_service.api.routers.task_runs._load_execution_owner_binding",
                new=AsyncMock(return_value=("user", str(owner_id))),
            ):
                response = test_client.get(
                    "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
                )

    assert response.status_code == 200


def test_get_task_run_artifact_session_projection_forbids_cross_owner_access() -> None:
    owner_id = uuid4()
    other_id = uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    artifact_service = AsyncMock()
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=other_id,
        email="other@example.com",
        is_superuser=False,
    )
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: artifact_service

    with TestClient(app) as test_client:
        with patch(
            "api_service.api.routers.task_runs.ManagedSessionStore.load",
            return_value=_build_session_record(),
        ):
            with patch(
                "api_service.api.routers.task_runs._load_execution_owner_binding",
                new=AsyncMock(return_value=("user", str(owner_id))),
            ):
                response = test_client.get(
                    "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli"
                )

    assert response.status_code == 403
