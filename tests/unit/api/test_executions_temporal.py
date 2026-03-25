"""Unit tests for Temporal specific API endpoint behaviors."""

from __future__ import annotations

from typing import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_service.api.routers.executions as executions_module
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session


def _override_user_dependencies(app: FastAPI, *, is_superuser: bool) -> MagicMock:
    # Plain MagicMock: AsyncMock user objects can trigger "never awaited" warnings
    # when routes or FastAPI touch attributes during teardown.
    mock_user = MagicMock()
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
def client() -> Iterator[tuple[TestClient, AsyncMock, MagicMock, MagicMock]]:
    app = FastAPI()
    app.include_router(executions_module.router)
    service = AsyncMock()
    app.dependency_overrides[executions_module._get_service] = lambda: service
    user = _override_user_dependencies(app, is_superuser=False)

    # MagicMock + explicit AsyncMocks: a bare AsyncMock session makes every
    # attribute an async mock; incidental access can leave unawaited coroutines.
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock(return_value=None)
    mock_session.rollback = AsyncMock(return_value=None)

    async def _session_dep():
        yield mock_session

    app.dependency_overrides[get_async_session] = _session_dep

    with TestClient(app) as test_client:
        yield test_client, service, user, mock_session

    app.dependency_overrides.clear()


def test_list_executions_source_temporal_bypasses_db_and_queries_temporal(
    client,
) -> None:
    test_client, service, user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        from datetime import UTC, datetime
        from types import SimpleNamespace

        memo_data = {"waiting_reason": "external_completion"}

        async def _memo():
            return memo_data

        mock_wf = SimpleNamespace()
        mock_wf.id = "mm:wf-1"
        mock_wf.run_id = "run-1"
        mock_wf.namespace = "moonmind"
        mock_wf.workflow_type = "MoonMind.Run"
        mock_wf.status = 1  # RUNNING
        mock_wf.memo = _memo
        mock_wf.search_attributes = {
            "mm_state": b'"awaiting_external"',
            "mm_entry": b'["run"]',
        }
        mock_wf.start_time = datetime.now(UTC)
        mock_wf.execution_time = None
        mock_wf.close_time = None

        mock_iterator = AsyncMock()
        mock_iterator.current_page = [mock_wf]
        mock_iterator.next_page_token = None
        mock_client.list_workflows = lambda **kwargs: mock_iterator

        mock_count = SimpleNamespace(count=1)
        mock_client.count_workflows = AsyncMock(return_value=mock_count)

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


def test_list_executions_source_temporal_merges_canonical_parameters(
    client,
) -> None:
    """Temporal list uses memo-only parameters; merge DB canonical row for Runtime/Skill."""
    from types import SimpleNamespace

    test_client, service, user, mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    # One canonical row matching the workflow id from Temporal list
    canon = SimpleNamespace()
    canon.workflow_id = "mm:wf-1"
    canon.parameters = {
        "targetRuntime": "codex",
        "task": {"tool": {"name": "fix-ci", "version": "1"}},
    }
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [canon]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        from datetime import UTC, datetime
        from types import SimpleNamespace

        memo_data = {"waiting_reason": "external_completion"}

        async def _memo():
            return memo_data

        mock_wf = SimpleNamespace()
        mock_wf.id = "mm:wf-1"
        mock_wf.run_id = "run-1"
        mock_wf.namespace = "moonmind"
        mock_wf.workflow_type = "MoonMind.Run"
        mock_wf.status = 1
        mock_wf.memo = _memo
        mock_wf.search_attributes = {
            "mm_state": b'"awaiting_external"',
            "mm_entry": b'["run"]',
        }
        mock_wf.start_time = datetime.now(UTC)
        mock_wf.execution_time = None
        mock_wf.close_time = None

        mock_iterator = AsyncMock()
        mock_iterator.current_page = [mock_wf]
        mock_iterator.next_page_token = None
        mock_client.list_workflows = lambda **kwargs: mock_iterator

        mock_count = SimpleNamespace(count=1)
        mock_client.count_workflows = AsyncMock(return_value=mock_count)

        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "workflowType": "MoonMind.Run",
                "state": "awaiting_external",
            },
        )

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["targetRuntime"] == "codex"
        assert item["targetSkill"] == "fix-ci"


def test_describe_execution_source_temporal_syncs_projection(client) -> None:
    test_client, service, user, _mock_session = client

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
        record.created_at = datetime.now(UTC)
        record.started_at = datetime.now(UTC)
        record.updated_at = datetime.now(UTC)
        record.closed_at = None
        record.integration_state = None
        service.describe_execution.return_value = record

        response = test_client.get("/api/executions/mm:wf-123?source=temporal")

        assert response.status_code == 200
        mock_fetch.assert_called_once_with(mock_client, "mm:wf-123")
        mock_sync.assert_called_once()
        service.describe_execution.assert_awaited_once_with(
            "mm:wf-123",
            include_orphaned=True,
        )


def test_describe_execution_canonicalizes_mm_prefix(client) -> None:
    test_client, service, user, _mock_session = client

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
    record.created_at = datetime.now(UTC)
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
        # Stand-in Temporal client: bare AsyncMock creates stray coroutines if touched;
        # this path does not use the client when authoritative read is off.
        mock_client = MagicMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_canon.return_value = ("mm:wf-123", True)
        response = test_client.get("/api/executions/wf-123")

        assert response.status_code == 200
        assert response.headers.get("Deprecation") == "true"
        assert response.headers.get("X-MoonMind-Canonical-WorkflowId") == "mm:wf-123"
        service.describe_execution.assert_awaited_once_with(
            "wf-123",
            include_orphaned=False,
        )


def test_temporal_unavailability_returns_503(client) -> None:
    test_client, service, user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        from temporalio.service import RPCError, RPCStatusCode

        mock_iterator = AsyncMock()
        mock_iterator.fetch_next_page = AsyncMock(
            side_effect=RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None)
        )
        mock_client.list_workflows = lambda **kwargs: mock_iterator

        mock_client.count_workflows = AsyncMock(
            side_effect=RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None)
        )

        response = test_client.get("/api/executions?source=temporal")

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "temporal_unavailable"


# Trigger CI
