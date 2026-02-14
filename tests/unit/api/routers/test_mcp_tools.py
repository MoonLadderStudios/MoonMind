"""Unit tests for MCP queue tool router."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.mcp_tools import _get_service, router
from api_service.auth_providers import get_current_user
from moonmind.workflows.agent_queue import models


pytestmark = [pytest.mark.speckit]


def _build_job(status: models.AgentJobStatus = models.AgentJobStatus.QUEUED):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        type="codex_exec",
        status=status,
        priority=10,
        payload={"instruction": "run"},
        created_by_user_id=uuid4(),
        requested_by_user_id=uuid4(),
        affinity_key="repo/moonmind",
        claimed_by=None,
        lease_expires_at=None,
        next_attempt_at=None,
        attempt=1,
        max_attempts=3,
        result_summary=None,
        error_message=None,
        artifacts_path=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    """Provide TestClient with service and auth overrides."""

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    mock_user = SimpleNamespace(
        id=uuid4(),
        email="mcp@example.com",
        is_active=True,
    )

    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda mock_user=mock_user: mock_user

    with TestClient(app) as test_client:
        yield test_client, mock_service, mock_user

    app.dependency_overrides.clear()


def test_list_tools_returns_queue_definitions(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """GET /mcp/tools should return all required queue tool names."""

    test_client, _service, _user = client

    response = test_client.get("/mcp/tools")

    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert {
        "queue.enqueue",
        "queue.claim",
        "queue.heartbeat",
        "queue.complete",
        "queue.fail",
        "queue.get",
        "queue.list",
        "queue.upload_artifact",
    }.issubset(names)


def test_call_queue_enqueue_success_returns_wrapped_job(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """queue.enqueue should invoke service and return wrapped JobModel payload."""

    test_client, service, user = client
    job = _build_job()
    service.create_job.return_value = job

    response = test_client.post(
        "/mcp/tools/call",
        json={
            "tool": "queue.enqueue",
            "arguments": {
                "type": "codex_exec",
                "priority": 10,
                "payload": {"instruction": "run"},
                "maxAttempts": 3,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["id"] == str(job.id)
    assert body["result"]["status"] == models.AgentJobStatus.QUEUED.value
    service.create_job.assert_awaited_once()
    called_kwargs = service.create_job.await_args.kwargs
    assert called_kwargs["created_by_user_id"] == user.id
    assert called_kwargs["requested_by_user_id"] == user.id


def test_call_unknown_tool_returns_404(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """Unknown tools should map to tool_not_found."""

    test_client, _service, _user = client

    response = test_client.post(
        "/mcp/tools/call",
        json={"tool": "queue.unknown", "arguments": {}},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "tool_not_found"


def test_call_invalid_arguments_returns_422(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """Invalid tool arguments should map to invalid_tool_arguments."""

    test_client, _service, _user = client

    response = test_client.post(
        "/mcp/tools/call",
        json={"tool": "queue.claim", "arguments": {}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_tool_arguments"


def test_call_queue_get_not_found_maps_404(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """queue.get should map missing jobs to job_not_found."""

    test_client, service, _user = client
    service.get_job.return_value = None

    response = test_client.post(
        "/mcp/tools/call",
        json={
            "tool": "queue.get",
            "arguments": {"jobId": str(uuid4())},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"
