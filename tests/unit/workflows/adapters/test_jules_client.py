"""Unit tests for Jules async HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

pytestmark = [pytest.mark.asyncio]

_TASK_RESPONSE_DATA = {
    "taskId": "task-001",
    "status": "pending",
    "url": "https://jules.example.com/tasks/task-001",
}


def _make_client(
    handler,
    *,
    retry_attempts: int = 3,
    retry_delay_seconds: float = 0.0,
) -> JulesClient:
    transport = httpx.MockTransport(handler)
    injected = httpx.AsyncClient(transport=transport, base_url="https://jules.test")
    return JulesClient(
        base_url="https://jules.test",
        api_key="test-key",
        retry_attempts=retry_attempts,
        retry_delay_seconds=retry_delay_seconds,
        client=injected,
    )


# --- success tests ---


@pytest.mark.asyncio
async def test_create_task_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/tasks"
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler)
    result = await client.create_task(
        JulesCreateTaskRequest(title="Fix bug", description="Resolve issue #42")
    )
    assert result.task_id == "task-001"
    assert result.status == "pending"
    assert result.url == "https://jules.example.com/tasks/task-001"


@pytest.mark.asyncio
async def test_resolve_task_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/tasks/task-001/finish" in request.url.path
        body = json.loads(request.content)
        assert "taskId" not in body
        return httpx.Response(
            200,
            json={"taskId": "task-001", "status": "completed", "url": None},
        )

    client = _make_client(handler)
    result = await client.resolve_task(
        JulesResolveTaskRequest(
            task_id="task-001", resolution_notes="Done", status="completed"
        )
    )
    assert result.task_id == "task-001"
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_get_task_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/tasks/task-001" in request.url.path
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler)
    result = await client.get_task(JulesGetTaskRequest(task_id="task-001"))
    assert result.task_id == "task-001"


# --- retry tests ---


@pytest.mark.asyncio
async def test_create_task_retries_on_503():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler, retry_attempts=3, retry_delay_seconds=0.0)
    result = await client.create_task(
        JulesCreateTaskRequest(title="Retry test", description="Should retry")
    )
    assert result.task_id == "task-001"
    assert call_count == 2


@pytest.mark.asyncio
async def test_create_task_retries_on_429():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                text="Too Many Requests",
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler, retry_attempts=3, retry_delay_seconds=0.0)
    result = await client.create_task(
        JulesCreateTaskRequest(title="Rate limit", description="Should retry on 429")
    )
    assert result.task_id == "task-001"
    assert call_count == 2


@pytest.mark.asyncio
async def test_create_task_fails_on_400():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, text="Bad Request")

    client = _make_client(handler, retry_attempts=3, retry_delay_seconds=0.0)
    with pytest.raises(JulesClientError) as exc_info:
        await client.create_task(
            JulesCreateTaskRequest(title="Bad", description="Should fail immediately")
        )
    assert exc_info.value.status_code == 400
    assert call_count == 1


# --- structured error tests ---


@pytest.mark.asyncio
async def test_error_has_structured_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="Unprocessable")

    client = _make_client(handler)
    with pytest.raises(JulesClientError) as exc_info:
        await client.create_task(JulesCreateTaskRequest(title="X", description="Y"))
    err = exc_info.value
    assert err.status_code == 422
    assert err.request_path == "/tasks"


@pytest.mark.asyncio
async def test_error_str_does_not_leak_secrets():
    """Error string representation should not contain API keys or tokens."""
    api_key = "sk-secret-jules-key-12345"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Invalid key: {api_key}")

    transport = httpx.MockTransport(handler)
    injected = httpx.AsyncClient(transport=transport, base_url="https://jules.test")
    client = JulesClient(
        base_url="https://jules.test",
        api_key=api_key,
        client=injected,
    )
    with pytest.raises(JulesClientError) as exc_info:
        await client.create_task(JulesCreateTaskRequest(title="X", description="Y"))
    error_str = str(exc_info.value)
    assert api_key not in error_str
    assert "Bearer" not in error_str


# --- cleanup tests ---


@pytest.mark.asyncio
async def test_aclose_closes_owned_client():
    closed = False
    original_aclose = httpx.AsyncClient.aclose

    async def tracking_aclose(self):
        nonlocal closed
        closed = True
        await original_aclose(self)

    client = JulesClient(
        base_url="https://jules.test",
        api_key="test-key",
    )
    assert client._owns_client is True

    httpx.AsyncClient.aclose = tracking_aclose  # type: ignore[assignment]
    try:
        await client.aclose()
        assert closed
    finally:
        httpx.AsyncClient.aclose = original_aclose  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_aclose_skips_injected_client():
    """aclose() should not close an externally-injected client."""
    injected = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        base_url="https://jules.test",
    )
    client = JulesClient(
        base_url="https://jules.test",
        api_key="test-key",
        client=injected,
    )
    assert client._owns_client is False
    await client.aclose()
    # injected client should still be usable
    assert not injected.is_closed
    await injected.aclose()
