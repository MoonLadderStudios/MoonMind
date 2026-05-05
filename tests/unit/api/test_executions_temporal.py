"""Unit tests for Temporal specific API endpoint behaviors."""

from __future__ import annotations

from types import SimpleNamespace
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


def test_list_executions_source_temporal_defaults_to_task_scope(client) -> None:
    test_client, _service, _user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_iterator = AsyncMock()
        mock_iterator.current_page = []
        mock_iterator.next_page_token = None
        mock_client.list_workflows = MagicMock(return_value=mock_iterator)
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=0))

        response = test_client.get("/api/executions", params={"source": "temporal"})

        assert response.status_code == 200
        expected_query = (
            'WorkflowType="MoonMind.Run" AND mm_entry="run" '
            'AND mm_owner_id="user-123"'
        )
        mock_client.count_workflows.assert_awaited_once_with(query=expected_query)
        mock_client.list_workflows.assert_called_once()
        assert mock_client.list_workflows.call_args.kwargs["query"] == expected_query


def test_list_executions_source_temporal_scope_all_fails_safe_to_task_query(
    client,
) -> None:
    test_client, _service, _user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_iterator = AsyncMock()
        mock_iterator.current_page = []
        mock_iterator.next_page_token = None
        mock_client.list_workflows = MagicMock(return_value=mock_iterator)
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=0))

        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "scope": "all"},
        )

        assert response.status_code == 200
        expected_query = (
            'WorkflowType="MoonMind.Run" AND mm_entry="run" '
            'AND mm_owner_id="user-123"'
        )
        mock_client.count_workflows.assert_awaited_once_with(query=expected_query)
        assert mock_client.list_workflows.call_args.kwargs["query"] == expected_query


def test_list_executions_source_temporal_ignores_workflow_kind_filters_for_task_list(
    client,
) -> None:
    test_client, _service, _user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_iterator = AsyncMock()
        mock_iterator.current_page = []
        mock_iterator.next_page_token = None
        mock_client.list_workflows = MagicMock(return_value=mock_iterator)
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=0))

        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scope": "system",
                "workflowType": "MoonMind.ProviderProfileManager",
                "entry": "manifest",
            },
        )

        assert response.status_code == 200
        expected_query = (
            'WorkflowType="MoonMind.Run" AND mm_entry="run" '
            'AND mm_owner_id="user-123"'
        )
        mock_client.count_workflows.assert_awaited_once_with(query=expected_query)
        assert mock_client.list_workflows.call_args.kwargs["query"] == expected_query


def test_list_executions_source_temporal_rejects_unknown_scope(client) -> None:
    test_client, _service, _user, _mock_session = client

    response = test_client.get(
        "/api/executions",
        params={"source": "temporal", "scope": "surprise"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_temporal_list_scope"


def test_task_detail_instructions_include_task_and_step_text() -> None:
    assert executions_module._derive_full_task_instructions(
        {
            "instructions": "Top-level task instructions.",
            "steps": [
                {"title": "Plan", "instructions": "Write the plan."},
                {"instructions": "Apply the change."},
                {"title": "Empty step", "instructions": "   "},
            ],
        }
    ) == (
        "Top-level task instructions.\n\n"
        "Step 1: Plan\nWrite the plan.\n\n"
        "Step 2\nApply the change."
    )

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

def test_list_executions_source_temporal_uses_memo_runtime_and_skill_for_child_runs(
    client,
) -> None:
    """Child workflows can lack canonical DB parameters but still publish compact memo visibility."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    test_client, _service, _user, mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        async def _memo():
            return {
                "entry": "run",
                "title": "Resolve PR #1633",
                "summary": "Resolver child workflow for merge automation.",
                "targetRuntime": "codex_cli",
                "targetSkill": "pr-resolver",
            }

        mock_wf = SimpleNamespace()
        mock_wf.id = "resolver:pr:1633:head:1045fd00767c:h:f144d66e268f79fd:1"
        mock_wf.run_id = "run-1"
        mock_wf.namespace = "moonmind"
        mock_wf.workflow_type = "MoonMind.Run"
        mock_wf.status = 2  # COMPLETED
        mock_wf.memo = _memo
        mock_wf.search_attributes = {
            "mm_entry": b'["run"]',
            "mm_owner_type": b'["user"]',
            "mm_owner_id": b'["user-123"]',
            "mm_repo": b'["MoonLadderStudios/Tactics"]',
        }
        mock_wf.start_time = datetime.now(UTC)
        mock_wf.execution_time = mock_wf.start_time
        mock_wf.close_time = mock_wf.start_time

        mock_iterator = AsyncMock()
        mock_iterator.current_page = [mock_wf]
        mock_iterator.next_page_token = None
        mock_client.list_workflows = lambda **kwargs: mock_iterator
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=1))

        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "workflowType": "MoonMind.Run"},
        )

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["targetRuntime"] == "codex_cli"
        assert item["targetSkill"] == "pr-resolver"
        assert item["repository"] == "MoonLadderStudios/Tactics"

def test_list_executions_source_temporal_orders_scheduled_runs_by_latest_scheduled_time(
    client,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    test_client, _service, _user, mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    late_schedule = datetime(2026, 4, 15, 18, 0, tzinfo=UTC)
    early_schedule = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)

    canonical_late = SimpleNamespace(
        workflow_id="mm:wf-late",
        parameters={},
        scheduled_for=late_schedule,
    )
    canonical_early = SimpleNamespace(
        workflow_id="mm:wf-early",
        parameters={},
        scheduled_for=early_schedule,
    )
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [canonical_late, canonical_early]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def _memo():
        return {}

    def _datetime_bytes(value: datetime) -> bytes:
        return f'"{value.isoformat().replace("+00:00", "Z")}"'.encode("utf-8")

    def _workflow(workflow_id: str, scheduled_for: datetime) -> SimpleNamespace:
        return SimpleNamespace(
            id=workflow_id,
            run_id=f"run-{workflow_id}",
            namespace="moonmind",
            workflow_type="MoonMind.Run",
            status=1,
            memo=_memo,
            search_attributes={
                "mm_state": b'"scheduled"',
                "mm_entry": b'["run"]',
                "mm_scheduled_for": _datetime_bytes(scheduled_for),
            },
            start_time=datetime(2026, 4, 15, 1, 0, tzinfo=UTC),
            execution_time=None,
            close_time=None,
        )

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_iterator = AsyncMock()
        mock_iterator.current_page = [
            _workflow("mm:wf-late", late_schedule),
            _workflow("mm:wf-early", early_schedule),
        ]
        mock_iterator.next_page_token = None
        mock_client.list_workflows = lambda **kwargs: mock_iterator
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=2))

        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "workflowType": "MoonMind.Run"},
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["workflowId"] for item in items] == ["mm:wf-late", "mm:wf-early"]
    assert (
        datetime.fromisoformat(items[0]["scheduledFor"].replace("Z", "+00:00"))
        == late_schedule
    )
    assert items[0]["startedAt"] is None
    assert items[1]["startedAt"] is None

def test_list_executions_source_temporal_orders_immediate_runs_by_updated_at(
    client,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    test_client, _service, _user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

    older_created = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    newer_created = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    older_updated = datetime(2026, 4, 15, 11, 0, tzinfo=UTC)
    newer_updated = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    async def _memo():
        return {}

    def _datetime_bytes(value: datetime) -> bytes:
        return f'"{value.isoformat().replace("+00:00", "Z")}"'.encode("utf-8")

    def _workflow(
        workflow_id: str,
        *,
        created_at: datetime,
        updated_at: datetime,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=workflow_id,
            run_id=f"run-{workflow_id}",
            namespace="moonmind",
            workflow_type="MoonMind.Run",
            status=1,
            memo=_memo,
            search_attributes={
                "mm_state": b'"executing"',
                "mm_entry": b'["run"]',
                "mm_updated_at": _datetime_bytes(updated_at),
            },
            start_time=created_at,
            execution_time=None,
            close_time=None,
        )

    with patch(
        "api_service.api.routers.executions.TemporalClientAdapter"
    ) as mock_adapter_cls:
        mock_adapter = mock_adapter_cls.return_value
        mock_client = AsyncMock()
        mock_adapter.get_client = AsyncMock(return_value=mock_client)

        mock_iterator = AsyncMock()
        mock_iterator.current_page = [
            _workflow(
                "mm:wf-older-created-newer-updated",
                created_at=older_created,
                updated_at=newer_updated,
            ),
            _workflow(
                "mm:wf-newer-created-older-updated",
                created_at=newer_created,
                updated_at=older_updated,
            ),
        ]
        mock_iterator.next_page_token = None
        mock_client.list_workflows = lambda **kwargs: mock_iterator
        mock_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=2))

        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "workflowType": "MoonMind.Run"},
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["workflowId"] for item in items] == [
        "mm:wf-older-created-newer-updated",
        "mm:wf-newer-created-older-updated",
    ]
    assert items[0]["scheduledFor"] is None
    assert items[1]["scheduledFor"] is None

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

def test_describe_execution_source_temporal_keeps_updated_at_stable_while_refreshing_freshness(
    client,
) -> None:
    test_client, service, _user, _mock_session = client

    executions_module.get_temporal_client_adapter.cache_clear()

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
        mock_fetch.return_value = AsyncMock()

        from datetime import UTC, datetime
        from types import SimpleNamespace

        from api_service.db.models import MoonMindWorkflowState

        semantic_updated_at = datetime(2026, 3, 28, 0, 0, 2, tzinfo=UTC)
        refreshed_at_1 = datetime(2026, 3, 28, 0, 0, 4, tzinfo=UTC)
        refreshed_at_2 = datetime(2026, 3, 28, 0, 0, 6, tzinfo=UTC)

        def _record(last_synced_at: datetime) -> SimpleNamespace:
            record = SimpleNamespace()
            record.workflow_id = "mm:wf-123"
            record.run_id = "run-1"
            record.namespace = "moonmind"
            record.workflow_type = SimpleNamespace(value="MoonMind.Run")
            record.state = MoonMindWorkflowState.EXECUTING
            record.close_status = None
            record.owner_id = "user-123"
            record.owner_type = SimpleNamespace(value="user")
            record.search_attributes = {"mm_state": "executing", "mm_entry": "run"}
            record.memo = {"title": "Task", "summary": "Running"}
            record.artifact_refs = []
            record.entry = "run"
            record.created_at = semantic_updated_at
            record.started_at = semantic_updated_at
            record.updated_at = semantic_updated_at
            record.last_synced_at = last_synced_at
            record.closed_at = None
            record.integration_state = None
            record.parameters = {}
            return record

        service.describe_execution.side_effect = [
            _record(refreshed_at_1),
            _record(refreshed_at_2),
        ]

        first = test_client.get("/api/executions/mm:wf-123?source=temporal")
        second = test_client.get("/api/executions/mm:wf-123?source=temporal")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["updatedAt"] == semantic_updated_at.isoformat().replace(
            "+00:00", "Z"
        )
        assert second.json()["updatedAt"] == semantic_updated_at.isoformat().replace(
            "+00:00", "Z"
        )
        assert first.json()["refreshedAt"] == refreshed_at_1.isoformat().replace(
            "+00:00", "Z"
        )
        assert second.json()["refreshedAt"] == refreshed_at_2.isoformat().replace(
            "+00:00", "Z"
        )
        assert mock_sync.await_count == 2

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
