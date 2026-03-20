"""Unit tests for Jules async HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesIntegrationStartRequest,
    JulesResolveTaskRequest,
    normalize_jules_status,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

pytestmark = [pytest.mark.asyncio]

_TASK_RESPONSE_DATA = {
    "id": "task-001",
    "state": "pending",
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
        assert request.url.path == "/sessions"
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
        assert "/sessions/task-001/finish" in request.url.path
        body = json.loads(request.content)
        assert "taskId" not in body
        return httpx.Response(
            200,
            json={"id": "task-001", "state": "completed", "url": None},
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
        assert "/sessions/task-001" in request.url.path
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler)
    result = await client.get_task(JulesGetTaskRequest(task_id="task-001"))
    assert result.task_id == "task-001"


@pytest.mark.asyncio
async def test_normalize_jules_status_maps_terminal_and_running_states():
    assert normalize_jules_status("pending") == "queued"
    assert normalize_jules_status("in_progress") == "running"
    assert normalize_jules_status("completed") == "succeeded"
    assert normalize_jules_status("canceled") == "canceled"
    assert normalize_jules_status("mystery") == "unknown"
    # Actual Jules API State enum values
    assert normalize_jules_status("QUEUED") == "queued"
    assert normalize_jules_status("PLANNING") == "running"
    assert normalize_jules_status("AWAITING_PLAN_APPROVAL") == "running"
    assert normalize_jules_status("AWAITING_USER_FEEDBACK") == "running"
    assert normalize_jules_status("IN_PROGRESS") == "running"
    assert normalize_jules_status("PAUSED") == "running"
    assert normalize_jules_status("FAILED") == "failed"
    assert normalize_jules_status("COMPLETED") == "succeeded"


@pytest.mark.asyncio
async def test_create_task_sends_correct_source_context_format():
    """Verify that sourceContext is serialized per the Jules API spec."""
    from moonmind.schemas.jules_models import SourceContext

    captured_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json=_TASK_RESPONSE_DATA)

    client = _make_client(handler)
    source_ctx = SourceContext.from_repo("owner/repo", branch="develop")
    await client.create_task(
        JulesCreateTaskRequest(
            title="Test",
            description="Check source context",
            source_context=source_ctx,
        )
    )

    assert captured_body["sourceContext"] == {
        "source": "sources/github/owner/repo",
        "githubRepoContext": {"startingBranch": "develop"},
    }
    assert captured_body["prompt"] == "Check source context"



@pytest.mark.asyncio
async def test_start_integration_builds_provider_neutral_result():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "prompt" in body  # description mapped to prompt
        return httpx.Response(
            200,
            json={
                "id": "task-123",
                "state": "pending",
                "url": "https://jules.example.com/tasks/task-123",
            },
        )

    client = _make_client(handler)
    result = await client.start_integration(
        JulesIntegrationStartRequest(
            correlationId="corr-1",
            idempotencyKey="idem-1",
            title="MoonMind run",
            description="Monitor this task",
            inputRefs=["art_1"],
            parameters={"prompt": "hello"},
            callbackUrl="https://moonmind.example.test/callback",
            callbackCorrelationKey="cb-1",
        ),
        recommended_poll_seconds=15,
    )

    assert result.external_operation_id == "task-123"
    assert result.normalized_status == "queued"
    assert result.callback_supported is True
    assert result.recommended_poll_seconds == 15
    assert result.idempotency_key == "idem-1"


@pytest.mark.asyncio
async def test_start_integration_marks_transport_timeout_as_ambiguous():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _make_client(handler, retry_attempts=1, retry_delay_seconds=0.0)

    with pytest.raises(JulesClientError) as exc_info:
        await client.start_integration(
            JulesIntegrationStartRequest(
                correlationId="corr-timeout",
                idempotencyKey="idem-timeout",
                title="MoonMind run",
                description="Monitor this task",
            )
        )

    assert exc_info.value.ambiguous is True


@pytest.mark.asyncio
async def test_fetch_and_cancel_integration_return_normalized_results():
    step = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        step["count"] += 1
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "task-123",
                    "state": "completed",
                    "url": "https://jules.example.com/tasks/task-123",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "task-123",
                "state": "canceled",
                "url": "https://jules.example.com/tasks/task-123",
            },
        )

    client = _make_client(handler)
    fetched = await client.fetch_integration_result(
        external_operation_id="task-123",
        result_refs=["art_result"],
    )
    canceled = await client.cancel_integration(external_operation_id="task-123")

    assert fetched.normalized_status == "succeeded"
    assert fetched.output_refs == ["art_result"]
    assert canceled.accepted is True
    assert canceled.normalized_status == "canceled"


@pytest.mark.asyncio
async def test_cancel_integration_reports_unsupported_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="Method not allowed")

    client = _make_client(handler, retry_attempts=1, retry_delay_seconds=0.0)
    result = await client.cancel_integration(external_operation_id="task-404")

    assert result.accepted is False
    assert result.unsupported is True


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
    assert err.request_path == "/sessions"


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


@pytest.mark.asyncio
async def test_send_message_success():
    """send_message() should POST to /sessions/{id}:sendMessage."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "POST"
        assert "/sessions/session-42:sendMessage" in request.url.path
        body = json.loads(request.content)
        assert body == {"prompt": "Continue with step 2."}
        return httpx.Response(200, content=b"")

    client = _make_client(handler)
    from moonmind.schemas.jules_models import JulesSendMessageRequest

    await client.send_message(
        JulesSendMessageRequest(sessionId="session-42", prompt="Continue with step 2.")
    )
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_send_message_retries_on_503():
    """send_message() should retry on 503."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"")

    client = _make_client(handler, retry_attempts=3, retry_delay_seconds=0.0)
    from moonmind.schemas.jules_models import JulesSendMessageRequest

    await client.send_message(
        JulesSendMessageRequest(sessionId="session-42", prompt="retry prompt")
    )
    assert call_count == 3
