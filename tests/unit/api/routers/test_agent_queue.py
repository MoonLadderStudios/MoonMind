"""Unit tests for the agent queue API router."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
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
    AgentJobOwnershipError,
    AgentJobStateError,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueJobAuthorizationError,
    AgentQueueValidationError,
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
        artifacts_path=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
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

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="queue-tester@example.com",
        is_active=True,
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
    assert response.json()["detail"]["code"] == "invalid_queue_payload"


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
    with pytest.raises(AgentQueueAuthenticationError):
        await _require_worker_auth(
            worker_token=None,
            service=AsyncMock(),
            user=None,
        )


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
    service.list_jobs.return_value = [job]

    response = test_client.get("/api/queue/jobs?summary=true&limit=50")

    assert response.status_code == 200
    payload = response.json()["items"][0]["payload"]
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
    service.list_jobs.return_value = [job]

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
    service.list_jobs.return_value = [job]

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
