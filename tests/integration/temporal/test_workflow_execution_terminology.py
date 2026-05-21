"""Hermetic integration checks for workflow execution terminology."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    _serialize_execution,
    get_temporal_client,
    router,
)
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_temporal_user_workflow_query_excludes_legacy_run_entry() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = lambda: AsyncMock()
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "scope": "tasks"},
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_entry="user_workflow"' in count_query
    assert 'mm_entry="run"' not in count_query


def test_serialized_workflow_execution_exposes_agent_run_id_not_task_run_id() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "user_workflow"},
        memo={
            "title": "Agent-backed workflow",
            "summary": "Running.",
            "taskRunId": "agent-run-1",
        },
        owner_id="user-1",
        entry="user_workflow",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:wf-agent",
        namespace="moonmind",
        run_id="temporal-run-1",
        artifact_refs=[],
        created_at="2026-05-21T00:00:00Z",
        started_at="2026-05-21T00:00:00Z",
        updated_at="2026-05-21T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["agentRunId"] == "agent-run-1"
    assert "taskRunId" not in payload
    assert "task_run_id" not in payload
