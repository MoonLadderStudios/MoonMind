"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_current_user
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from moonmind.config.settings import settings


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
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user
    return mock_user


def _build_execution_record(*, state: MoonMindWorkflowState) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:workflow-123",
        run_id="run-123",
        workflow_type=TemporalWorkflowType.RUN,
        state=state,
        close_status=None,
        search_attributes={
            "mm_owner_id": "user-123",
            "mm_owner_type": "user",
            "mm_repo": "Moon/Mind",
            "mm_continue_as_new_cause": "manual_rerun",
        },
        memo={
            "title": "Temporal task",
            "summary": "Waiting on review.",
            "continue_as_new_cause": "manual_rerun",
            "latest_temporal_run_id": "run-123",
        },
        artifact_refs=["art_123"],
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id="user-123",
        integration_state=None,
    )


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
    with TestClient(app) as client:
        response = client.get(
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

    with TestClient(app) as client:
        response = client.get(
            "/api/executions",
            params={"ownerType": "system"},
        )

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

    with TestClient(app) as client:
        response = client.get("/api/executions")

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

    with TestClient(app) as client:
        response = client.get("/api/executions", params={"ownerType": "user"})

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

    with TestClient(app) as client:
        response = client.post(
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


def test_describe_execution_includes_actions_and_debug_fields(
    monkeypatch,
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

    with TestClient(app) as client:
        response = client.get("/api/executions/mm:workflow-123")

    assert response.status_code == 200
    body = response.json()
    assert body["taskId"] == "mm:workflow-123"
    assert body["source"] == "temporal"
    assert body["dashboardStatus"] == "awaiting_action"
    assert body["rawState"] == "awaiting_external"
    assert body["temporalRunId"] == "run-123"
    assert body["latestRunView"] is True
    assert body["continueAsNewCause"] == "manual_rerun"
    assert body["legacyRunId"] is None
    assert body["waitingReason"] == "Waiting on review."
    assert body["attentionRequired"] is True
    assert body["actions"]["canApprove"] is True
    assert body["actions"]["canResume"] is True
    assert body["actions"]["canCancel"] is True
    assert body["actions"]["canRerun"] is False
    assert body["debugFields"]["workflowId"] == "mm:workflow-123"
    assert body["debugFields"]["temporalRunId"] == "run-123"
    assert body["redirectPath"] == "/tasks/mm:workflow-123?source=temporal"


def test_describe_execution_disables_actions_when_feature_flag_off(
    monkeypatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.EXECUTING
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", False)

    with TestClient(app) as client:
        response = client.get("/api/executions/mm:workflow-123")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canPause"] is False
    assert body["actions"]["disabledReasons"]["pause"] == "actions_disabled"
    assert body["debugFields"] is None
