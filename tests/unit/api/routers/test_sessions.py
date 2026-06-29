"""Unit tests for MM-1016 /api/sessions compatibility facades.

Source traceability: MM-977 chat-session API naming alignment.
Implementation traceability: MM-1031 approval elicitation resolution facade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
import pytest

from api_service.api.routers import sessions as sessions_router
from api_service.api.routers.sessions import router
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord


@pytest.fixture
def test_user() -> User:
    return User(id=uuid4(), email="test@example.com", is_superuser=True)


@pytest.fixture
def client(test_user: User) -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    artifact_service = AsyncMock()

    app.dependency_overrides[get_current_user()] = lambda: test_user
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: artifact_service

    with TestClient(app) as test_client:
        yield test_client, artifact_service

    app.dependency_overrides.clear()


def _enable_session_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sessions_router.settings.feature_flags,
        "session_api_compat_enabled",
        True,
    )


def _session_record(**overrides: object) -> CodexManagedSessionRecord:
    values = {
        "sessionId": "sess:mm:run-1:codex_cli",
        "sessionEpoch": 2,
        "agentRunId": "mm:run-1",
        "containerId": "ctr-1",
        "threadId": "thread-1",
        "runtimeId": "codex_cli",
        "imageRef": "ghcr.io/moonmind/session:latest",
        "controlUrl": "http://session-control",
        "status": "ready",
        "workspacePath": "/tmp/workspace",
        "sessionWorkspacePath": "/tmp/workspace/session",
        "artifactSpoolPath": "/tmp/workspace/artifacts",
        "latestSummaryRef": "artifact-summary",
        "latestCheckpointRef": "artifact-checkpoint",
        "latestControlEventRef": "artifact-control",
        "latestResetBoundaryRef": "artifact-reset",
        "startedAt": datetime(2026, 6, 1, tzinfo=UTC),
        "updatedAt": datetime(2026, 6, 1, 0, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return CodexManagedSessionRecord.model_validate(values)


def _projection() -> SimpleNamespace:
    return SimpleNamespace(
        agent_run_id="mm:run-1",
        session_id="sess:mm:run-1:codex_cli",
        session_epoch=2,
        grouped_artifacts=[],
        latest_summary_ref={"artifactId": "artifact-summary"},
        latest_checkpoint_ref={"artifactId": "artifact-checkpoint"},
        latest_control_event_ref={"artifactId": "artifact-control"},
        latest_reset_boundary_ref={"artifactId": "artifact-reset"},
    )


def test_session_aliases_are_disabled_by_default(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sessions_router.settings.feature_flags,
        "session_api_compat_enabled",
        False,
    )
    test_client, _ = client

    with patch("api_service.api.routers.sessions.ManagedSessionStore.load") as load:
        response = test_client.get("/api/sessions/sess-1")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_api_compat_disabled"
    load.assert_not_called()


def test_get_session_snapshot_returns_bounded_alias_contract(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client
    run_record = SimpleNamespace(workflow_id="mm:workflow-1", status="running")

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ) as load_session:
        with patch(
            "api_service.api.routers.sessions._build_agent_run_artifact_session_projection",
            new=AsyncMock(return_value=_projection()),
        ):
            with patch(
                "api_service.api.routers.sessions.ManagedRunStore.load",
                return_value=run_record,
            ):
                response = test_client.get("/api/sessions/sess%3Amm%3Arun-1%3Acodex_cli")

    assert response.status_code == 200
    load_session.assert_called_once_with("sess:mm:run-1:codex_cli")
    body = response.json()
    assert body["id"] == "sess:mm:run-1:codex_cli"
    assert body["agentRunId"] == "mm:run-1"
    assert body["workflowId"] == "mm:workflow-1"
    assert body["status"] == "running"
    assert body["sessionEpoch"] == 2
    assert body["interventionCapabilities"] == {
        "sendFollowUp": True,
        "clearSession": True,
        "interruptTurn": False,
        "cancelSession": False,
    }
    assert body["artifactRefs"]["latestSummaryRef"] == {
        "artifactId": "artifact-summary"
    }


def test_get_session_items_maps_events_and_artifact_refs(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client
    run_record = SimpleNamespace(workspace_path="/tmp/workspace")
    events = [
        {
            "sequence": 3,
            "kind": "assistant_message",
            "timestamp": "2026-06-01T00:00:03Z",
            "sessionEpoch": 2,
            "threadId": "thread-1",
            "text": "done",
        }
    ]

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.sessions.ManagedRunStore.load",
            return_value=run_record,
        ):
            with patch(
                "api_service.api.routers.sessions._load_agent_run_observability_events",
                return_value=(events, "artifacts"),
            ) as load_events:
                with patch(
                    "api_service.api.routers.sessions._build_agent_run_artifact_session_projection",
                    new=AsyncMock(return_value=_projection()),
                ):
                    response = test_client.get("/api/sessions/sess-1/items?since=1")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "artifacts"
    assert body["items"][0]["id"] == "event:3"
    assert body["items"][0]["kind"] == "assistant_message"
    assert {item["kind"] for item in body["items"][1:]} == {
        "latest_summary",
        "latest_checkpoint",
        "latest_control_event",
        "latest_reset_boundary",
    }
    assert load_events.call_args.kwargs["session_epochs"] == {2}


def test_session_stream_delegates_to_agent_run_live_logs(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    async def _stream(**kwargs):
        return Response("data: {}\n\n", media_type="text/event-stream")

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.sessions.stream_agent_run_live_logs",
            new=AsyncMock(side_effect=_stream),
        ) as stream:
            response = test_client.get("/api/sessions/sess-1/stream?since=10")

    assert response.status_code == 200
    assert response.text == "data: {}\n\n"
    assert stream.await_args.kwargs["id"] == "mm:run-1"
    assert stream.await_args.kwargs["since"] == 10


def test_session_stream_rejects_unsupported_format(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    response = test_client.get("/api/sessions/sess-1/stream?format=jsonl")

    assert response.status_code == 400
    assert "Unsupported session stream format" in response.json()["detail"]


def test_post_session_message_event_maps_to_existing_control_path(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client
    control_response = SimpleNamespace(action="send_follow_up", projection=_projection())

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.sessions.control_agent_run_artifact_session",
            new=AsyncMock(return_value=control_response),
        ) as control:
            response = test_client.post(
                "/api/sessions/sess-1/events",
                json={"type": "message", "message": "continue", "reason": "operator"},
            )

    assert response.status_code == 200
    assert response.json()["action"] == "send_follow_up"
    control_payload = control.await_args.kwargs["payload"]
    assert control.await_args.kwargs["agent_run_id"] == "mm:run-1"
    assert control_payload.action == "send_follow_up"
    assert control_payload.message == "continue"


def test_post_session_event_rejects_unknown_type(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        response = test_client.post(
            "/api/sessions/sess-1/events",
            json={"type": "retry_everything"},
        )

    assert response.status_code == 400
    assert "Unsupported session event type" in response.json()["detail"]


def test_resolve_elicitation_maps_approval_to_existing_control_path(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client
    control_response = SimpleNamespace(action="send_follow_up", projection=_projection())

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.sessions.control_agent_run_artifact_session",
            new=AsyncMock(return_value=control_response),
        ) as control:
            response = test_client.post(
                "/api/sessions/sess-1/elicitations/el-1/resolve",
                json={"decision": "approve"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "elicitation_resolution"
    assert body["elicitationId"] == "el-1"
    assert body["decision"] == "approve"
    assert body["action"] == "send_follow_up"
    assert body["projection"]["agent_run_id"] == "mm:run-1"
    control_payload = control.await_args.kwargs["payload"]
    assert control.await_args.kwargs["agent_run_id"] == "mm:run-1"
    assert control.await_args.kwargs["session_id"] == "sess:mm:run-1:codex_cli"
    assert control_payload.action == "send_follow_up"
    assert control_payload.message == "Approved."
    assert control_payload.reason == "session_elicitation:el-1:approve"


def test_resolve_elicitation_preserves_session_authorization(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.agent_runs._require_agent_run_access",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="no")),
        ):
            with patch(
                "api_service.api.routers.sessions.control_agent_run_artifact_session",
                new=AsyncMock(),
            ) as control:
                response = test_client.post(
                    "/api/sessions/sess-1/elicitations/el-1/resolve",
                    json={"decision": "approve"},
                )

    assert response.status_code == 403
    control.assert_not_awaited()


def test_terminal_session_suppresses_elicitation_resolution_capability(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(status="terminated"),
    ):
        response = test_client.post(
            "/api/sessions/sess-1/elicitations/el-1/resolve",
            json={"decision": "approve"},
        )

    assert response.status_code == 409
    assert "not available" in response.json()["detail"]


def test_resolve_elicitation_rejects_unsupported_capability(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(status="busy", activeTurnId="turn-1"),
    ):
        with patch(
            "api_service.api.routers.sessions.control_agent_run_artifact_session",
            new=AsyncMock(),
        ) as control:
            response = test_client.post(
                "/api/sessions/sess-1/elicitations/el-1/resolve",
                json={"decision": "approve"},
            )

    assert response.status_code == 409
    assert "not available" in response.json()["detail"]
    control.assert_not_awaited()


def test_resolve_elicitation_rejects_unsupported_decision(
    client: tuple[TestClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_api(monkeypatch)
    test_client, _ = client

    with patch(
        "api_service.api.routers.sessions.ManagedSessionStore.load",
        return_value=_session_record(),
    ):
        with patch(
            "api_service.api.routers.sessions.control_agent_run_artifact_session",
            new=AsyncMock(),
        ) as control:
            response = test_client.post(
                "/api/sessions/sess-1/elicitations/el-1/resolve",
                json={"decision": "maybe"},
            )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_elicitation_resolution"
    control.assert_not_awaited()
