"""MM-990 tests for the thin Omnigent HTTP client."""

from __future__ import annotations

import httpx
import pytest

from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
    parse_sse_line,
)


@pytest.mark.asyncio
async def test_omnigent_client_exposes_confirmed_operations() -> None:
    assert not hasattr(OmnigentHttpClient, "get_workspace_diff")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/agents":
            return httpx.Response(
                200,
                json={"agents": [{"id": "ag_1", "name": "codex"}]},
            )
        if request.method == "DELETE":
            assert request.url.path == "/v1/sessions/sess_1"
            assert request.url.query == b"delete_branch=false"
        return httpx.Response(200, json={"ok": True})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        api_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.list_agents() == [{"id": "ag_1", "name": "codex"}]
    assert await client.get_agent("ag_1") == {"ok": True}
    assert await client.create_agent_bundle(
        filename="bundle.tgz",
        content=b"x",
    ) == {"ok": True}
    assert await client.create_session({"agent_id": "ag_1"}) == {"ok": True}
    assert await client.get_session("sess_1") == {"ok": True}
    assert await client.post_event("sess_1", {"type": "message"}) == {"ok": True}
    assert await client.resolve_elicitation(
        "sess_1",
        "el_1",
        {"answer": "yes"},
    ) == {"ok": True}
    assert await client.list_changed_files("sess_1") == {"ok": True}
    assert await client.list_session_files("sess_1") == {"ok": True}
    assert await client.interrupt("sess_1") == {"ok": True}
    assert await client.stop_session("sess_1") == {"ok": True}
    assert await client.delete_session("sess_1") == {"ok": True}


@pytest.mark.asyncio
async def test_omnigent_client_structures_and_redacts_non_2xx_diagnostics() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            503,
            json={
                "error": "bad",
                "apiKey": "secret-token",
                "authorization": "Bearer secret-token",
            },
        )

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        api_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(OmnigentClientError) as exc:
        await client.get_session("sess_1")

    diagnostics = exc.value.diagnostics()
    assert diagnostics["statusCode"] == 503
    assert diagnostics["failureClass"] == "integration_error"
    assert "secret-token" not in str(diagnostics)
    assert diagnostics["responseBody"]["apiKey"] == "[REDACTED]"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_failure_class"),
    [
        (400, "user_error"),
        (404, "user_error"),
        (401, "integration_error"),
        (403, "integration_error"),
        (429, "integration_error"),
        (503, "integration_error"),
    ],
)
async def test_omnigent_client_maps_http_failures_to_canonical_classes(
    status_code: int,
    expected_failure_class: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "bad target"})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(OmnigentClientError) as exc:
        await client.create_session({"agent_id": "ag_1"})

    assert exc.value.failure_class == expected_failure_class


def test_parse_sse_line_redacts_payload_and_rejects_malformed_frames() -> None:
    parsed = parse_sse_line(
        'data: {"type":"message","token":"sensitive-value"}'
    )
    assert parsed == {"type": "message", "token": "[REDACTED]"}

    with pytest.raises(OmnigentClientError, match="Malformed Omnigent SSE frame"):
        parse_sse_line("data: not-json")
