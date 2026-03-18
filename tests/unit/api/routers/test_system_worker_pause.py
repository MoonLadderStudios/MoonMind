"""Unit tests for worker pause system router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from api_service.api.routers.system_worker_pause import (
    _CURRENT_USER,
    _get_service,
    router,
)
from api_service.auth import _DEFAULT_USER_ID
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models as aq_models
from moonmind.workflows.agent_queue.service import (
    AgentQueueValidationError,
    QueueSystemMetadata,
    WorkerPauseAuditEvent,
    WorkerPauseMetrics,
    WorkerPauseSnapshot,
)

pytestmark = [pytest.mark.asyncio]


def _build_snapshot(
    paused: bool = False,
    running: int = 0,
    queued: int = 3,
    stale_running: int = 0,
    metrics_source: str = "temporal",
    mode=None,
    audit_action: str | None = None,
    audit_mode=None,  # accepts WorkerPauseMode or None
    actor_user_id: UUID | None = None,
) -> WorkerPauseSnapshot:
    now = datetime.now(UTC)
    action = audit_action or ("pause" if paused else "resume")
    return WorkerPauseSnapshot(
        system=QueueSystemMetadata(
            workers_paused=paused,
            mode=mode,
            reason="maintenance" if paused else None,
            version=2,
            requested_by_user_id=None,
            requested_at=now if paused else None,
            updated_at=now,
        ),
        metrics=WorkerPauseMetrics(
            queued=queued,
            running=running,
            stale_running=stale_running,
            metrics_source=metrics_source,
        ),
        audit_events=(
            WorkerPauseAuditEvent(
                id=uuid4(),
                action=action,
                mode=audit_mode or mode,
                reason="maintenance",
                actor_user_id=actor_user_id or uuid4(),
                created_at=now,
            ),
        ),
    )


@pytest.fixture
def client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="tester@example.com",
        is_superuser=True,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[_CURRENT_USER] = lambda: mock_user

    with TestClient(app) as test_client:
        yield test_client, mock_service


def test_get_worker_pause_snapshot_returns_payload(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """GET endpoint should return serialized snapshot."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(paused=True)

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["system"]["workersPaused"] is True
    assert body["metrics"]["queued"] == 3
    assert body["audit"]["latest"][0]["action"] == "pause"
    service.get_worker_pause_snapshot.assert_awaited_once()


def test_apply_worker_pause_state_success(client: tuple[TestClient, AsyncMock]) -> None:
    """POST endpoint should delegate to service and return snapshot."""

    test_client, service = client
    service.apply_worker_pause_action.return_value = _build_snapshot(paused=False)

    response = test_client.post(
        "/api/system/worker-pause",
        json={"action": "resume", "reason": "done", "forceResume": True},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["system"]["workersPaused"] is False
    service.apply_worker_pause_action.assert_awaited_once()
    called = service.apply_worker_pause_action.await_args.kwargs
    assert called["action"] == "resume"
    assert called["reason"] == "done"
    assert called["force_resume"] is True


def test_apply_worker_pause_state_validation_error(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Validation errors should map to HTTP 400 with detail."""

    test_client, service = client
    service.apply_worker_pause_action.side_effect = AgentQueueValidationError(
        "already paused"
    )

    response = test_client.post(
        "/api/system/worker-pause",
        json={"action": "pause", "mode": "drain", "reason": "upgrade"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    detail = response.json()["detail"]
    assert detail["code"] == "worker_pause_invalid_request"


def test_apply_worker_pause_state_requires_operator_role(monkeypatch) -> None:
    """Non-superusers should not be allowed to toggle global worker pause."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()

    mock_user = SimpleNamespace(
        id=uuid4(), email="user@example.com", is_superuser=False
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[_CURRENT_USER] = lambda: mock_user

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/system/worker-pause",
            json={"action": "resume", "reason": "done", "forceResume": True},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "worker_pause_forbidden"


def test_apply_worker_pause_state_requires_actor_identity(monkeypatch) -> None:
    """Missing actor id should be rejected to keep audit trails complete."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()

    mock_user = SimpleNamespace(id=None, email="user@example.com", is_superuser=True)
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[_CURRENT_USER] = lambda: mock_user

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/system/worker-pause",
            json={"action": "resume", "reason": "done", "forceResume": True},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "worker_pause_actor_missing"


def test_disabled_auth_allows_non_superuser_pause_control(monkeypatch) -> None:
    """Disabled auth mode should not require superuser for pause controls."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", None, raising=False)
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()

    mock_user = SimpleNamespace(id=None, email="user@example.com", is_superuser=False)
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[_CURRENT_USER] = lambda: mock_user

    with TestClient(app) as test_client:
        mock_service.apply_worker_pause_action.return_value = _build_snapshot(
            paused=False
        )
        response = test_client.post(
            "/api/system/worker-pause",
            json={"action": "resume", "reason": "done", "forceResume": True},
        )

    assert response.status_code == status.HTTP_200_OK
    called = mock_service.apply_worker_pause_action.await_args.kwargs
    assert called["actor_user_id"] == UUID(
        settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
    )


# ---- T007: Temporal Visibility metrics integration (DOC-REQ-007, FR-003) ----


def test_response_includes_metrics_source_temporal(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """GET should expose metricsSource='temporal' when Visibility API is used."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True, metrics_source="temporal"
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metrics"]["metricsSource"] == "temporal"


def test_response_includes_metrics_source_legacy(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """GET should expose metricsSource='legacy' when falling back to queue table."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True, metrics_source="legacy"
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metrics"]["metricsSource"] == "legacy"


# ---- T008: isDrained when Temporal reports 0 running (DOC-REQ-002, FR-003) ----


def test_is_drained_true_when_zero_running(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """isDrained should be true when Temporal reports 0 running and 0 stale."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True, running=0, stale_running=0
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metrics"]["isDrained"] is True
    assert body["metrics"]["running"] == 0
    assert body["metrics"]["staleRunning"] == 0


def test_is_drained_false_when_workflows_running(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """isDrained should be false when there are still running workflows."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True, running=5
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metrics"]["isDrained"] is False
    assert body["metrics"]["running"] == 5


def test_is_drained_false_when_stale_workflows(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """isDrained should be false when there are stale running workflows."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True, running=0, stale_running=2
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metrics"]["isDrained"] is False
    assert body["metrics"]["staleRunning"] == 2


# ---- T009: Audit trail completeness (DOC-REQ-010, FR-002) ----


def test_audit_trail_includes_actor_reason_mode_timestamps(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Audit events should include actor, reason, mode, and timestamps."""

    actor_id = uuid4()
    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=True,
        mode=aq_models.WorkerPauseMode.DRAIN,
        audit_action="pause",
        audit_mode=aq_models.WorkerPauseMode.DRAIN,
        actor_user_id=actor_id,
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    audit_events = response.json()["audit"]["latest"]
    assert len(audit_events) >= 1

    event = audit_events[0]
    assert event["action"] == "pause"
    assert event["mode"] == "drain"
    assert event["reason"] is not None
    assert event["actorUserId"] == str(actor_id)
    assert event["createdAt"] is not None


def test_audit_trail_resume_event_valid(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """Resume audit events should be valid with mode=None."""

    test_client, service = client
    service.get_worker_pause_snapshot.return_value = _build_snapshot(
        paused=False, audit_action="resume"
    )

    response = test_client.get("/api/system/worker-pause")

    assert response.status_code == status.HTTP_200_OK
    event = response.json()["audit"]["latest"][0]
    assert event["action"] == "resume"


# ---- T012: Batch Signal dispatch on quiesce (DOC-REQ-003, FR-007, FR-010) ----


def test_quiesce_pause_delegates_to_service(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """POST with action=pause, mode=quiesce should be accepted."""

    test_client, service = client
    service.apply_worker_pause_action.return_value = _build_snapshot(
        paused=True, mode=aq_models.WorkerPauseMode.QUIESCE
    )

    response = test_client.post(
        "/api/system/worker-pause",
        json={"action": "pause", "mode": "quiesce", "reason": "graceful shutdown"},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["system"]["workersPaused"] is True
    called = service.apply_worker_pause_action.await_args.kwargs
    assert called["action"] == "pause"
    assert called["mode"] == "quiesce"


def test_quiesce_resume_delegates_to_service(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """POST with action=resume should be accepted after quiesce pause."""

    test_client, service = client
    service.apply_worker_pause_action.return_value = _build_snapshot(paused=False)

    response = test_client.post(
        "/api/system/worker-pause",
        json={"action": "resume", "reason": "upgrade done", "forceResume": True},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["system"]["workersPaused"] is False
    service.apply_worker_pause_action.assert_awaited_once()
