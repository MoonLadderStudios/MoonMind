"""Unit tests for task-run live session API router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterator
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_runs import router
from api_service.db.base import get_async_session

@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    db_mock = AsyncMock()
    # Mock the execute return value
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = None
    result_mock.scalars.return_value = scalars_mock
    db_mock.execute.return_value = result_mock
    
    app.dependency_overrides[get_async_session] = lambda: db_mock

    with TestClient(app) as test_client:
        yield test_client, db_mock
    app.dependency_overrides.clear()

def test_get_live_session_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client

    response = test_client.get(f"/api/task-runs/{uuid4()}/live-session")

    assert response.status_code == 404

def test_get_live_session_worker_endpoint_returns_404_when_missing(
    client: tuple[TestClient, AsyncMock],
) -> None:
    test_client, db_mock = client

    response = test_client.get(f"/api/task-runs/{uuid4()}/live-session/worker")

    assert response.status_code == 404
