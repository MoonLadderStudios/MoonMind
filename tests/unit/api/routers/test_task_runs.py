"""Unit tests for task-run live session API router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.agent_queue import _WorkerRequestAuth
from api_service.api.routers.task_runs import _get_service, _require_worker_auth, router
from api_service.auth_providers import get_current_user
from moonmind.workflows.agent_queue import models


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


def _build_job():
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        type="task",
        status=models.AgentJobStatus.RUNNING,
        priority=0,
        payload={"liveControl": {"paused": False}},
        created_by_user_id=None,
        requested_by_user_id=None,
        cancel_requested_by_user_id=None,
        cancel_requested_at=None,
        cancel_reason=None,
        affinity_key=None,
        claimed_by="worker-1",
        lease_expires_at=None,
        next_attempt_at=None,
        attempt=1,
        max_attempts=1,
        result_summary=None,
        error_message=None,
        artifacts_path=None,
        started_at=now,
        finished_at=None,
        created_at=now,
        updated_at=now,
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


def _override_user_dependencies(app: FastAPI, user: object) -> None:
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
        app.dependency_overrides[dependency] = lambda user=user: user


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[_require_worker_auth] = lambda: _WorkerRequestAuth(
        auth_source="worker_token",
        worker_id="worker-1",
        allowed_repositories=(),
        allowed_job_types=(),
        capabilities=("codex",),
    )
    user = SimpleNamespace(id=uuid4(), email="task-runs@example.com", is_active=True)
    _override_user_dependencies(app, user)

    with TestClient(app) as test_client:
        yield test_client, service
    app.dependency_overrides.clear()


def test_create_live_session_success(client: tuple[TestClient, AsyncMock]) -> None:
    test_client, service = client
    task_run_id = uuid4()
    service.create_live_session.return_value = _build_live_session(
        task_run_id=task_run_id,
        status=models.AgentJobLiveSessionStatus.STARTING,
    )

    response = test_client.post(f"/api/task-runs/{task_run_id}/live-session", json={})

    assert response.status_code == 200
    assert response.json()["session"]["taskRunId"] == str(task_run_id)
    assert response.json()["session"]["status"] == "starting"
    service.create_live_session.assert_awaited_once()


def test_get_live_session_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock]
) -> None:
    test_client, service = client
    task_run_id = uuid4()
    service.get_live_session.return_value = None

    response = test_client.get(f"/api/task-runs/{task_run_id}/live-session")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "live_session_not_found"


def test_grant_live_session_write_success(
    client: tuple[TestClient, AsyncMock]
) -> None:
    test_client, service = client
    task_run_id = uuid4()
    live = _build_live_session(task_run_id=task_run_id)
    service.grant_live_session_write.return_value = SimpleNamespace(
        session=live,
        attach_rw="ssh rw",
        web_rw="https://web-rw",
        granted_until=datetime.now(UTC),
    )

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/grant-write",
        json={"ttlMinutes": 15},
    )

    assert response.status_code == 200
    assert response.json()["attachRw"] == "ssh rw"
    service.grant_live_session_write.assert_awaited_once()


def test_apply_control_action_success(client: tuple[TestClient, AsyncMock]) -> None:
    test_client, service = client
    task_run_id = uuid4()
    service.apply_control_action.return_value = _build_job()

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/control",
        json={"action": "pause"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    service.apply_control_action.assert_awaited_once()


def test_operator_message_returns_control_event(
    client: tuple[TestClient, AsyncMock]
) -> None:
    test_client, service = client
    task_run_id = uuid4()
    service.append_operator_message.return_value = _build_control_event(task_run_id)

    response = test_client.post(
        f"/api/task-runs/{task_run_id}/operator-messages",
        json={"message": "Please rerun with verbose logs."},
    )

    assert response.status_code == 201
    assert response.json()["taskRunId"] == str(task_run_id)
    assert response.json()["action"] == "send_message"
    service.append_operator_message.assert_awaited_once()


def test_worker_report_rejects_worker_id_mismatch(
    client: tuple[TestClient, AsyncMock]
) -> None:
    test_client, service = client
    task_run_id = uuid4()
    response = test_client.post(
        f"/api/task-runs/{task_run_id}/live-session/report",
        json={"workerId": "worker-2", "status": "starting"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "worker_not_authorized"
    service.report_live_session.assert_not_awaited()
