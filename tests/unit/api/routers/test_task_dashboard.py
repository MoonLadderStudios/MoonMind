"""Unit tests for task dashboard shell routes."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_dashboard import (
    _get_temporal_service,
    _is_allowed_path,
    _resolve_user_dependency_overrides,
    router,
)
from api_service.db.base import get_async_session


def _build_mock_temporal_service() -> AsyncMock:
    return AsyncMock()


@contextmanager
def _client_with_mock_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)

    mock_user = SimpleNamespace(id=uuid4(), email="dashboard@example.com")
    mock_service = AsyncMock()
    mock_service.list_jobs_page.return_value = SimpleNamespace(
        items=tuple(),
        page_size=50,
        next_cursor=None,
    )
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user
    app.dependency_overrides[_get_temporal_service] = _build_mock_temporal_service

    with TestClient(app) as test_client:
        yield test_client, mock_service

    app.dependency_overrides.clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client_with_mock_service() as (test_client, _mock_service):
        yield test_client


def test_allowed_path_helper_accepts_known_routes() -> None:
    assert _is_allowed_path("list")
    assert not _is_allowed_path("system")
    assert not _is_allowed_path("queue")
    assert not _is_allowed_path("queue/new")
    assert not _is_allowed_path("queue/123")
    assert _is_allowed_path("mm:123")
    assert _is_allowed_path("123e4567-e89b-12d3-a456-426614174000")
    assert _is_allowed_path("mm:123e4567-e89b-12d3-a456-426614174000")
    assert _is_allowed_path("mm:01JNX7SYH6A3K1V8Q2D7E9F4AB")
    assert _is_allowed_path("new")
    assert _is_allowed_path("manifests")
    assert _is_allowed_path("manifests/new")
    assert _is_allowed_path("schedules")
    assert _is_allowed_path("schedules/new")
    assert _is_allowed_path("settings")
    assert _is_allowed_path("workers")


def test_allowed_path_helper_rejects_unknown_routes() -> None:
    assert not _is_allowed_path("")
    assert not _is_allowed_path("queue/new/extra")
    assert not _is_allowed_path("queue//")
    assert not _is_allowed_path("queue/<script>alert(1)</script>")
    assert not _is_allowed_path("queue/not allowed")


def test_root_route_renders_dashboard_shell(client: TestClient) -> None:
    response = client.get("/tasks")

    assert response.status_code == 200
    body = response.text
    assert "Mission Control" in body
    assert "task-dashboard-config" in body
    assert "/static/task_dashboard/dashboard.js" in body
    assert "viewport-fit=cover" in body
    assert "moonmind.theme" in body


def test_static_sub_routes_render_dashboard_shell(client: TestClient) -> None:
    for path in (
        "/tasks/list",
        "/tasks/new",
        "/tasks/create",
        "/tasks/manifests",
        "/tasks/manifests/new",
        "/tasks/schedules",
        "/tasks/schedules/new",
        "/tasks/workers",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "task-dashboard-config" in response.text

    # /tasks/settings has its own template now
    response = client.get("/tasks/settings")
    assert response.status_code == 200
    assert "moonmind-ui-boot" in response.text


def test_detail_sub_routes_render_dashboard_shell(client: TestClient) -> None:
    for path in (
        f"/tasks/{uuid4()}",
        f"/tasks/mm:{uuid4()}",
        "/tasks/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
        "/tasks/mm:workflow-123",
        f"/tasks/manifests/{uuid4()}",
        f"/tasks/schedules/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "task-dashboard-config" in response.text


def test_legacy_system_dashboard_route_returns_404(client: TestClient) -> None:
    response = client.get("/tasks/system")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "dashboard_route_not_found"


def test_invalid_multi_segment_routes_return_404(client: TestClient) -> None:
    for path in (
        "/tasks/unknown/extra/segment",
        "/tasks/queue/new/extra",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"


def test_temporal_source_root_is_not_exposed(client: TestClient) -> None:
    response = client.get("/tasks/temporal")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "dashboard_route_not_found"


def test_temporal_source_subroutes_return_404_until_first_class_source_exists(
    client: TestClient,
) -> None:
    for path in (
        "/tasks/temporal/new",
        f"/tasks/temporal/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"


def test_invalid_dashboard_route_returns_404(client: TestClient) -> None:
    response = client.get("/tasks/not-a-valid-dashboard-path/extra")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "dashboard_route_not_found"
    assert detail["message"] == (
        "Dashboard route was not found. Use /tasks/list, /tasks/{taskId}, "
        "/tasks/create, /tasks/new, "
        "/tasks/proposals, /tasks/manifests, /tasks/manifests/new, "
        "/tasks/schedules, /tasks/schedules/new, /tasks/workers, /tasks/skills, or /tasks/settings."
    )


def test_skills_api_returns_available_skill_ids(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.task_dashboard.list_available_skill_names",
        lambda: ("speckit", "speckit-orchestrate"),
    )

    response = client.get("/api/tasks/skills")

    assert response.status_code == 200
    assert response.json() == {
        "items": {
            "worker": ["speckit", "speckit-orchestrate"],
        },
        "legacyItems": [
            {"id": "speckit", "markdown": None},
            {"id": "speckit-orchestrate", "markdown": None},
        ],
    }




def test_create_dashboard_skill_success(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.task_dashboard.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    payload = {
        "name": "MyNewSkill",
        "markdown": "# My New Skill\n\nThis is the skill content...",
    }
    response = client.post("/api/tasks/skills", json=payload)

    assert response.status_code == 201
    assert response.json() == {"status": "success"}

    skill_file = tmp_path / "MyNewSkill" / "SKILL.md"
    assert skill_file.is_file()
    assert skill_file.read_text(encoding="utf-8") == payload["markdown"]


def test_create_dashboard_skill_invalid_name(client: TestClient) -> None:
    payload = {
        "name": "../MyNewSkill",
        "markdown": "# My New Skill\n\nThis is the skill content...",
    }
    response = client.post("/api/tasks/skills", json=payload)

    assert response.status_code == 400
    assert "Invalid skill name" in response.json()["detail"]


def test_create_dashboard_skill_already_exists(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.task_dashboard.settings.workflow.skills_local_mirror_root",
        str(tmp_path),
    )

    skill_dir = tmp_path / "ExistingSkill"
    skill_dir.mkdir()

    payload = {
        "name": "ExistingSkill",
        "markdown": "# Existing Skill\n\nContent...",
    }
    response = client.post("/api/tasks/skills", json=payload)

    assert response.status_code == 409
    assert "already exists locally" in response.json()["detail"]


def test_task_source_endpoint_returns_resolved_temporal_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_resolve(**_kwargs):
        return {
            "taskId": "mm:workflow-123",
            "source": "temporal",
            "sourceLabel": "Temporal",
            "detailPath": "/tasks/mm:workflow-123?source=temporal",
        }

    monkeypatch.setattr(
        "api_service.api.routers.task_dashboard._resolve_dashboard_task_source",
        _fake_resolve,
    )

    response = client.get("/api/tasks/mm:workflow-123/source")

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "mm:workflow-123",
        "source": "temporal",
        "sourceLabel": "Temporal",
        "detailPath": "/tasks/mm:workflow-123?source=temporal",
    }


def test_task_source_endpoint_returns_404_when_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_resolve(**_kwargs):
        return None

    monkeypatch.setattr(
        "api_service.api.routers.task_dashboard._resolve_dashboard_task_source",
        _fake_resolve,
    )

    response = client.get("/api/tasks/mm:workflow-404/source")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "task_source_not_found"


def test_task_resolution_returns_temporal_source_for_workflow_id() -> None:
    class FakeSession:
        async def get(self, model, key):
            if model.__name__ == "TemporalExecutionRecord":
                return SimpleNamespace(
                    workflow_id=str(key),
                    owner_id="owner-1",
                    entry="run",
                )
            return None

    app = FastAPI()
    app.include_router(router)
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda: SimpleNamespace(
            id="owner-1",
            email="dashboard@example.com",
            is_superuser=False,
        )
    app.dependency_overrides[get_async_session] = lambda: FakeSession()

    with TestClient(app) as client:
        response = client.get("/api/tasks/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB/resolution")

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
        "source": "temporal",
        "entry": "run",
        "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
    }


def test_task_resolution_rejects_orchestrator_source_hint() -> None:
    class FakeSession:
        async def get(self, model, key):
            return None

    task_id = str(uuid4())

    app = FastAPI()
    app.include_router(router)
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda: SimpleNamespace(
            id=uuid4(),
            email="dashboard@example.com",
            is_superuser=False,
        )
    app.dependency_overrides[get_async_session] = lambda: FakeSession()

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/resolution?source=orchestrator")

    assert response.status_code == 422
