"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    _serialize_execution,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from moonmind.config.settings import settings
from moonmind.workflows.temporal import TemporalExecutionValidationError


def _override_user_dependencies(app: FastAPI, *, is_superuser: bool) -> SimpleNamespace:
    mock_user = SimpleNamespace(
        id=uuid4(),
        email="executions@example.com",
        is_active=True,
        is_superuser=is_superuser,
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
    def _current_user() -> SimpleNamespace:
        return mock_user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user
    return mock_user


def _build_execution_record(
    *,
    workflow_type: TemporalWorkflowType = TemporalWorkflowType.RUN,
    state: MoonMindWorkflowState = MoonMindWorkflowState.EXECUTING,
    owner_id: str = "user-123",
) -> SimpleNamespace:
    now = datetime.now(UTC)
    entry = (
        "manifest" if workflow_type is TemporalWorkflowType.MANIFEST_INGEST else "run"
    )
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:wf-1",
        run_id="run-2",
        workflow_type=workflow_type,
        state=state,
        close_status=None,
        search_attributes={
            "mm_owner_id": owner_id,
            "mm_owner_type": "user" if owner_id != "system" else "system",
            "mm_entry": entry,
            "mm_repo": "Moon/Mind",
            "mm_continue_as_new_cause": "manual_rerun",
        },
        memo={
            "title": "Temporal task",
            "summary": "Waiting on review.",
            "continue_as_new_cause": "manual_rerun",
            "latest_temporal_run_id": "run-2",
        },
        artifact_refs=["art_123"],
        manifest_ref=(
            "art_manifest_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        plan_ref=(
            "art_plan_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        parameters=(
            {
                "requestedBy": {"type": "user", "id": "user-1"},
                "executionPolicy": {
                    "failurePolicy": "best_effort",
                    "maxConcurrency": 3,
                },
                "manifestNodes": [
                    {"nodeId": "node-a", "state": "ready"},
                    {"nodeId": "node-b", "state": "running"},
                ],
            }
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else {}
        ),
        paused=False,
        waiting_reason=None,
        attention_required=False,
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id=owner_id,
        owner_type="user" if owner_id != "system" else "system",
        entry=entry,
        integration_state=None,
    )


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        yield test_client, service, user

    app.dependency_overrides.clear()


def _client_with_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        yield test_client, mock_service

    app.dependency_overrides.clear()


def test_list_executions_passes_temporal_filters_for_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    owner_id = uuid4()
    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "workflowType": "MoonMind.Run",
                "state": "executing",
                "entry": "run",
                "ownerType": "user",
                "ownerId": str(owner_id),
                "repo": "Moon/Mind",
                "integration": "github",
                "pageSize": 25,
                "nextPageToken": "token-123",
            },
        )

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["workflow_type"] == "MoonMind.Run"
    assert kwargs["state"] == "executing"
    assert kwargs["entry"] == "run"
    assert kwargs["owner_type"] == "user"
    assert kwargs["owner_id"] == str(owner_id)
    assert kwargs["repo"] == "Moon/Mind"
    assert kwargs["integration"] == "github"
    assert kwargs["page_size"] == 25
    assert kwargs["next_page_token"] == "token-123"


def test_list_executions_rejects_non_admin_owner_type_override() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "system"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "execution_forbidden"
    mock_service.list_executions.assert_not_awaited()


def test_list_executions_uses_owner_id_without_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions")

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] is None


def test_list_executions_allows_explicit_user_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "user"})

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] == "user"


def test_create_task_shaped_execution_rejects_invalid_required_capabilities() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "requiredCapabilities": 1,
                    "task": {
                        "instructions": "Ship the Temporal integration.",
                    },
                },
            },
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "payload.requiredCapabilities must be a JSON array of strings."
    )
    mock_service.create_execution.assert_not_awaited()


def test_create_execution_surfaces_domain_validation_errors(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported workflow type: MoonMind.Unknown"
    )

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Unknown"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"


def test_create_execution_routes_directly_to_temporal(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    record = _build_execution_record()
    record.memo["title"] = "Test direct temporal"
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Run", "title": "Test direct temporal"},
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Test direct temporal"
    service.create_execution.assert_awaited_once()


def test_create_execution_enforces_idempotency(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Run", "idempotencyKey": "idem-123"},
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["idempotency_key"] == "idem-123"


def test_list_executions_rejects_non_admin_cross_owner_queries(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.get("/api/executions", params={"ownerId": str(uuid4())})

    assert response.status_code == 403
    assert (
        response.json()["detail"]["message"]
        == "Cannot list executions for another user."
    )
    service.list_executions.assert_not_awaited()


def test_describe_execution_hides_foreign_workflow_visibility(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(
        owner_id=str(uuid4()),
        workflow_id="mm:foreign",
    )

    response = test_client.get("/api/executions/mm:foreign")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"
    assert str(user.id) != service.describe_execution.return_value.owner_id


def test_update_execution_invalid_update_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.update_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported update name: UnknownUpdate"
    )

    response = test_client.post(
        "/api/executions/mm:test/update",
        json={"updateName": "UnknownUpdate"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_update_request"


def test_signal_execution_invalid_signal_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.signal_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported signal name: UnknownSignal"
    )

    response = test_client.post(
        "/api/executions/mm:test/signal",
        json={"signalName": "UnknownSignal"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "signal_rejected"


def test_serialize_execution_treats_system_owner_id_as_system_owner_type() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="system",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.INITIALIZING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        started_at="2026-03-06T00:00:00Z",
        updated_at="2026-03-06T00:00:00Z",
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.owner_type == "system"
    assert payload.owner_id == "system"


def test_describe_execution_exposes_task_and_temporal_run_identity() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowId"] == "mm:wf-1"
        assert payload["taskId"] == "mm:wf-1"
        assert payload["runId"] == "run-2"
        assert payload["temporalRunId"] == "run-2"
        assert payload["latestRunView"] is True
        assert payload["continueAsNewCause"] == "manual_rerun"


def test_request_rerun_update_response_includes_continue_as_new_cause() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
            "continue_as_new_cause": "manual_rerun",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
            },
        )

        assert response.status_code == 200
        assert response.json()["continueAsNewCause"] == "manual_rerun"


def test_list_executions_preserves_logical_identity_fields() -> None:
    for test_client, service in _client_with_service():
        service.list_executions.return_value = SimpleNamespace(
            items=[_build_execution_record()],
            next_page_token="cursor-1",
            count=1,
        )

        response = test_client.get("/api/executions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["nextPageToken"] == "cursor-1"
        item = payload["items"][0]
        assert item["workflowId"] == "mm:wf-1"
        assert item["taskId"] == "mm:wf-1"
        assert item["runId"] == "run-2"
        assert item["temporalRunId"] == "run-2"
        assert item["latestRunView"] is True
        assert item["continueAsNewCause"] == "manual_rerun"


def test_describe_manifest_execution_exposes_bounded_manifest_fields() -> None:
    """Manifest ingest detail should expose refs, policy, and bounded counts."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowType"] == "MoonMind.ManifestIngest"
        assert payload["manifestArtifactRef"] == "art_manifest_1"
        assert payload["planArtifactRef"] == "art_plan_1"
        assert payload["executionPolicy"]["maxConcurrency"] == 3
        assert payload["counts"]["ready"] == 1
        assert payload["counts"]["running"] == 1


def test_manifest_update_route_passes_manifest_specific_fields() -> None:
    """Manifest-specific update requests should be forwarded unchanged to the service."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "next_safe_point",
            "message": "Manifest update accepted and will be applied at the next safe point.",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "UpdateManifest",
                "newManifestArtifactRef": "art_manifest_2",
                "mode": "REPLACE_FUTURE",
                "idempotencyKey": "manifest-update-1",
            },
        )

        assert response.status_code == 200
        called = service.update_execution.await_args.kwargs
        assert called["update_name"] == "UpdateManifest"
        assert called["new_manifest_artifact_ref"] == "art_manifest_2"
        assert called["mode"] == "REPLACE_FUTURE"


def test_manifest_status_route_returns_bounded_snapshot() -> None:
    """Manifest status route should return the service snapshot unchanged."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.describe_manifest_status.return_value = {
            "workflowId": "mm:wf-1",
            "state": "executing",
            "phase": "executing",
            "paused": False,
            "maxConcurrency": 3,
            "failurePolicy": "best_effort",
            "counts": {
                "pending": 0,
                "ready": 1,
                "running": 1,
                "succeeded": 0,
                "failed": 0,
                "canceled": 0,
            },
        }

        response = test_client.get("/api/executions/mm:wf-1/manifest-status")

        assert response.status_code == 200
        assert response.json()["counts"]["running"] == 1


def test_manifest_nodes_route_returns_page_payload() -> None:
    """Manifest node page route should preserve cursor and count fields."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.list_manifest_nodes.return_value = {
            "items": [
                {
                    "nodeId": "node-b",
                    "state": "running",
                    "workflowType": "MoonMind.Run",
                }
            ],
            "nextCursor": "cursor-1",
            "count": 1,
        }

        response = test_client.get(
            "/api/executions/mm:wf-1/manifest-nodes",
            params={"state": "running", "limit": 25},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["nextCursor"] == "cursor-1"
        assert body["items"][0]["nodeId"] == "node-b"
        assert body["items"][0]["workflowType"] == "MoonMind.Run"


def test_describe_execution_includes_actions_and_debug_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.AWAITING_EXTERNAL
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["dashboardStatus"] == "awaiting_action"
    assert body["waitingReason"] == "Waiting on review."
    assert body["attentionRequired"] is True
    assert body["actions"]["canApprove"] is True
    assert body["actions"]["canResume"] is True
    assert body["actions"]["canCancel"] is True
    assert body["debugFields"]["workflowId"] == "mm:wf-1"
    assert body["redirectPath"] == "/tasks/mm:wf-1?source=temporal"


def test_describe_execution_disables_actions_when_feature_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canPause"] is False
    assert body["actions"]["disabledReasons"]["pause"] == "actions_disabled"
    assert body["debugFields"] is None


def test_action_endpoints_reject_requests_when_actions_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)

    with TestClient(app) as test_client:
        # Test update endpoint
        update_response = test_client.post(
            "/api/executions/mm:wf-1/update", json={"updateName": "RequestRerun"}
        )
        assert update_response.status_code == 403
        assert update_response.json()["detail"]["code"] == "actions_disabled"

        # Test signal endpoint
        signal_response = test_client.post(
            "/api/executions/mm:wf-1/signal", json={"signalName": "pause"}
        )
        assert signal_response.status_code == 403
        assert signal_response.json()["detail"]["code"] == "actions_disabled"

        # Test cancel endpoint
        cancel_response = test_client.post("/api/executions/mm:wf-1/cancel", json={})
        assert cancel_response.status_code == 403
        assert cancel_response.json()["detail"]["code"] == "actions_disabled"
