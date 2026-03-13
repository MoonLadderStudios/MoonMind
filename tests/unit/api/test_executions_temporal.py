"""Unit tests for Temporal specific API endpoint behaviors."""

from __future__ import annotations

from typing import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_service.api.routers.executions as executions_module
from api_service.auth_providers import get_current_user


def _override_user_dependencies(app: FastAPI, *, is_superuser: bool) -> AsyncMock:
    mock_user = AsyncMock()
    mock_user.id = "user-123"
    mock_user.is_superuser = is_superuser
    app.dependency_overrides[get_current_user()] = lambda: mock_user
    for route in app.routes:
        if hasattr(route, "dependant"):
            for dep in route.dependant.dependencies:
                if dep.call.__name__ == "_current_user_fallback":
                    app.dependency_overrides[dep.call] = lambda: mock_user
    return mock_user


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, AsyncMock]]:
    app = FastAPI()
    app.include_router(executions_module.router)
    service = AsyncMock()
    app.dependency_overrides[executions_module._get_service] = lambda: service
    user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        yield test_client, service, user

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_executions_source_temporal_bypasses_db_and_queries_temporal(
    client,
) -> None:
    test_client, service, user = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        async def mock_list_workflows(query):
            from datetime import UTC, datetime
            from types import SimpleNamespace

            mock_wf = SimpleNamespace()
            mock_wf.id = "mm:wf-1"
            mock_wf.run_id = "run-1"
            mock_wf.namespace = "moonmind"
            mock_wf.workflow_type = "MoonMind.Run"
            mock_wf.status = 1  # RUNNING
            mock_wf.memo = {"waiting_reason": "external_completion"}
            mock_wf.search_attributes = {
                "mm_state": b'"awaiting_external"',
                "mm_entry": b'["run"]',
            }
            mock_wf.start_time = datetime.now(UTC)
            mock_wf.close_time = None
            yield mock_wf

        mock_client.list_workflows = mock_list_workflows

        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "workflowType": "MoonMind.Run",
                "state": "awaiting_external",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        item = data["items"][0]
        assert item["workflowId"] == "mm:wf-1"
        assert item["state"] == "awaiting_external"
        assert item["entry"] == "run"
        assert item["waitingReason"] == "external_completion"


@pytest.mark.asyncio
async def test_describe_execution_source_temporal_syncs_projection(client) -> None:
    test_client, service, user = client

    executions_module.get_temporal_client_adapter.cache_clear()

    # We patch fetch_workflow_execution and sync_execution_projection
    with (
        patch(
            "moonmind.workflows.temporal.client.fetch_workflow_execution"
        ) as mock_fetch,
        patch("api_service.core.sync.sync_execution_projection") as mock_sync,
        patch(
            "api_service.api.routers.executions.TemporalClientAdapter"
        ) as mock_adapter_cls,
    ):
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_desc = AsyncMock()
        mock_fetch.return_value = mock_desc

        from datetime import UTC, datetime
        from types import SimpleNamespace

        from api_service.db.models import MoonMindWorkflowState

        record = SimpleNamespace()
        record.workflow_id = "mm:wf-123"
        record.run_id = "run-1"
        record.namespace = "moonmind"
        record.workflow_type = SimpleNamespace(value="MoonMind.Run")
        record.state = MoonMindWorkflowState.EXECUTING
        record.close_status = None
        record.owner_id = "user-123"
        record.owner_type = SimpleNamespace(value="user")
        record.search_attributes = {}
        record.memo = {}
        record.artifact_refs = []
        record.entry = "run"
        record.started_at = datetime.now(UTC)
        record.updated_at = datetime.now(UTC)
        record.closed_at = None
        record.integration_state = None
        service.describe_execution.return_value = record

        response = test_client.get("/api/executions/mm:wf-123?source=temporal")

        assert response.status_code == 200
        mock_fetch.assert_called_once_with(mock_client, "mm:wf-123")
        mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_describe_execution_canonicalizes_mm_prefix(client) -> None:
    test_client, service, user = client

    executions_module.get_temporal_client_adapter.cache_clear()

    from datetime import UTC, datetime
    from types import SimpleNamespace

    from api_service.db.models import MoonMindWorkflowState

    record = SimpleNamespace()
    record.workflow_id = "mm:wf-123"
    record.run_id = "run-1"
    record.namespace = "moonmind"
    record.workflow_type = SimpleNamespace(value="MoonMind.Run")
    record.state = MoonMindWorkflowState.EXECUTING
    record.close_status = None
    record.owner_id = "user-123"
    record.owner_type = SimpleNamespace(value="user")
    record.search_attributes = {}
    record.memo = {}
    record.artifact_refs = []
    record.entry = "run"
    record.started_at = datetime.now(UTC)
    record.updated_at = datetime.now(UTC)
    record.closed_at = None
    record.integration_state = None
    service.describe_execution.return_value = record

    with (
        patch(
            "api_service.api.routers.executions._canonicalize_execution_identifier"
        ) as mock_canon,
        patch(
            "api_service.api.routers.executions.TemporalClientAdapter"
        ) as mock_adapter_cls,
    ):
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_canon.return_value = ("mm:wf-123", True)
        response = test_client.get("/api/executions/wf-123")

        assert response.status_code == 200
        assert response.headers.get("Deprecation") == "true"
        assert response.headers.get("X-MoonMind-Canonical-WorkflowId") == "mm:wf-123"


@pytest.mark.asyncio
async def test_temporal_unavailability_returns_503(client) -> None:
    test_client, service, user = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        async def mock_list_workflows(query):
            from temporalio.service import RPCError, RPCStatusCode

            raise RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None)
            yield  # Ensure it's treated as an async generator

        mock_client.list_workflows = mock_list_workflows

        response = test_client.get("/api/executions?source=temporal")

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "temporal_unavailable"


# Trigger CI
