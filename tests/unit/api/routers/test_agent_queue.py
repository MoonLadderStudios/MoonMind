"""Unit tests for the agent queue API router."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import ANY, AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from api_service.api.routers.agent_queue import (
    _build_job_model,
    _get_service,
    _require_worker_auth,
    _WorkerRequestAuth,
    router,
    stream_job_events,
)
from api_service.auth_providers import get_current_user
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import (
    AgentJobNotFoundError,
    AgentJobOwnershipError,
    AgentJobStateError,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueJobAuthorizationError,
    AgentQueueValidationError,
    QueueJobPage,
    QueueSafeguardJob,
    QueueSafeguardSnapshot,
    QueueSystemMetadata,
    QueueSystemResponse,
)


def _build_job(status: models.AgentJobStatus = models.AgentJobStatus.QUEUED):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        type="codex_exec",
        status=status,
        priority=10,
        payload={"instruction": "run"},
        created_by_user_id=uuid4(),
        requested_by_user_id=uuid4(),
        cancel_requested_by_user_id=None,
        cancel_requested_at=None,
        cancel_reason=None,
        affinity_key="repo/moonmind",
        claimed_by=None,
        lease_expires_at=None,
        next_attempt_at=None,
        attempt=1,
        max_attempts=3,
        result_summary=None,
        error_message=None,
        finish_outcome_code=None,
        finish_outcome_stage=None,
        finish_outcome_reason=None,
        finish_summary_json=None,
        artifacts_path=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


def _build_artifact(job_id: UUID | None = None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        job_id=job_id or uuid4(),
        name="inputs/abc/file.png",
        content_type="image/png",
        size_bytes=123,
        digest="sha256:abc123",
        storage_path="job/inputs/abc/file.png",
        created_at=now,
    )


def _build_manifest_job():
    job = _build_job()
    job.type = "manifest"
    job.payload = {
        "manifest": {
            "name": "demo-manifest",
            "action": "run",
            "source": {
                "kind": "inline",
                "name": "demo-manifest",
                "content": "version: 'v0'\\nmetadata:\\n  name: demo-manifest\\n",
            },
            "options": {"dryRun": True},
        },
        "manifestHash": "sha256:abc123",
        "manifestVersion": "v0",
        "requiredCapabilities": [
            "manifest",
            "embeddings",
            "openai",
            "qdrant",
            "confluence",
        ],
        "effectiveRunConfig": {"dryRun": True},
        "manifestSecretRefs": {
            "profile": [
                {
                    "envKey": "OPENAI_API_KEY",
                    "provider": "openai",
                    "field": "api_key",
                    "normalized": "profile://openai#api_key",
                },
                {
                    "envKey": "OPENAI_API_KEY",
                    "provider": "openai",
                    "field": "api_key",
                    "normalized": "profile://openai#api_key",
                },
            ],
            "vault": [
                {
                    "ref": "vault://kv/manifests/demo-manifest#token",
                    "mount": "kv",
                    "path": "manifests/demo-manifest",
                    "field": "token",
                },
                {
                    "ref": "vault://kv/manifests/demo-manifest#token",
                    "mount": "kv",
                    "path": "manifests/demo-manifest",
                    "field": "token",
                },
            ],
        },
    }
    return job


def _build_system_metadata(*, paused: bool = False, version: int = 1):
    now = datetime.now(UTC)
    return QueueSystemMetadata(
        workers_paused=paused,
        mode=None,
        reason=None,
        version=version,
        requested_by_user_id=None,
        requested_at=None,
        updated_at=now,
    )


def test_build_job_model_warns_and_falls_back_for_non_dict_payload(caplog) -> None:
    """Non-dict payloads should be coerced with visibility for troubleshooting."""

    job = _build_job()
    with caplog.at_level("WARNING"):
        model = _build_job_model(job=job, payload="not-a-dict")

    assert model.payload == {}
    assert "returned non-dict payload" in caplog.text


def _build_live_session(
    *,
    task_run_id=None,
    status: models.AgentJobLiveSessionStatus = models.AgentJobLiveSessionStatus.READY,
):
    now = datetime.now(UTC)
    run_id = task_run_id or uuid4()
    return SimpleNamespace(
        id=uuid4(),
        task_run_id=run_id,
        provider=models.AgentJobLiveSessionProvider.TMATE,
        status=status,
        created_at=now,
        updated_at=now,
        ready_at=now if status is models.AgentJobLiveSessionStatus.READY else None,
        ended_at=None,
        expires_at=now,
        rw_granted_until=None,
        worker_id="worker-1",
        worker_hostname="host-1",
        tmate_session_name="mm-test",
        tmate_socket_path="/tmp/moonmind/tmate/test.sock",
        attach_ro="ssh ro",
        web_ro=None,
        last_heartbeat_at=now,
        error_message=None,
    )


def _build_control_event(task_run_id):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        task_run_id=task_run_id,
        actor_user_id=uuid4(),
        action="send_message",
        metadata_json={"message": "hello"},
        created_at=now,
    )


@pytest.fixture
def client(monkeypatch) -> Iterator[tuple[TestClient, AsyncMock]]:
    """Provide a TestClient with queue service dependency overridden."""
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", False)

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

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="queue-tester@example.com",
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
        yield test_client, mock_service
    app.dependency_overrides.clear()


@pytest.fixture
def superuser_client() -> Iterator[tuple[TestClient, AsyncMock]]:
    """Provide a TestClient with superuser identity."""

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

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="queue-superuser@example.com",
        is_active=True,
        is_superuser=True,
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
        yield test_client, mock_service
    app.dependency_overrides.clear()


def test_create_job_success(client: tuple[TestClient, AsyncMock]) -> None:
    """POST /jobs should return created job payload."""

    test_client, service = client
    job = _build_job()
    service.create_job.return_value = job

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "codex_exec",
            "priority": 10,
            "payload": {"instruction": "run"},
            "maxAttempts": 3,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["status"] == models.AgentJobStatus.QUEUED.value
    assert body["type"] == "codex_exec"
    service.create_job.assert_awaited_once()


def test_create_job_routes_proposal_requested_tasks_to_queue(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task submissions with proposeTasks=true should stay on queue workers."""

    test_client, service = client
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True, raising=False)
    temporal_submit = AsyncMock()
    monkeypatch.setattr(
        "api_service.api.routers.agent_queue._create_execution_from_task_request",
        temporal_submit,
    )

    job = _build_job()
    job.type = "task"
    service.create_job.return_value = job

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "task",
            "priority": 5,
            "payload": {
                "repository": "Moon/Test",
                "task": {
                    "instructions": "Add proposal handling coverage",
                    "proposeTasks": True,
                },
            },
            "maxAttempts": 3,
        },
    )

    assert response.status_code == 201
    service.create_job.assert_awaited_once()
    temporal_submit.assert_not_awaited()


def test_create_job_routes_non_proposal_tasks_to_temporal_when_enabled(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task submissions with proposeTasks=false should keep Temporal routing."""

    test_client, service = client
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True, raising=False)
    execution = SimpleNamespace(
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        started_at=None,
    )
    temporal_submit = AsyncMock(return_value=execution)
    monkeypatch.setattr(
        "api_service.api.routers.agent_queue._create_execution_from_task_request",
        temporal_submit,
    )

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "task",
            "priority": 3,
            "payload": {
                "repository": "Moon/Test",
                "task": {
                    "instructions": "Run without follow-up proposals",
                    "proposeTasks": False,
                },
            },
            "maxAttempts": 2,
        },
    )

    assert response.status_code == 201
    service.create_job.assert_not_awaited()
    temporal_submit.assert_awaited_once()
    assert response.json()["status"] == "queued"
    assert response.json()["type"] == "task"


def test_update_queued_job_success(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT /jobs/{id} should return updated job payload."""

    test_client, service = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
    job = _build_job()
    job.type = "task"
    service.update_queued_job.return_value = job

    response = test_client.put(
        f"/api/queue/jobs/{job.id}",
        json={
            "type": "task",
            "priority": 3,
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
            "maxAttempts": 4,
            "expectedUpdatedAt": "2026-02-25T01:23:45.678Z",
            "note": "tighten instructions",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["status"] == models.AgentJobStatus.QUEUED.value
    assert body["type"] == "task"
    service.update_queued_job.assert_awaited_once()
    assert service.update_queued_job.await_args.kwargs["actor_is_superuser"] is False


def test_update_queued_job_disabled_auth_uses_operator_override(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled auth mode should bypass owner checks via operator override."""

    test_client, service = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    job = _build_job()
    job.type = "task"
    service.update_queued_job.return_value = job

    response = test_client.put(
        f"/api/queue/jobs/{job.id}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
        },
    )

    assert response.status_code == 200
    service.update_queued_job.assert_awaited_once()
    assert service.update_queued_job.await_args.kwargs["actor_is_superuser"] is True


def test_update_queued_job_superuser_passes_authorization_flag(
    superuser_client: tuple[TestClient, AsyncMock],
) -> None:
    """Superuser requests should set actor_is_superuser on service updates."""

    test_client, service = superuser_client
    job = _build_job()
    job.type = "task"
    service.update_queued_job.return_value = job

    response = test_client.put(
        f"/api/queue/jobs/{job.id}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
        },
    )

    assert response.status_code == 200
    service.update_queued_job.assert_awaited_once()
    assert service.update_queued_job.await_args.kwargs["actor_is_superuser"] is True


def test_update_queued_job_state_conflict_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queued job update state conflicts should return HTTP 409."""

    test_client, service = client
    service.update_queued_job.side_effect = AgentJobStateError("already running")

    response = test_client.put(
        f"/api/queue/jobs/{uuid4()}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "job_state_conflict"


def test_update_queued_job_validation_error_maps_422(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queued job update payload validation errors should return HTTP 422."""

    test_client, service = client
    service.update_queued_job.side_effect = AgentQueueValidationError("invalid payload")

    response = test_client.put(
        f"/api/queue/jobs/{uuid4()}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test"},
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"]["code"] == "invalid_queue_payload"


def test_update_queued_job_authorization_error_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queued job update ownership errors should return HTTP 403."""

    test_client, service = client
    service.update_queued_job.side_effect = AgentQueueJobAuthorizationError("not owner")

    response = test_client.put(
        f"/api/queue/jobs/{uuid4()}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "job_not_authorized"


def test_update_queued_job_not_found_maps_404(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queued job update missing target should return HTTP 404."""

    test_client, service = client
    missing_id = uuid4()
    service.update_queued_job.side_effect = AgentJobNotFoundError(missing_id)

    response = test_client.put(
        f"/api/queue/jobs/{missing_id}",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Update"}},
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]["code"] == "job_not_found"


def test_update_queued_job_claude_runtime_gate_maps_400(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queued job update runtime gate errors should map to HTTP 400."""

    test_client, service = client
    service.update_queued_job.side_effect = AgentQueueValidationError(
        "targetRuntime=claude requires ANTHROPIC_API_KEY"
    )

    response = test_client.put(
        f"/api/queue/jobs/{uuid4()}",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Test",
                "targetRuntime": "claude",
                "task": {"instructions": "Update"},
            },
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"]["code"] == "claude_runtime_disabled"


def test_resubmit_job_success(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /jobs/{id}/resubmit should return newly created job payload."""

    test_client, service = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
    source_job_id = uuid4()
    created_job = _build_job()
    created_job.type = "task"
    service.resubmit_job.return_value = created_job

    response = test_client.post(
        f"/api/queue/jobs/{source_job_id}/resubmit",
        json={
            "type": "task",
            "priority": 3,
            "payload": {
                "repository": "Moon/Test",
                "task": {"instructions": "Retry with edits"},
            },
            "maxAttempts": 4,
            "note": "updated inputs",
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["id"] == str(created_job.id)
    assert body["type"] == "task"
    service.resubmit_job.assert_awaited_once()
    assert service.resubmit_job.await_args.kwargs["actor_is_superuser"] is False


def test_resubmit_job_disabled_auth_uses_operator_override(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled auth mode should bypass owner checks for resubmit."""

    test_client, service = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    source_job_id = uuid4()
    created_job = _build_job()
    created_job.type = "task"
    service.resubmit_job.return_value = created_job

    response = test_client.post(
        f"/api/queue/jobs/{source_job_id}/resubmit",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Test",
                "task": {"instructions": "Retry with edits"},
            },
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    service.resubmit_job.assert_awaited_once()
    assert service.resubmit_job.await_args.kwargs["actor_is_superuser"] is True


def test_resubmit_job_superuser_passes_authorization_flag(
    superuser_client: tuple[TestClient, AsyncMock],
) -> None:
    """Superuser requests should set actor_is_superuser on resubmit calls."""

    test_client, service = superuser_client
    source_job_id = uuid4()
    created_job = _build_job()
    created_job.type = "task"
    service.resubmit_job.return_value = created_job

    response = test_client.post(
        f"/api/queue/jobs/{source_job_id}/resubmit",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Test",
                "task": {"instructions": "Retry with edits"},
            },
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    service.resubmit_job.assert_awaited_once()
    assert service.resubmit_job.await_args.kwargs["actor_is_superuser"] is True


def test_resubmit_job_state_conflict_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Terminal-state eligibility conflicts should return HTTP 409."""

    test_client, service = client
    service.resubmit_job.side_effect = AgentJobStateError("cannot resubmit")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/resubmit",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Retry"}},
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "job_state_conflict"


def test_resubmit_job_validation_error_maps_422(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Resubmit payload validation errors should return HTTP 422."""

    test_client, service = client
    service.resubmit_job.side_effect = AgentQueueValidationError("invalid payload")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/resubmit",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Retry"}},
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"]["code"] == "invalid_queue_payload"


def test_resubmit_job_requires_payload(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Missing resubmit payload should fail fast during request validation."""

    test_client, _ = client
    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/resubmit",
        json={"type": "task"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_resubmit_job_claude_runtime_gate_maps_400(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Resubmit runtime gate errors should return HTTP 400."""

    test_client, service = client
    service.resubmit_job.side_effect = AgentQueueValidationError(
        "targetRuntime=claude requires ANTHROPIC_API_KEY"
    )

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/resubmit",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Test",
                "targetRuntime": "claude",
                "task": {"instructions": "Retry"},
            },
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"]["code"] == "claude_runtime_disabled"


def test_resubmit_job_authorization_error_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Resubmit ownership errors should return HTTP 403."""

    test_client, service = client
    service.resubmit_job.side_effect = AgentQueueJobAuthorizationError("not owner")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/resubmit",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Retry"}},
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "job_not_authorized"


def test_resubmit_job_not_found_maps_404(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Resubmit missing source job should return HTTP 404."""

    test_client, service = client
    source_job_id = uuid4()
    service.resubmit_job.side_effect = AgentJobNotFoundError(source_job_id)

    response = test_client.post(
        f"/api/queue/jobs/{source_job_id}/resubmit",
        json={
            "type": "task",
            "payload": {"repository": "Moon/Test", "task": {"instructions": "Retry"}},
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]["code"] == "job_not_found"


def test_create_job_rejects_claude_runtime_without_api_key(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Claude runtime requests should map to HTTP 400 when API key is missing."""

    test_client, service = client
    service.create_job.side_effect = AgentQueueValidationError(
        "targetRuntime=claude requires ANTHROPIC_API_KEY to be configured"
    )

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "claude",
            },
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["detail"]["code"] == "claude_runtime_disabled"
    assert (
        body["detail"]["message"]
        == "targetRuntime=claude is not available in the current server configuration"
    )
    service.create_job.assert_awaited_once()


def test_create_job_rejects_jules_runtime_without_config(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Jules runtime requests should map to HTTP 400 when Jules is disabled."""

    test_client, service = client
    service.create_job.side_effect = AgentQueueValidationError(
        "targetRuntime=jules requires JULES_API_KEY configured (set JULES_ENABLED=false to explicitly disable)"
    )

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "task",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "jules",
            },
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["detail"]["code"] == "jules_runtime_disabled"
    assert (
        body["detail"]["message"]
        == "targetRuntime=jules is not available in the current server configuration"
    )
    service.create_job.assert_awaited_once()


def test_create_job_with_attachments_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """POST /jobs/with-attachments should call attachment-aware service."""

    test_client, service = client
    job = _build_job()
    artifact = _build_artifact(job.id)
    service.create_job_with_attachments.return_value = (job, [artifact])

    payload = {
        "request": json.dumps(
            {
                "type": "task",
                "priority": 5,
                "payload": {"repository": "Moon/Test"},
                "maxAttempts": 3,
            }
        )
    }
    files = [
        (
            "files",
            (
                "image.png",
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
                "image/png",
            ),
        )
    ]

    response = test_client.post(
        "/api/queue/jobs/with-attachments", data=payload, files=files
    )

    assert response.status_code == 201
    body = response.json()
    assert body["job"]["id"] == str(job.id)
    assert len(body["attachments"]) == 1
    service.create_job_with_attachments.assert_awaited_once()
    called_attachments = service.create_job_with_attachments.await_args.kwargs[
        "attachments"
    ]
    assert len(called_attachments) == 1
    assert called_attachments[0].filename == "image.png"
    assert called_attachments[0].content_type == "image/png"
    assert called_attachments[0].data.startswith(b"\x89PNG")


def test_create_job_with_attachments_rejects_temporal_routing(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment submission should fail fast when task routing is Temporal."""

    test_client, service = client
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", False)
    files = [
        (
            "files",
            ("image.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        )
    ]

    response = test_client.post(
        "/api/queue/jobs/with-attachments",
        data={
            "request": json.dumps(
                {"type": "task", "payload": {"repository": "Moon/Test"}}
            )
        },
        files=files,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"]["code"] == "invalid_routing_target"
    service.create_job_with_attachments.assert_not_awaited()


def test_create_job_with_attachments_invalid_request(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Malformed job request payload should return HTTP 422."""

    test_client, service = client
    files = [
        (
            "files",
            ("image.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        )
    ]
    response = test_client.post(
        "/api/queue/jobs/with-attachments",
        data={"request": "{not-json}"},
        files=files,
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    service.create_job_with_attachments.assert_not_awaited()


def test_create_job_with_attachments_file_too_large(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router should reject oversized attachments before service dispatch."""

    test_client, service = client
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_max_bytes",
        2,
    )
    files = [
        (
            "files",
            ("image.png", b"\x00" * 3, "image/png"),
        )
    ]
    response = test_client.post(
        "/api/queue/jobs/with-attachments",
        data={"request": json.dumps({"type": "task", "payload": {}})},
        files=files,
    )
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    service.create_job_with_attachments.assert_not_awaited()


def test_create_job_with_attachments_total_too_large(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router should enforce total attachment byte limits."""

    test_client, service = client
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_total_bytes",
        4,
    )
    files = [
        ("files", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")),
        ("files", ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")),
    ]
    response = test_client.post(
        "/api/queue/jobs/with-attachments",
        data={"request": json.dumps({"type": "task", "payload": {}})},
        files=files,
    )
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    service.create_job_with_attachments.assert_not_awaited()


def test_create_job_with_attachments_maps_attachment_type_error(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Attachment validation errors should surface a stable API error code."""

    test_client, service = client
    service.create_job_with_attachments.side_effect = AgentQueueValidationError(
        "attachment content type must be PNG, JPEG, or WebP"
    )
    files = [
        (
            "files",
            ("invalid-signature.png", b"this-is-not-an-image", "image/png"),
        )
    ]

    response = test_client.post(
        "/api/queue/jobs/with-attachments",
        data={"request": json.dumps({"type": "task", "payload": {}})},
        files=files,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"]["code"] == "attachment_type_not_allowed"


def test_list_job_attachments_unauthorized_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Owner authorization failures for attachment list should map to HTTP 403."""

    test_client, service = client
    service.list_attachments_for_user.side_effect = AgentQueueJobAuthorizationError(
        "not owner"
    )

    response = test_client.get(f"/api/queue/jobs/{uuid4()}/attachments")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "job_not_authorized"


def test_download_job_attachment_unauthorized_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Owner authorization failures for attachment download should map to HTTP 403."""

    test_client, service = client
    service.get_attachment_download_for_user.side_effect = (
        AgentQueueJobAuthorizationError("not owner")
    )
    response = test_client.get(
        f"/api/queue/jobs/{uuid4()}/attachments/{uuid4()}/download"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "job_not_authorized"


def test_create_manifest_job_sanitizes_payload(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Manifest submissions should return sanitized payload metadata."""

    test_client, service = client
    job = _build_manifest_job()
    service.create_job.return_value = job

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "manifest",
            "payload": {
                "manifest": {
                    "name": "demo-manifest",
                    "source": {"kind": "inline", "content": "version: 'v0'"},
                }
            },
        },
    )

    assert response.status_code == 201
    payload = response.json()["payload"]
    assert payload["manifest"]["name"] == "demo-manifest"
    assert payload["manifest"]["source"]["kind"] == "inline"
    assert "content" not in payload["manifest"]["source"]
    assert payload["manifestHash"] == "sha256:abc123"
    assert payload["manifestSecretRefs"]["profile"] == [
        {
            "envKey": "OPENAI_API_KEY",
            "provider": "openai",
            "field": "api_key",
            "normalized": "profile://openai#api_key",
        }
    ]
    assert payload["manifestSecretRefs"]["vault"] == [
        {
            "ref": "vault://kv/manifests/demo-manifest#token",
            "mount": "kv",
            "path": "manifests/demo-manifest",
            "field": "token",
        }
    ]


def test_create_manifest_job_validation_error(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Manifest contract errors should map to HTTP 422."""

    test_client, service = client
    service.create_job.side_effect = AgentQueueValidationError("invalid manifest")

    response = test_client.post(
        "/api/queue/jobs",
        json={
            "type": "manifest",
            "payload": {
                "manifest": {
                    "name": "demo-manifest",
                    "source": {"kind": "inline", "content": "version: 'v0'"},
                }
            },
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"]["code"] == "invalid_manifest_job"


def test_claim_job_empty_queue_returns_null(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Claim should return null job when queue has no eligible entries."""

    test_client, service = client
    service.claim_job.return_value = QueueSystemResponse(
        job=None,
        system=_build_system_metadata(paused=True),
    )

    response = test_client.post(
        "/api/queue/jobs/claim",
        json={"workerId": "worker-1", "leaseSeconds": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job"] is None
    assert body["system"]["workersPaused"] is True
    assert body["system"]["version"] == 1
    service.claim_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_require_worker_auth_accepts_worker_token_without_oidc_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker-token auth must not require user dependency when token is present."""

    mock_service = AsyncMock()
    mock_service.resolve_worker_token.return_value = SimpleNamespace(
        auth_source="worker_token",
        worker_id="worker-1",
        allowed_repositories=("Moon/Mind",),
        allowed_job_types=("codex_exec",),
        capabilities=("codex",),
    )
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")

    resolved = await _require_worker_auth(
        worker_token="mmwt_token",
        service=mock_service,
        user=None,
    )

    assert resolved.worker_id == "worker-1"
    assert resolved.auth_source == "worker_token"
    mock_service.resolve_worker_token.assert_awaited_once_with("mmwt_token")


@pytest.mark.asyncio
async def test_require_worker_auth_rejects_missing_credentials_when_oidc_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing worker token and missing OIDC user should fail authentication."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    with pytest.raises(HTTPException) as exc_info:
        await _require_worker_auth(
            worker_token=None,
            service=AsyncMock(),
            user=None,
        )
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "worker_auth_failed"


@pytest.mark.asyncio
async def test_require_worker_auth_maps_invalid_token_to_401() -> None:
    """Invalid worker tokens should map to HTTP 401 instead of bubbling as 500."""

    mock_service = AsyncMock()
    mock_service.resolve_worker_token.side_effect = AgentQueueAuthenticationError(
        "invalid worker token"
    )

    with pytest.raises(HTTPException) as exc_info:
        await _require_worker_auth(
            worker_token="mmwt_invalid",
            service=mock_service,
            user=None,
        )

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "worker_auth_failed"


def test_heartbeat_job_includes_system_metadata(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Heartbeat responses should surface system metadata to workers."""

    test_client, service = client
    job = _build_job(status=models.AgentJobStatus.RUNNING)
    system = QueueSystemMetadata(
        workers_paused=True,
        mode="quiesce",
        reason="Short maintenance",
        version=4,
        requested_by_user_id=None,
        requested_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service.heartbeat.return_value = QueueSystemResponse(job=job, system=system)

    response = test_client.post(
        f"/api/queue/jobs/{job.id}/heartbeat",
        json={"workerId": "worker-1", "leaseSeconds": 60},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["system"]["workersPaused"] is True
    assert payload["system"]["mode"] == "quiesce"
    service.heartbeat.assert_awaited_once()


def test_update_job_runtime_state_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Runtime-state endpoint should forward worker-owned checkpoint payloads."""

    test_client, service = client
    job = _build_job(status=models.AgentJobStatus.RUNNING)
    job.payload = {
        "targetRuntime": "jules",
        "runtimeState": {
            "runtime": "jules",
            "externalTaskId": "task-123",
            "status": "running",
        },
    }
    service.update_runtime_state.return_value = job

    response = test_client.post(
        f"/api/queue/jobs/{job.id}/runtime-state",
        json={
            "workerId": "worker-1",
            "runtimeState": {
                "runtime": "jules",
                "externalTaskId": "task-123",
                "status": "running",
            },
        },
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["payload"]["runtimeState"]["externalTaskId"] == "task-123"
    service.update_runtime_state.assert_awaited_once_with(
        job_id=job.id,
        worker_id="worker-1",
        runtime_state={
            "runtime": "jules",
            "externalTaskId": "task-123",
            "status": "running",
        },
    )


def test_update_job_runtime_state_worker_mismatch_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Runtime-state endpoint should reject worker ids that mismatch token policy."""

    test_client, service = client
    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/runtime-state",
        json={
            "workerId": "worker-2",
            "runtimeState": {"runtime": "jules", "externalTaskId": "task-999"},
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "worker_not_authorized"
    service.update_runtime_state.assert_not_awaited()


def test_claim_job_worker_mismatch_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Claim should reject worker ids that do not match token policy."""

    test_client, service = client
    response = test_client.post(
        "/api/queue/jobs/claim",
        json={"workerId": "worker-2", "leaseSeconds": 60},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "worker_not_authorized"
    service.claim_job.assert_not_awaited()


def test_get_job_not_found_returns_404(client: tuple[TestClient, AsyncMock]) -> None:
    """GET /jobs/{id} should return 404 when service returns no job."""

    test_client, service = client
    job_id = uuid4()
    service.get_job.return_value = None

    response = test_client.get(f"/api/queue/jobs/{job_id}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_create_job_live_session_success(client: tuple[TestClient, AsyncMock]) -> None:
    """POST /jobs/{id}/live-session should create session tracking state."""

    test_client, service = client
    job_id = uuid4()
    service.create_live_session.return_value = _build_live_session(
        task_run_id=job_id,
        status=models.AgentJobLiveSessionStatus.STARTING,
    )

    response = test_client.post(f"/api/queue/jobs/{job_id}/live-session", json={})

    assert response.status_code == 200
    assert response.json()["session"]["taskRunId"] == str(job_id)
    assert response.json()["session"]["status"] == "starting"
    service.create_live_session.assert_awaited_once()


def test_get_job_live_session_not_found_maps_404(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """GET /jobs/{id}/live-session should map missing session to 404."""

    test_client, service = client
    job_id = uuid4()
    service.get_live_session.return_value = None

    response = test_client.get(f"/api/queue/jobs/{job_id}/live-session")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "live_session_not_found"


def test_get_job_live_session_unauthorized_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """GET /jobs/{id}/live-session should map ownership check failures to 403."""

    test_client, service = client
    service.get_live_session.side_effect = AgentQueueJobAuthorizationError("denied")

    response = test_client.get(f"/api/queue/jobs/{uuid4()}/live-session")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "job_not_authorized"


def test_grant_job_live_session_write_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """POST grant-write alias should return RW attach details."""

    test_client, service = client
    job_id = uuid4()
    live = _build_live_session(task_run_id=job_id)
    service.grant_live_session_write.return_value = SimpleNamespace(
        session=live,
        attach_rw="ssh rw",
        web_rw="https://web-rw",
        granted_until=datetime.now(UTC),
    )

    response = test_client.post(
        f"/api/queue/jobs/{job_id}/live-session/grant-write",
        json={"ttlMinutes": 15},
    )

    assert response.status_code == 200
    assert response.json()["attachRw"] == "ssh rw"
    service.grant_live_session_write.assert_awaited_once()


def test_apply_job_control_action_success(client: tuple[TestClient, AsyncMock]) -> None:
    """POST /jobs/{id}/control should apply action and return updated job."""

    test_client, service = client
    job_id = uuid4()
    service.apply_control_action.return_value = _build_job(
        status=models.AgentJobStatus.RUNNING
    )

    response = test_client.post(
        f"/api/queue/jobs/{job_id}/control",
        json={"action": "pause"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    service.apply_control_action.assert_awaited_once()


def test_append_job_operator_message_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """POST /jobs/{id}/operator-messages should append one control event."""

    test_client, service = client
    job_id = uuid4()
    service.append_operator_message.return_value = _build_control_event(job_id)

    response = test_client.post(
        f"/api/queue/jobs/{job_id}/operator-messages",
        json={"message": "Please continue with logs"},
    )

    assert response.status_code == 201
    assert response.json()["taskRunId"] == str(job_id)
    assert response.json()["action"] == "send_message"
    service.append_operator_message.assert_awaited_once()


def test_migration_telemetry_returns_summary(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Migration telemetry endpoint should return aggregated rollout metrics."""

    test_client, service = client
    service.get_migration_telemetry.return_value = SimpleNamespace(
        generated_at=datetime.now(UTC),
        window_hours=168,
        total_jobs=12,
        events_truncated=False,
        job_volume_by_type={"task": 9, "codex_exec": 3},
        failure_counts_by_runtime_stage=[
            {"runtime": "codex", "stage": "execute", "count": 2}
        ],
        publish_outcomes={
            "requested": 5,
            "published": 3,
            "skipped": 1,
            "failed": 1,
            "unknown": 0,
            "publishedRate": 0.6,
            "skippedRate": 0.2,
            "failedRate": 0.2,
        },
    )

    response = test_client.get("/api/queue/telemetry/migration?windowHours=168")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalJobs"] == 12
    assert payload["eventsTruncated"] is False
    assert payload["jobVolumeByType"]["task"] == 9
    assert payload["publishOutcomes"]["publishedRate"] == 0.6


def test_queue_safeguards_endpoint_requires_operator(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Safeguard telemetry endpoint should be operator-only."""

    test_client, service = client

    response = test_client.get("/api/queue/telemetry/safeguards")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operator_role_required"
    service.get_queue_safeguard_snapshot.assert_not_awaited()


def test_queue_safeguards_endpoint_operator_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Operator should receive safeguard telemetry payload."""

    test_client, service = client
    test_client.app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=uuid4(),
        email="ops@example.com",
        is_active=True,
        is_superuser=True,
    )
    job = _build_job(status=models.AgentJobStatus.RUNNING)
    snapshot = QueueSafeguardSnapshot(
        generated_at=datetime.now(UTC),
        max_runtime_seconds=60,
        stale_lease_grace_seconds=30,
        timed_out=(
            QueueSafeguardJob(
                job=job,
                runtime_seconds=120,
                lease_overdue_seconds=None,
            ),
        ),
        stale=(),
    )
    service.get_queue_safeguard_snapshot.return_value = snapshot

    response = test_client.get("/api/queue/telemetry/safeguards")

    assert response.status_code == 200
    body = response.json()
    assert body["maxRuntimeSeconds"] == 60
    assert body["timedOut"][0]["id"] == str(job.id)
    service.get_queue_safeguard_snapshot.assert_awaited_once()


def test_complete_job_state_conflict_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Complete endpoint should map state errors to HTTP 409."""

    test_client, service = client
    service.complete_job.side_effect = AgentJobStateError("invalid transition")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/complete",
        json={"workerId": "worker-1", "resultSummary": "done"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_state_conflict"


def test_complete_job_forwards_finish_metadata(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Complete endpoint should forward finish metadata to service layer."""

    test_client, service = client
    job = _build_job(status=models.AgentJobStatus.SUCCEEDED)
    service.complete_job.return_value = job

    response = test_client.post(
        f"/api/queue/jobs/{job.id}/complete",
        json={
            "workerId": "worker-1",
            "resultSummary": "done",
            "finishOutcomeCode": "NO_CHANGES",
            "finishOutcomeStage": "publish",
            "finishOutcomeReason": "publish skipped: no local changes",
            "finishSummary": {"schemaVersion": "v1"},
        },
    )

    assert response.status_code == 200
    kwargs = service.complete_job.await_args.kwargs
    assert kwargs["finish_outcome_code"] == "NO_CHANGES"
    assert kwargs["finish_outcome_stage"] == "publish"
    assert kwargs["finish_outcome_reason"] == "publish skipped: no local changes"
    assert kwargs["finish_summary"] == {"schemaVersion": "v1"}


def test_heartbeat_ownership_conflict_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Heartbeat endpoint should map ownership mismatches to HTTP 409."""

    test_client, service = client
    service.heartbeat.side_effect = AgentJobOwnershipError("owned by worker-2")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/heartbeat",
        json={"workerId": "worker-1", "leaseSeconds": 120},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_ownership_mismatch"


def test_list_jobs_rejects_invalid_status_filter(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Invalid list status query values should return HTTP 422."""

    test_client, service = client
    response = test_client.get("/api/queue/jobs?status=bad-status")

    assert response.status_code == 422
    service.list_jobs.assert_not_awaited()
    service.list_jobs_page.assert_not_awaited()


def test_list_jobs_with_summary_returns_compact_payload(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Summary list mode should return compact payloads and still expose task metadata."""

    test_client, service = client
    job = _build_job()
    job.payload = {
        "targetRuntime": "codex",
        "instruction": "This is a long instruction that should be trimmed for list responses.",
        "task": {
            "runtime": {"mode": "codex"},
            "skill": {"id": "speckit-run"},
            "publish": {"mode": "pr"},
            "instructions": "Task instructions for list rendering.",
        },
        "unrelated": {"data": "should_not_be_returned"},
    }
    service.list_jobs_page.return_value = QueueJobPage(
        items=(job,),
        page_size=50,
        next_cursor=None,
    )

    response = test_client.get("/api/queue/jobs?summary=true&limit=50")

    assert response.status_code == 200
    body = response.json()
    payload = body["items"][0]["payload"]
    assert payload["runtime"] == "codex"
    assert payload["task"]["runtime"]["mode"] == "codex"
    assert payload["task"]["skill"]["id"] == "speckit-run"
    assert payload["task"]["publish"]["mode"] == "pr"
    assert payload["task"]["instructions"] == "Task instructions for list rendering."
    assert (
        payload["instruction"]
        == "This is a long instruction that should be trimmed for list responses."
    )
    assert "unrelated" not in payload
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["hasMore"] is False
    assert body["page_size"] == 50
    assert body["next_cursor"] is None
    service.list_jobs_page.assert_awaited_once_with(
        status=None,
        job_type=None,
        limit=50,
        cursor=None,
    )


def test_list_jobs_omits_finish_summary_by_default(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Queue list payload should include outcome metadata but omit finish summary."""

    test_client, service = client
    job = _build_job()
    job.finish_outcome_code = "NO_CHANGES"
    job.finish_outcome_stage = "publish"
    job.finish_outcome_reason = "publish skipped: no local changes"
    job.finish_summary_json = {
        "schemaVersion": "v1",
        "finishOutcome": {"code": "NO_CHANGES"},
    }
    service.list_jobs_page.return_value = QueueJobPage(
        items=(job,),
        page_size=50,
        next_cursor=None,
    )

    response = test_client.get("/api/queue/jobs?limit=50")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["finishOutcomeCode"] == "NO_CHANGES"
    assert item["finishOutcomeStage"] == "publish"
    assert "finishSummary" not in item


def test_list_jobs_includes_has_more_and_offset_metadata(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """List responses should expose pagination metadata for queue dashboards."""

    test_client, service = client
    jobs = [_build_job() for _ in range(51)]
    service.list_jobs.return_value = jobs

    response = test_client.get("/api/queue/jobs?limit=50&offset=100")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 50
    assert body["offset"] == 100
    assert body["limit"] == 50
    assert body["hasMore"] is True
    assert body["page_size"] == 50
    assert body["next_cursor"] is None
    service.list_jobs.assert_awaited_once_with(
        status=None,
        job_type=None,
        limit=51,
        offset=100,
    )


def test_list_jobs_includes_cursor_metadata_for_default_path(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Cursor-based list path should surface page_size and next_cursor metadata."""

    test_client, service = client
    jobs = (_build_job(), _build_job())
    service.list_jobs_page.return_value = QueueJobPage(
        items=jobs,
        page_size=50,
        next_cursor="opaque-cursor",
    )

    response = test_client.get("/api/queue/jobs?limit=50")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["hasMore"] is True
    assert body["page_size"] == 50
    assert body["next_cursor"] == "opaque-cursor"
    service.list_jobs_page.assert_awaited_once_with(
        status=None,
        job_type=None,
        limit=50,
        cursor=None,
    )


def test_list_jobs_rejects_limit_above_max(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Requesting cursor list limits above the upper bound should fail validation."""

    test_client, service = client
    response = test_client.get("/api/queue/jobs?limit=999")

    assert response.status_code == 422
    service.list_jobs.assert_not_awaited()
    service.list_jobs_page.assert_not_awaited()


def test_list_jobs_forwards_cursor_token(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Cursor query value should be forwarded to cursor pagination service path."""

    test_client, service = client
    service.list_jobs_page.return_value = QueueJobPage(
        items=tuple(),
        page_size=50,
        next_cursor=None,
    )

    response = test_client.get("/api/queue/jobs?limit=50&cursor=abc123")

    assert response.status_code == 200
    service.list_jobs_page.assert_awaited_once_with(
        status=None,
        job_type=None,
        limit=50,
        cursor="abc123",
    )


def test_list_jobs_rejects_cursor_with_offset(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Offset compatibility path should not accept simultaneous cursor query args."""

    test_client, service = client
    response = test_client.get("/api/queue/jobs?cursor=abc&offset=50")

    assert response.status_code == 422
    service.list_jobs.assert_not_awaited()
    service.list_jobs_page.assert_not_awaited()


def test_list_jobs_rejects_cursor_with_zero_offset(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Explicitly provided zero-offset is still incompatible with cursor mode."""

    test_client, service = client
    response = test_client.get("/api/queue/jobs?cursor=abc&offset=0")

    assert response.status_code == 422
    service.list_jobs.assert_not_awaited()
    service.list_jobs_page.assert_not_awaited()


def test_list_jobs_invalid_cursor_maps_422(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Invalid cursor tokens should map to validation HTTP responses."""

    test_client, service = client
    service.list_jobs_page.side_effect = AgentQueueValidationError("cursor is invalid")

    response = test_client.get("/api/queue/jobs?cursor=not-valid")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_queue_payload"


def test_get_job_finish_summary_returns_json_payload(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Dedicated finish-summary endpoint should return stored summary JSON."""

    test_client, service = client
    job = _build_job()
    job.finish_summary_json = {
        "schemaVersion": "v1",
        "finishOutcome": {"code": "NO_CHANGES"},
    }
    service.get_job.return_value = job

    response = test_client.get(f"/api/queue/jobs/{job.id}/finish-summary")

    assert response.status_code == 200
    assert response.json()["schemaVersion"] == "v1"


def test_list_jobs_with_summary_preserves_legacy_publish_mode(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Summary list mode should retain top-level legacy publish mode fields."""

    test_client, service = client
    job = _build_job()
    job.payload = {
        "publish": {"mode": "pr"},
        "instruction": "legacy payload",
    }
    service.list_jobs_page.return_value = QueueJobPage(
        items=(job,),
        page_size=50,
        next_cursor=None,
    )

    response = test_client.get("/api/queue/jobs?summary=true&limit=50")

    assert response.status_code == 200
    payload = response.json()["items"][0]["payload"]
    assert payload["publish"]["mode"] == "pr"


def test_list_jobs_returns_manifest_metadata(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Manifest listings should surface sanitized payload metadata."""

    test_client, service = client
    job = _build_manifest_job()
    service.list_jobs_page.return_value = QueueJobPage(
        items=(job,),
        page_size=50,
        next_cursor=None,
    )

    response = test_client.get("/api/queue/jobs", params={"type": "manifest"})

    assert response.status_code == 200
    payload = response.json()["items"][0]["payload"]
    assert payload["manifestHash"] == "sha256:abc123"
    assert payload["requiredCapabilities"][0] == "manifest"
    assert "content" not in payload["manifest"]["source"]


def test_fail_job_validation_error_maps_422(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Fail endpoint should map validation failures to HTTP 422."""

    test_client, service = client
    service.fail_job.side_effect = AgentQueueValidationError("errorMessage required")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/fail",
        json={"workerId": "worker-1", "errorMessage": " "},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_queue_payload"


def test_cancel_job_success_maps_service_response(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Cancel endpoint should return serialized queue job response."""

    test_client, service = client
    job = _build_job(status=models.AgentJobStatus.CANCELLED)
    service.request_cancel.return_value = job

    response = test_client.post(
        f"/api/queue/jobs/{job.id}/cancel",
        json={"reason": "operator request"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    service.request_cancel.assert_awaited_once()


def test_recover_job_clone_success(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Recover endpoint should delegate to service and serialize both jobs."""

    test_client, service = client
    original = _build_job(status=models.AgentJobStatus.RUNNING)
    cloned = _build_job()
    service.recover_job.return_value = (original, cloned)

    response = test_client.post(
        f"/api/queue/jobs/{original.id}/recover",
        json={"mode": "clone"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recoveredJob"]["id"] == str(original.id)
    assert body["clonedJob"]["id"] == str(cloned.id)
    service.recover_job.assert_awaited_once_with(
        job_id=original.id,
        actor_user_id=ANY,
        actor_is_superuser=True,
        mode="clone",
    )


def test_ack_cancel_worker_mismatch_maps_403(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Worker identity mismatch on cancel ack should map to forbidden."""

    test_client, service = client
    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/cancel/ack",
        json={"workerId": "worker-2", "message": "stop"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "worker_not_authorized"
    service.ack_cancel.assert_not_awaited()


def test_ack_cancel_state_conflict_maps_409(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Cancel ack should map repository state conflicts to HTTP 409."""

    test_client, service = client
    service.ack_cancel.side_effect = AgentJobStateError("invalid cancel ack")

    response = test_client.post(
        f"/api/queue/jobs/{uuid4()}/cancel/ack",
        json={"workerId": "worker-1", "message": "stop"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_state_conflict"


def test_append_event_success(client: tuple[TestClient, AsyncMock]) -> None:
    """Event append endpoint should return serialized event payload."""

    test_client, service = client
    event = SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        level=models.AgentJobEventLevel.INFO,
        message="started",
        payload={"phase": "execute"},
        created_at=datetime.now(UTC),
    )
    service.append_event.return_value = event

    response = test_client.post(
        f"/api/queue/jobs/{event.job_id}/events",
        json={"workerId": "worker-1", "level": "info", "message": "started"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(event.id)
    assert body["jobId"] == str(event.job_id)


def test_list_job_events_success(client: tuple[TestClient, AsyncMock]) -> None:
    """Event list endpoint should return ordered event payloads."""

    test_client, service = client
    job_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        job_id=job_id,
        level=models.AgentJobEventLevel.INFO,
        message="progress",
        payload={"pct": 50},
        created_at=datetime.now(UTC),
    )
    service.list_events.return_value = [event]

    response = test_client.get(f"/api/queue/jobs/{job_id}/events?limit=50")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["message"] == "progress"


def test_list_job_events_forwards_composite_cursor(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Polling endpoint should forward after + afterEventId cursor fields."""

    test_client, service = client
    job_id = uuid4()
    after = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    after_event_id = uuid4()
    service.list_events.return_value = []

    response = test_client.get(
        f"/api/queue/jobs/{job_id}/events?limit=50&after={after}&afterEventId={after_event_id}"
    )

    assert response.status_code == 200
    service.list_events.assert_awaited_once()
    kwargs = service.list_events.await_args.kwargs
    assert kwargs["job_id"] == job_id
    assert kwargs["limit"] == 50
    assert kwargs["after_event_id"] == after_event_id
    assert kwargs["after"] is not None


def test_list_job_events_forwards_before_cursor_and_sort(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Event list endpoint should forward before + beforeEventId + sort."""

    test_client, service = client
    job_id = uuid4()
    before = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    before_event_id = uuid4()
    service.list_events.return_value = []

    response = test_client.get(
        f"/api/queue/jobs/{job_id}/events?limit=75&before={before}&beforeEventId={before_event_id}&sort=desc"
    )

    assert response.status_code == 200
    service.list_events.assert_awaited_once()
    kwargs = service.list_events.await_args.kwargs
    assert kwargs["job_id"] == job_id
    assert kwargs["limit"] == 75
    assert kwargs["before_event_id"] == before_event_id
    assert kwargs["before"] is not None
    assert kwargs["sort"] == "desc"


@pytest.mark.asyncio
async def test_stream_job_events_sse_emits_queue_event(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """SSE endpoint should emit serialized queue events."""

    _test_client, service = client
    job_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        job_id=job_id,
        level=models.AgentJobEventLevel.INFO,
        message="progress",
        payload={"pct": 25},
        created_at=datetime.now(UTC),
    )

    async def fake_list_events(*, job_id, limit, after, after_event_id):
        return [event] if after is None and after_event_id is None else []

    service.list_events.side_effect = fake_list_events

    class FakeRequest:
        def __init__(self) -> None:
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks > 2

    response = await stream_job_events(
        job_id=job_id,
        request=FakeRequest(),
        after=None,
        after_event_id=None,
        limit=200,
        poll_interval_ms=1000,
        service=service,
        _user=SimpleNamespace(id=uuid4()),
    )

    chunk = await anext(response.body_iterator)
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
    lines = text.splitlines()

    assert any(line == "event: queue_event" for line in lines)
    data_line = next(line[6:] for line in lines if line.startswith("data: "))
    payload = json.loads(data_line)
    assert payload["id"] == str(event.id)
    assert payload["jobId"] == str(job_id)
    if hasattr(response.body_iterator, "aclose"):
        await response.body_iterator.aclose()


def test_create_worker_token_success(client: tuple[TestClient, AsyncMock]) -> None:
    """Worker token create endpoint should return metadata + one-time token."""

    test_client, service = client
    token_record = SimpleNamespace(
        id=uuid4(),
        worker_id="executor-01",
        description="primary",
        allowed_repositories=["Moon/Mind"],
        allowed_job_types=["codex_exec"],
        capabilities=["codex"],
        is_active=True,
        created_at=datetime.now(UTC),
    )
    service.issue_worker_token.return_value = SimpleNamespace(
        raw_token="mmwt_secret",
        token_record=token_record,
    )

    response = test_client.post(
        "/api/queue/workers/tokens",
        json={"workerId": "executor-01", "allowedJobTypes": ["codex_exec"]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"] == "mmwt_secret"
    assert body["workerToken"]["workerId"] == "executor-01"
