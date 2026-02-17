"""Unit tests for task dashboard shell routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_dashboard import (
    _is_allowed_path,
    _resolve_user_dependency_overrides,
    router,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)

    mock_user = SimpleNamespace(id=uuid4(), email="dashboard@example.com")
    for dependency in _resolve_user_dependency_overrides():
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_allowed_path_helper_accepts_known_routes() -> None:
    assert _is_allowed_path("queue")
    assert _is_allowed_path("queue/new")
    assert _is_allowed_path("queue/123")
    assert _is_allowed_path("orchestrator/run-1")


def test_allowed_path_helper_rejects_unknown_routes() -> None:
    assert not _is_allowed_path("")
    assert not _is_allowed_path("unknown")
    assert not _is_allowed_path("queue/new/extra")
    assert not _is_allowed_path("queue//")
    assert not _is_allowed_path("queue/<script>alert(1)</script>")
    assert not _is_allowed_path("queue/not allowed")


def test_root_route_renders_dashboard_shell(client: TestClient) -> None:
    response = client.get("/tasks")

    assert response.status_code == 200
    body = response.text
    assert "Tasks Dashboard" in body
    assert "task-dashboard-config" in body
    assert "/static/task_dashboard/dashboard.js" in body


def test_static_sub_routes_render_dashboard_shell(client: TestClient) -> None:
    for path in (
        "/tasks/queue",
        "/tasks/queue/new",
        "/tasks/orchestrator",
        "/tasks/orchestrator/new",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "task-dashboard-config" in response.text


def test_detail_sub_routes_render_dashboard_shell(client: TestClient) -> None:
    for path in (
        f"/tasks/queue/{uuid4()}",
        f"/tasks/orchestrator/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "task-dashboard-config" in response.text


def test_speckit_routes_return_404(client: TestClient) -> None:
    for path in (
        "/tasks/speckit",
        "/tasks/speckit/new",
        f"/tasks/speckit/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dashboard_route_not_found"


def test_invalid_dashboard_route_returns_404(client: TestClient) -> None:
    response = client.get("/tasks/not-a-valid-dashboard-path")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "dashboard_route_not_found"


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
        "items": [
            {"id": "speckit"},
            {"id": "speckit-orchestrate"},
        ]
    }
