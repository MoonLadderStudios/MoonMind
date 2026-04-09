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

from api_service.api.routers import task_runs as task_runs_router
from api_service.api.routers.task_runs import router
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.auth_providers import get_current_user
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth
from api_service.db import models as db_models
from api_service.db.models import User
from moonmind.schemas.agent_runtime_models import RunObservabilityEvent
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


def test_get_observability_summary_returns_session_backed_artifact_refs(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    run_id = uuid4()

    mock_record = MagicMock()
    mock_record.model_dump.return_value = {
        "runId": str(run_id),
        "status": "completed",
        "runtimeId": "codex_cli",
        "stdoutArtifactRef": "sess-1/stdout.log",
        "stderrArtifactRef": "sess-1/stderr.log",
        "diagnosticsRef": "sess-1/diagnostics.json",
        "liveStreamCapable": False,
    }
    mock_record.status = "completed"
    mock_record.live_stream_capable = False

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{run_id}/observability-summary")

    assert response.status_code == 200
    body = response.json()["summary"]
    assert body["runtimeId"] == "codex_cli"
    assert body["stdoutArtifactRef"] == "sess-1/stdout.log"
    assert body["stderrArtifactRef"] == "sess-1/stderr.log"
    assert body["diagnosticsRef"] == "sess-1/diagnostics.json"
    assert body["supportsLiveStreaming"] is False
    assert body["liveStreamStatus"] == "ended"


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


def test_get_observability_summary_includes_session_snapshot(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    run_id = uuid4()
    mock_record = MagicMock()
    mock_record.model_dump.return_value = {"status": "running"}
    mock_record.status = "running"
    mock_record.live_stream_capable = True

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._load_task_run_session_record",
            return_value=_build_session_record(),
        ):
            response = test_client.get(f"/api/task-runs/{run_id}/observability-summary")

    assert response.status_code == 200
    snapshot = response.json()["summary"]["sessionSnapshot"]
    assert snapshot["sessionId"] == "sess:wf-task-1:codex_cli"
    assert snapshot["sessionEpoch"] == 2
    assert snapshot["threadId"] == "thread-2"


def test_get_observability_summary_uses_record_snapshot_when_session_record_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client
    run_id = uuid4()
    mock_record = MagicMock()
    mock_record.model_dump.return_value = {
        "status": "completed",
        "sessionId": "sess-1",
        "sessionEpoch": 3,
        "containerId": "ctr-1",
        "threadId": "thread-3",
        "activeTurnId": "turn-9",
        "observabilityEventsRef": "run-1/observability.events.jsonl",
    }
    mock_record.status = "completed"
    mock_record.live_stream_capable = False
    mock_record.session_id = "sess-1"
    mock_record.session_epoch = 3
    mock_record.container_id = "ctr-1"
    mock_record.thread_id = "thread-3"
    mock_record.active_turn_id = "turn-9"
    mock_record.observability_events_ref = "run-1/observability.events.jsonl"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._load_task_run_session_record",
            return_value=None,
        ):
            response = test_client.get(f"/api/task-runs/{run_id}/observability-summary")

    assert response.status_code == 200
    body = response.json()["summary"]
    assert body["observabilityEventsRef"] == "run-1/observability.events.jsonl"
    assert body["sessionSnapshot"]["sessionId"] == "sess-1"
    assert body["sessionSnapshot"]["sessionEpoch"] == 3
    assert body["sessionSnapshot"]["threadId"] == "thread-3"


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


def test_get_task_run_observability_events_reads_structured_spool_history(
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
                        "sequence": 10,
                        "stream": "stdout",
                        "text": "stdout line\n",
                        "timestamp": "2026-04-08T00:00:00Z",
                        "kind": "stdout_chunk",
                    }
                ),
                json.dumps(
                    {
                        "sequence": 11,
                        "stream": "session",
                        "text": "Epoch boundary reached.",
                        "timestamp": "2026-04-08T00:00:01Z",
                        "kind": "session_reset_boundary",
                        "session_id": "sess:wf-task-1:codex_cli",
                        "session_epoch": 2,
                        "thread_id": "thread-2",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mock_record = MagicMock()
    mock_record.workspace_path = str(workspace_path)
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(f"/api/task-runs/{uuid4()}/observability/events")

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    assert [event["sequence"] for event in body["events"]] == [10, 11]
    assert body["events"][1]["stream"] == "session"
    assert body["events"][1]["kind"] == "session_reset_boundary"
    assert body["events"][1]["sessionId"] == "sess:wf-task-1:codex_cli"
    assert body["events"][1]["sessionEpoch"] == 2
    assert body["events"][1]["threadId"] == "thread-2"
    assert "session_id" not in body["events"][1]


def test_get_task_run_observability_events_filters_spool_fallback_rows(
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
                        "sequence": 1,
                        "stream": "stdout",
                        "text": "stdout line\n",
                        "timestamp": "2026-04-08T00:00:00Z",
                        "kind": "stdout_chunk",
                    }
                ),
                json.dumps(
                    {
                        "sequence": 2,
                        "stream": "session",
                        "text": "boundary\n",
                        "timestamp": "2026-04-08T00:00:01Z",
                        "kind": "session_reset_boundary",
                        "session_id": "sess:wf-task-1:codex_cli",
                        "session_epoch": 2,
                        "thread_id": "thread-2",
                    }
                ),
                json.dumps(
                    {
                        "sequence": 3,
                        "stream": "system",
                        "text": "annotation\n",
                        "timestamp": "2026-04-08T00:00:02Z",
                        "kind": "system_annotation",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mock_record = MagicMock()
    mock_record.workspace_path = str(workspace_path)
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        response = test_client.get(
            f"/api/task-runs/{uuid4()}/observability/events?since=1&stream=session&kind=session_reset_boundary"
        )

    assert response.status_code == 200
    body = response.json()
    assert [event["sequence"] for event in body["events"]] == [2]
    assert body["events"][0]["stream"] == "session"
    assert body["events"][0]["kind"] == "session_reset_boundary"


def test_get_task_run_observability_events_prefers_persisted_event_artifact(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "observability.events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 5,
                        "stream": "stdout",
                        "text": "persisted stdout\n",
                        "timestamp": "2026-04-08T00:00:00Z",
                        "kind": "stdout_chunk",
                    }
                ),
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 6,
                        "stream": "session",
                        "text": "Persisted reset boundary.",
                        "timestamp": "2026-04-08T00:00:01Z",
                        "kind": "reset_boundary_published",
                        "sessionId": "sess-1",
                        "sessionEpoch": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "live_streams.spool").write_text(
        json.dumps(
            {
                "sequence": 99,
                "stream": "stdout",
                "text": "spool fallback should not win\n",
                "timestamp": "2026-04-08T00:10:00Z",
                "kind": "stdout_chunk",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.workspace_path = str(workspace_path)
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    mock_record.observability_events_ref = "run-1/observability.events.jsonl"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(f"/api/task-runs/{uuid4()}/observability/events")

    assert response.status_code == 200
    body = response.json()
    assert [event["sequence"] for event in body["events"]] == [5, 6]
    assert body["events"][1]["kind"] == "reset_boundary_published"
    assert body["events"][0]["runId"] == "run-1"
    assert body["events"][1]["sessionId"] == "sess-1"
    assert body["events"][1]["sessionEpoch"] == 2
    assert "run_id" not in body["events"][0]


def test_get_task_run_observability_events_applies_since_stream_and_kind_filters(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "observability.events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 4,
                        "stream": "stdout",
                        "text": "older stdout\n",
                        "timestamp": "2026-04-08T00:00:00Z",
                        "kind": "stdout_chunk",
                    }
                ),
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 6,
                        "stream": "session",
                        "text": "boundary\n",
                        "timestamp": "2026-04-08T00:00:01Z",
                        "kind": "session_reset_boundary",
                        "sessionId": "sess-1",
                        "sessionEpoch": 2,
                    }
                ),
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 7,
                        "stream": "session",
                        "text": "summary\n",
                        "timestamp": "2026-04-08T00:00:02Z",
                        "kind": "summary_published",
                        "sessionId": "sess-1",
                        "sessionEpoch": 2,
                    }
                ),
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": 8,
                        "stream": "session",
                        "text": "session started\n",
                        "timestamp": "2026-04-08T00:00:03Z",
                        "kind": "session_started",
                        "sessionId": "sess-1",
                        "sessionEpoch": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.workspace_path = str(tmp_path / "workspace")
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    mock_record.observability_events_ref = "run-1/observability.events.jsonl"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(
                f"/api/task-runs/{uuid4()}/observability/events?since=5&stream=session&kind=session_reset_boundary&kind=summary_published&limit=5"
            )

    assert response.status_code == 200
    body = response.json()
    assert [event["sequence"] for event in body["events"]] == [6, 7]
    assert {event["kind"] for event in body["events"]} == {
        "session_reset_boundary",
        "summary_published",
    }
    assert all(event["stream"] == "session" for event in body["events"])


def test_get_task_run_observability_events_limits_to_oldest_matching_rows_after_since(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "observability.events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "runId": "run-1",
                        "sequence": sequence,
                        "stream": "stdout",
                        "text": f"event-{sequence}\n",
                        "timestamp": f"2026-04-08T00:00:0{sequence - 5}Z",
                        "kind": "stdout_chunk",
                    }
                )
                for sequence in (6, 7, 8, 9)
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.workspace_path = str(tmp_path / "workspace")
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    mock_record.observability_events_ref = "run-1/observability.events.jsonl"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(
                f"/api/task-runs/{uuid4()}/observability/events?since=5&limit=2"
            )

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert [event["sequence"] for event in body["events"]] == [6, 7]


def test_get_task_run_observability_events_keeps_artifact_fallback_rows_when_since_is_present(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.log").write_text("artifact stdout\n", encoding="utf-8")

    mock_record = MagicMock()
    mock_record.workspace_path = str(tmp_path / "workspace")
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    mock_record.observability_events_ref = None
    mock_record.diagnostics_ref = None
    mock_record.stdout_artifact_ref = "run-1/stdout.log"
    mock_record.stderr_artifact_ref = None

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            response = test_client.get(
                f"/api/task-runs/{uuid4()}/observability/events?since=5"
            )

    assert response.status_code == 200
    body = response.json()
    assert [event["kind"] for event in body["events"]] == ["stdout_chunk"]
    assert body["events"][0]["sequence"] == 0
    assert body["events"][0]["text"] == "artifact stdout\n"


def test_get_task_run_observability_events_uses_record_snapshot_when_session_record_missing(
    client: tuple[TestClient, AsyncMock],
    tmp_path,
) -> None:
    test_client, _ = client
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "observability.events.jsonl").write_text(
        json.dumps(
            {
                "runId": "run-1",
                "sequence": 5,
                "stream": "session",
                "text": "summary\n",
                "timestamp": "2026-04-08T00:00:00Z",
                "kind": "summary_published",
                "sessionId": "sess-1",
                "sessionEpoch": 3,
                "threadId": "thread-3",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    mock_record = MagicMock()
    mock_record.workspace_path = str(tmp_path / "workspace")
    mock_record.started_at = datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    mock_record.observability_events_ref = "run-1/observability.events.jsonl"
    mock_record.session_id = "sess-1"
    mock_record.session_epoch = 3
    mock_record.container_id = "ctr-1"
    mock_record.thread_id = "thread-3"
    mock_record.active_turn_id = "turn-9"

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch(
            "api_service.api.routers.task_runs._get_agent_runtime_artifacts_root",
            return_value=str(artifacts_root),
        ):
            with patch(
                "api_service.api.routers.task_runs._load_task_run_session_record",
                return_value=None,
            ):
                response = test_client.get(f"/api/task-runs/{uuid4()}/observability/events")

    assert response.status_code == 200
    body = response.json()
    assert body["sessionSnapshot"]["sessionId"] == "sess-1"
    assert body["sessionSnapshot"]["sessionEpoch"] == 3
    assert body["sessionSnapshot"]["containerId"] == "ctr-1"
    assert body["sessionSnapshot"]["threadId"] == "thread-3"
    assert body["sessionSnapshot"]["activeTurnId"] == "turn-9"


def test_stream_task_run_live_logs_serializes_canonical_event_aliases(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _ = client

    mock_record = MagicMock()
    mock_record.status = "running"
    mock_record.live_stream_capable = True
    mock_record.workspace_path = "/tmp/workspace"

    async def _follow(*args, **kwargs):
        yield RunObservabilityEvent(
            runId="run-1",
            sequence=11,
            stream="session",
            text="boundary\n",
            timestamp="2026-04-08T00:00:01Z",
            kind="session_reset_boundary",
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
            activeTurnId="turn-3",
        )

    with patch("api_service.api.routers.task_runs.ManagedRunStore.load", return_value=mock_record):
        with patch("api_service.api.routers.task_runs.SpoolLogReader.follow", new=_follow):
            response = test_client.get(f"/api/task-runs/{uuid4()}/logs/stream?since=10")

    assert response.status_code == 200
    assert '"sessionId":"sess-1"' in response.text
    assert '"activeTurnId":"turn-3"' in response.text
    assert '"session_id"' not in response.text


def test_load_task_run_session_record_uses_targeted_standard_paths(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_sessions = tmp_path / "managed_sessions"
    managed_sessions.mkdir()
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    older = _build_session_record().model_copy(
        update={
            "session_id": "sess:wf-task-1:codex_cli",
            "updated_at": datetime(2026, 4, 8, 0, 0, tzinfo=UTC),
        }
    )
    newer = _build_session_record().model_copy(
        update={
            "session_id": "sess:wf-task-1:codex_cli-2",
            "updated_at": datetime(2026, 4, 8, 1, 0, tzinfo=UTC),
        }
    )
    (managed_sessions / "sess:wf-task-1:codex_cli.json").write_text(
        json.dumps(older.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    (managed_sessions / "sess:wf-task-1:codex_cli-2.json").write_text(
        json.dumps(newer.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )

    with patch("pathlib.Path.rglob", side_effect=AssertionError("unexpected recursive scan")):
        record = task_runs_router._load_task_run_session_record("wf-task-1")

    assert record is not None
    assert record.session_id == "sess:wf-task-1:codex_cli-2"


def test_iter_diagnostics_observability_events_dedupes_system_annotations(tmp_path) -> None:
    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "observability_events": [
                    {
                        "sequence": 7,
                        "timestamp": "2026-04-08T00:00:01Z",
                        "stream": "system",
                        "text": "Session reset",
                        "kind": "system_annotation",
                    }
                ],
                "annotations": [
                    {
                        "sequence": 7,
                        "timestamp": "2026-04-08T00:00:01Z",
                        "text": "Session reset",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    events = list(task_runs_router._iter_diagnostics_observability_events(diagnostics_path))

    assert len(events) == 1
    assert events[0]["kind"] == "system_annotation"


def test_event_sort_key_uses_timestamp_before_sequence() -> None:
    later_sequenced = {
        "sequence": 1,
        "timestamp": "2026-04-08T00:00:02Z",
        "stream": "system",
        "text": "later",
        "kind": "system_annotation",
    }
    earlier_unsequenced = {
        "sequence": 0,
        "timestamp": "2026-04-08T00:00:01Z",
        "stream": "stdout",
        "text": "earlier",
        "kind": "stdout_chunk",
    }

    ordered = sorted(
        [later_sequenced, earlier_unsequenced],
        key=task_runs_router._event_sort_key,
    )

    assert ordered[0]["text"] == "earlier"


def test_iter_historical_artifact_events_chunks_large_logs_and_keeps_tail(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(artifacts_root))

    stdout_text = ("A" * 65536) + ("B" * 65536) + ("C" * 65536)
    (artifacts_root / "run").mkdir()
    (artifacts_root / "run" / "stdout.log").write_text(stdout_text, encoding="utf-8")

    record = SimpleNamespace(
        diagnostics_ref=None,
        stdout_artifact_ref="run/stdout.log",
        stderr_artifact_ref=None,
        started_at=datetime(2026, 4, 8, 0, 0, tzinfo=UTC),
    )

    events = list(
        task_runs_router._iter_historical_artifact_events(
            record,
            None,
            limit_per_stream=2,
        )
    )

    assert len(events) == 2
    assert all(event["kind"] == "stdout_chunk" for event in events)
    assert events[0]["offset"] > 0
    assert events[0]["text"] == "B" * 65536
    assert events[1]["text"] == "C" * 65536


def test_iter_historical_artifact_events_preserves_specific_session_artifact_refs(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(artifacts_root))

    (artifacts_root / "art_control").write_text(
        json.dumps(
            {
                "action": "clear_session",
                "previousSessionEpoch": 1,
                "newSessionEpoch": 2,
                "previousThreadId": "thread-1",
                "newThreadId": "thread-2",
                "recordedAt": "2026-04-08T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    (artifacts_root / "art_reset").write_text(
        json.dumps(
            {
                "sessionEpoch": 2,
                "threadId": "thread-2",
                "recordedAt": "2026-04-08T00:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    (artifacts_root / "art_summary").write_text("summary", encoding="utf-8")
    (artifacts_root / "art_checkpoint").write_text("checkpoint", encoding="utf-8")

    record = SimpleNamespace(
        diagnostics_ref=None,
        stdout_artifact_ref=None,
        stderr_artifact_ref=None,
        started_at=datetime(2026, 4, 8, 0, 0, tzinfo=UTC),
    )
    session_record = _build_session_record()

    events = list(
        task_runs_router._iter_historical_artifact_events(
            record,
            session_record,
            limit_per_stream=20,
        )
    )
    events_by_kind = {event["kind"]: event for event in events}

    assert events_by_kind["summary_published"]["metadata"]["summaryRef"] == "art_summary"
    assert events_by_kind["summary_published"]["metadata"]["artifactRef"] == "art_summary"
    assert events_by_kind["checkpoint_published"]["metadata"]["checkpointRef"] == "art_checkpoint"
    assert events_by_kind["checkpoint_published"]["metadata"]["artifactRef"] == "art_checkpoint"
    assert events_by_kind["session_cleared"]["metadata"]["controlEventRef"] == "art_control"
    assert events_by_kind["session_cleared"]["metadata"]["resetBoundaryRef"] == "art_reset"
    assert events_by_kind["session_reset_boundary"]["metadata"]["controlEventRef"] == "art_control"
    assert events_by_kind["session_reset_boundary"]["metadata"]["resetBoundaryRef"] == "art_reset"


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


def test_get_task_run_artifact_session_projection_returns_404_for_invalid_session_id(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _artifact_service = client

    with patch(
        "api_service.api.routers.task_runs.ManagedSessionStore.load",
        side_effect=ValueError("session_id resolves outside store root"),
    ):
        response = test_client.get("/api/task-runs/wf-task-1/artifact-sessions/%2E%2E")

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
    assert (
        response.json()["detail"]
        == "You do not have permission to access this task run or its session projection."
    )


def test_post_task_run_artifact_session_control_routes_send_follow_up_and_returns_projection(
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
    client_adapter = AsyncMock()
    client_adapter.update_workflow.return_value = {"accepted": True}

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=record):
        with patch("api_service.api.routers.task_runs.get_temporal_client_adapter", return_value=client_adapter):
            response = test_client.post(
                "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli/control",
                json={
                    "action": "send_follow_up",
                    "message": "Continue reusing the current session.",
                    "reason": "Operator clarification",
                },
            )

    assert response.status_code == 200
    client_adapter.update_workflow.assert_awaited_once_with(
        "wf-task-1:session:codex_cli",
        "SendFollowUp",
        {
            "message": "Continue reusing the current session.",
            "reason": "Operator clarification",
        },
    )
    body = response.json()
    assert body["action"] == "send_follow_up"
    assert body["projection"]["session_epoch"] == 2
    assert body["projection"]["latest_summary_ref"]["artifact_id"] == "art_summary"
    assert body["projection"]["latest_checkpoint_ref"]["artifact_id"] == "art_checkpoint"


def test_post_task_run_artifact_session_control_routes_clear_session_and_returns_projection(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, artifact_service = client
    record = _build_session_record().model_copy(
        update={
            "session_epoch": 3,
            "thread_id": "thread-3",
            "latest_control_event_ref": "art_control",
            "latest_reset_boundary_ref": "art_reset",
        }
    )

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
    client_adapter = AsyncMock()
    client_adapter.update_workflow.return_value = {"accepted": True}

    with patch("api_service.api.routers.task_runs.ManagedSessionStore.load", return_value=record):
        with patch("api_service.api.routers.task_runs.get_temporal_client_adapter", return_value=client_adapter):
            response = test_client.post(
                "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli/control",
                json={
                    "action": "clear_session",
                    "reason": "Reset stale context",
                },
            )

    assert response.status_code == 200
    client_adapter.update_workflow.assert_awaited_once_with(
        "wf-task-1:session:codex_cli",
        "ClearSession",
        {
            "reason": "Reset stale context",
        },
    )
    body = response.json()
    assert body["action"] == "clear_session"
    assert body["projection"]["session_epoch"] == 3
    assert body["projection"]["latest_control_event_ref"]["artifact_id"] == "art_control"
    assert body["projection"]["latest_reset_boundary_ref"]["artifact_id"] == "art_reset"


def test_post_task_run_artifact_session_control_rejects_blank_follow_up_message(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, _artifact_service = client

    response = test_client.post(
        "/api/task-runs/wf-task-1/artifact-sessions/sess:wf-task-1:codex_cli/control",
        json={
            "action": "send_follow_up",
            "message": "   ",
        },
    )

    assert response.status_code == 422
