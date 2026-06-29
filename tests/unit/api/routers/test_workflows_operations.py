"""Tests for legacy workflow cleanup and Codex operations routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.workflows import _get_repository, router
from api_service.auth_providers import get_current_user
from moonmind.workflows.automation import models


@pytest.fixture
def client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    repo = AsyncMock()
    app.dependency_overrides[_get_repository] = lambda: repo

    user = SimpleNamespace(id=uuid4(), is_active=True, is_superuser=True)
    user_dependencies = {
        dep.call
        for route in app.routes
        if getattr(route, "dependant", None) is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user

    return TestClient(app), repo


def test_legacy_workflow_lifecycle_routes_return_gone(
    client: tuple[TestClient, AsyncMock],
) -> None:
    http_client, repo = client
    run_id = uuid4()
    artifact_id = uuid4()

    for method, path in (
        ("get", "/api/workflows/runs"),
        ("get", f"/api/workflows/runs/{run_id}"),
        ("get", f"/api/workflows/runs/{run_id}/tasks"),
        ("get", f"/api/workflows/runs/{run_id}/artifacts"),
        ("get", f"/api/workflows/runs/{run_id}/artifacts/{artifact_id}"),
        ("get", f"/api/workflows/runs/{run_id}/artifacts/{artifact_id}/download"),
        ("post", f"/api/workflows/runs/{run_id}/retry"),
    ):
        response = getattr(http_client, method)(path)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "legacy_workflow_runs_api_removed"
        assert response.json()["detail"]["jiraIssue"] == "MM-1022"

    repo.assert_not_awaited()


def test_codex_shards_moved_to_operations_namespace(
    client: tuple[TestClient, AsyncMock],
) -> None:
    http_client, repo = client
    repo.list_codex_shard_health.return_value = [
        SimpleNamespace(
            queue_name="codex-shard-0",
            shard_status=models.CodexWorkerShardStatus.ACTIVE,
            hash_modulo=1,
            worker_hostname="worker-0",
            volume_name="codex-auth-0",
            volume_status=models.CodexAuthVolumeStatus.READY,
            volume_last_verified_at=None,
            volume_worker_affinity=None,
            volume_notes=None,
            latest_run_id=None,
            latest_run_status=None,
            latest_preflight_status=None,
            latest_preflight_message=None,
            latest_preflight_checked_at=None,
        )
    ]

    response = http_client.get("/api/v1/operations/codex/shards")

    assert response.status_code == 200
    assert response.json()["shards"][0]["queueName"] == "codex-shard-0"
    repo.list_codex_shard_health.assert_awaited_once()


def test_legacy_codex_operations_routes_return_gone(
    client: tuple[TestClient, AsyncMock],
) -> None:
    http_client, repo = client
    run_id = uuid4()

    shard_response = http_client.get("/api/workflows/codex/shards")
    preflight_response = http_client.post(
        f"/api/workflows/runs/{run_id}/codex/preflight",
        json={},
    )

    assert shard_response.status_code == 410
    assert shard_response.json()["detail"]["replacement"] == (
        "/api/v1/operations/codex/shards"
    )
    assert preflight_response.status_code == 410
    assert preflight_response.json()["detail"]["replacement"] == (
        f"/api/v1/operations/codex/preflight/{run_id}"
    )
    repo.assert_not_awaited()
