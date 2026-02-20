"""Unit tests for worker pause system router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from api_service.api.routers.system_worker_pause import (
    _get_service,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.workflows.agent_queue.service import (
    AgentQueueValidationError,
    QueueSystemMetadata,
    WorkerPauseAuditEvent,
    WorkerPauseMetrics,
    WorkerPauseSnapshot,
)
from uuid import uuid4

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


def _build_snapshot(paused: bool = False) -> WorkerPauseSnapshot:
    now = datetime.now(UTC)
    return WorkerPauseSnapshot(
        system=QueueSystemMetadata(
            workers_paused=paused,
            mode=None,
            reason="maintenance" if paused else None,
            version=2,
            requested_by_user_id=None,
            requested_at=now if paused else None,
            updated_at=now,
        ),
        metrics=WorkerPauseMetrics(queued=3, running=0, stale_running=0),
        audit_events=(
            WorkerPauseAuditEvent(
                id=uuid4(),
                action="pause" if paused else "resume",
                mode=None,
                reason="maintenance",
                actor_user_id=None,
                created_at=now,
            ),
        ),
    )


@pytest.fixture
def client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()

    mock_user = SimpleNamespace(id=uuid4(), email="tester@example.com")
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_current_user()] = lambda: mock_user

    with TestClient(app) as test_client:
        yield test_client, mock_service


def test_get_worker_pause_snapshot_returns_payload(
    client: tuple[TestClient, AsyncMock]
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
    client: tuple[TestClient, AsyncMock]
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
