"""Unit tests for Temporal execution lifecycle API router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    _serialize_execution,
    router,
)
from api_service.auth_providers import get_current_user
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType


def _build_execution_record() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:wf-1",
        run_id="run-2",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={
            "mm_state": "executing",
            "mm_continue_as_new_cause": "manual_rerun",
        },
        memo={
            "title": "Temporal execution",
            "summary": "Rerun requested via Continue-As-New.",
            "continue_as_new_cause": "manual_rerun",
            "latest_temporal_run_id": "run-2",
        },
        artifact_refs=["artifact://output/1"],
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id=None,
    )


def _client_with_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=uuid4(), is_superuser=True
    )

    with TestClient(app) as test_client:
        yield test_client, mock_service
    app.dependency_overrides.clear()


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
    )

    payload = _serialize_execution(record)

    assert payload.owner_type == "system"
    assert payload.owner_id == "system"


def test_describe_execution_exposes_task_and_temporal_run_identity() -> None:
    """Temporal execution detail should anchor on workflow/task identity."""

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
    """Accepted rerun updates should report structured Continue-As-New cause."""

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
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["applied"] == "continue_as_new"
        assert payload["continueAsNewCause"] == "manual_rerun"


def test_list_executions_preserves_logical_identity_fields() -> None:
    """Temporal execution list items should stay anchored on workflow identity."""

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
        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert item["workflowId"] == "mm:wf-1"
        assert item["taskId"] == "mm:wf-1"
        assert item["runId"] == "run-2"
        assert item["temporalRunId"] == "run-2"
        assert item["latestRunView"] is True
        assert item["continueAsNewCause"] == "manual_rerun"
