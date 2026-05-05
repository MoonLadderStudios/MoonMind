"""Hermetic integration coverage for task-list visibility boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    router as executions_router,
)
from api_service.db.base import get_async_session
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class _EmptyCanonicalRows:
    def all(self) -> list[object]:
        return []


class _EmptyCanonicalResult:
    def scalars(self) -> _EmptyCanonicalRows:
        return _EmptyCanonicalRows()


class _FakeTemporalIterator:
    def __init__(self, workflow_ids: list[str]) -> None:
        self.current_page = [SimpleNamespace(id=workflow_id) for workflow_id in workflow_ids]
        self.next_page_token: bytes | None = None

    async def fetch_next_page(self) -> None:
        return None


def _execution_user_overrides() -> list[object]:
    return [
        dep.call
        for route in executions_router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") in {"_get_current_user", "get_current_user"}
    ]


def _projection(workflow_id: str, user_id: str) -> dict[str, object]:
    now = datetime(2026, 5, 5, tzinfo=UTC)
    if workflow_id == "mm:task-run":
        workflow_type = TemporalWorkflowType.RUN
        entry = "run"
        title = "Task run row"
        owner_type = "user"
        owner = user_id
    elif workflow_id == "mm:manifest":
        workflow_type = TemporalWorkflowType.MANIFEST_INGEST
        entry = "manifest"
        title = "Manifest row"
        owner_type = "user"
        owner = user_id
    else:
        workflow_type = TemporalWorkflowType.PROVIDER_PROFILE_MANAGER
        entry = "system"
        title = "System row"
        owner_type = "system"
        owner = "system"
    return {
        "namespace": "moonmind",
        "workflow_id": workflow_id,
        "run_id": "run-1",
        "workflow_type": workflow_type,
        "state": MoonMindWorkflowState.EXECUTING,
        "close_status": None,
        "search_attributes": {
            "mm_owner_id": owner,
            "mm_owner_type": owner_type,
            "mm_entry": entry,
        },
        "memo": {"title": title, "summary": title},
        "artifact_refs": [],
        "manifest_ref": (
            "art_manifest"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        "plan_ref": (
            "art_plan"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        "parameters": {},
        "paused": False,
        "waiting_reason": None,
        "attention_required": False,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "closed_at": None,
        "owner_id": owner,
        "owner_type": owner_type,
        "entry": entry,
        "integration_state": None,
    }


@pytest_asyncio.fixture
async def async_client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    user = SimpleNamespace(
        id=uuid4(),
        email="task-list-integration@example.com",
        is_active=True,
        is_superuser=False,
    )
    for dependency in _execution_user_overrides():
        app.dependency_overrides[dependency] = lambda user=user: user
    app.dependency_overrides[_get_service] = lambda: AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=AsyncMock(return_value=_EmptyCanonicalResult())
    )

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=3)),
        list_workflows=Mock(
            return_value=_FakeTemporalIterator(
                ["mm:task-run", "mm:manifest", "mm:system"]
            )
        ),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    async def fake_projection(workflow: SimpleNamespace) -> dict[str, object]:
        return _projection(workflow.id, str(user.id))

    monkeypatch.setattr(
        "api_service.core.sync.map_temporal_state_to_projection",
        fake_projection,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(base_url="http://testserver", transport=transport) as client:
        yield client

    app.dependency_overrides.clear()


async def test_tasks_list_temporal_scope_filters_mixed_workflow_rows(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get(
        "/api/executions",
        params={"source": "temporal", "scope": "tasks"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Task run row"]
    assert body["items"][0]["workflowType"] == "MoonMind.Run"
    assert body["items"][0]["entry"] == "run"


@pytest.mark.parametrize(
    "params",
    [
        {"source": "temporal", "scope": "system"},
        {"source": "temporal", "scope": "all"},
        {
            "source": "temporal",
            "scope": "tasks",
            "workflowType": "MoonMind.ProviderProfileManager",
        },
        {"source": "temporal", "scope": "tasks", "entry": "manifest"},
    ],
)
async def test_task_scope_boundary_remains_task_safe_for_broad_compatibility_params(
    async_client: AsyncClient,
    params: dict[str, str],
) -> None:
    response = await async_client.get("/api/executions", params=params)

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()["items"]]
    if params.get("scope") == "tasks":
        assert titles == ["Task run row"]
    else:
        assert "Task run row" in titles
