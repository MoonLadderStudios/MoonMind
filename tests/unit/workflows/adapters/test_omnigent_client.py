"""MM-990 tests for the thin Omnigent HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
    aclose_shared_pool_client,
    default_omnigent_pool_limits,
    init_shared_pool_client,
    parse_sse_line,
    shared_pool_client,
)


@pytest.mark.asyncio
async def test_omnigent_client_exposes_confirmed_operations() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "GET" and request.url.path == "/v1/agents":
            assert request.url.params["limit"] == "1000"
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "ag_1",
                            "name": "codex-native-ui",
                            "harness": "codex-native",
                            "builtin": True,
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.method == "GET" and request.url.path == "/v1/hosts":
            return httpx.Response(
                200,
                json={
                    "hosts": [
                        {
                            "host_id": "host-1",
                            "name": "mm-host-1",
                            "status": "online",
                            "configured_harnesses": {"codex-native": True},
                        }
                    ]
                },
            )
        if request.method == "GET" and request.url.path.endswith("/diff/src/app.py"):
            return httpx.Response(200, content=b"diff --git a/src/app.py b/src/app.py\n")
        if request.method == "GET" and request.url.path.endswith(
            "/filesystem/src/app.py"
        ):
            return httpx.Response(200, content=b"print('ok')\n")
        if request.method == "DELETE":
            assert request.url.path == "/v1/sessions/sess_1"
            assert request.url.query == b"delete_branch=false"
        return httpx.Response(200, json={"ok": True})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        api_token="secret-token",
        transport=httpx.MockTransport(handler),
    )

    assert await client.list_agents() == [
        {
            "id": "ag_1",
            "name": "codex-native-ui",
            "harness": "codex-native",
            "builtin": True,
            "capabilities": ["session.start"],
        }
    ]
    assert await client.list_hosts() == [
        {
            "host_id": "host-1",
            "name": "mm-host-1",
            "status": "online",
            "configured_harnesses": {"codex-native": True},
        }
    ]
    assert "/v1/hosts" in requested_paths
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
    assert await client.list_workspace_files("sess_1") == {"ok": True}
    assert await client.get_workspace_file("sess_1", "src/app.py") == b"print('ok')\n"
    assert await client.get_workspace_diff(
        "sess_1",
        "src/app.py",
    ) == b"diff --git a/src/app.py b/src/app.py\n"
    assert await client.list_session_files("sess_1") == {"ok": True}
    assert await client.interrupt("sess_1") == {"ok": True}
    assert await client.stop_session("sess_1") == {"ok": True}
    assert await client.delete_session("sess_1") == {"ok": True}


@pytest.mark.asyncio
async def test_injected_http_client_preserves_omnigent_timeout_contract() -> None:
    """An injected client must not replace Omnigent's transport timeouts."""

    observed_timeouts: dict[str, dict[str, float | None]] = {}
    observed_event_timeouts: dict[str, dict[str, float | None]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        timeout = dict(request.extensions["timeout"])
        if request.url.path.endswith("/events"):
            payload = json.loads(request.content)
            observed_event_timeouts[str(payload["type"])] = timeout
        else:
            observed_timeouts[request.url.path] = timeout
        if request.url.path.endswith("/stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"data: [DONE]\n\n",
            )
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as injected_client:
        client = OmnigentHttpClient(
            base_url="https://omnigent.test",
            timeout_seconds=47.0,
            event_timeout_seconds=131.0,
            stream_timeout_seconds=None,
            client=injected_client,
        )

        assert await client.get_session("sess_1") == {"ok": True}
        assert await client.post_event("sess_1", {"type": "message"}) == {
            "ok": True
        }
        assert await client.interrupt("sess_1") == {"ok": True}
        assert await client.stop_session("sess_1") == {"ok": True}
        assert [event async for event in client.stream_events("sess_1")] == []

    ordinary_timeout = observed_timeouts["/v1/sessions/sess_1"]
    assert ordinary_timeout == {
        "connect": 47.0,
        "read": 47.0,
        "write": 47.0,
        "pool": 47.0,
    }
    assert observed_event_timeouts["message"] == {
        "connect": 131.0,
        "read": 131.0,
        "write": 131.0,
        "pool": 131.0,
    }
    assert observed_event_timeouts["interrupt"] == ordinary_timeout
    assert observed_event_timeouts["stop_session"] == ordinary_timeout
    stream_timeout = observed_timeouts["/v1/sessions/sess_1/stream"]
    assert stream_timeout == {
        "connect": 47.0,
        "read": None,
        "write": 47.0,
        "pool": 47.0,
    }


@pytest.mark.asyncio
async def test_omnigent_client_names_empty_transport_timeout() -> None:
    """httpx timeout strings may be empty, but diagnostics must identify them."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=request)

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        OmnigentClientError,
        match="Omnigent transport error: ReadTimeout",
    ):
        await client.post_event("sess_1", {"type": "message"})


@pytest.mark.asyncio
async def test_omnigent_client_follows_agent_catalog_cursor() -> None:
    requested_after: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        requested_after.append(after)
        if after is None:
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "ag_1", "name": "first"}],
                    "last_id": "ag_1",
                    "has_more": True,
                },
            )
        assert after == "ag_1"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "ag_2", "name": "codex-native-ui"}],
                "last_id": "ag_2",
                "has_more": False,
            },
        )

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )

    assert [agent["id"] for agent in await client.list_agents()] == ["ag_1", "ag_2"]
    assert requested_after == [None, "ag_1"]


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
    assert parse_sse_line("event: response.created") is None

    parsed = parse_sse_line(
        'data: {"type":"message","token":"sensitive-value"}'
    )
    assert parsed == {"type": "message", "token": "[REDACTED]"}

    with pytest.raises(OmnigentClientError, match="Malformed Omnigent SSE frame"):
        parse_sse_line("data: not-json")


@pytest.mark.asyncio
async def test_omnigent_client_does_not_leak_moonmind_auth_headers_upstream() -> None:
    """§16 rule 7: proxy mode must not forward MoonMind internal auth headers."""

    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        api_token="server-token",
        transport=httpx.MockTransport(handler),
        forward_headers={
            "Authorization": "Bearer moonmind-user-token",
            "Cookie": "session=abc",
            "X-MoonMind-Auth": "internal",
            "X-Trace-Id": "trace-1",
        },
    )

    await client.create_session({"agent_id": "ag_1"})

    assert "moonmind-user-token" not in captured.get("authorization", "")
    assert captured["authorization"] == "Bearer server-token"
    assert "cookie" not in captured
    assert "x-moonmind-auth" not in captured
    assert "x-trace-id" not in captured


@pytest.mark.asyncio
async def test_omnigent_client_forwards_explicitly_allowlisted_headers() -> None:
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
        forward_headers={"X-MoonMind-Trace": "trace-1", "Cookie": "session=abc"},
        upstream_header_allowlist=["x-moonmind-trace"],
    )

    await client.create_session({"agent_id": "ag_1"})

    assert captured["x-moonmind-trace"] == "trace-1"
    assert "cookie" not in captured


def test_pool_limits_are_bounded_and_overridable(monkeypatch) -> None:
    limits = default_omnigent_pool_limits()
    assert 10 <= limits.max_connections <= 500
    assert 1 <= limits.max_keepalive_connections <= 100

    monkeypatch.setenv("MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS", "10000")
    capped = default_omnigent_pool_limits()
    assert capped.max_connections <= 500


@pytest.mark.asyncio
async def test_shared_pool_reused_and_closed() -> None:
    await aclose_shared_pool_client()
    assert shared_pool_client() is None
    first = await init_shared_pool_client()
    assert shared_pool_client() is first
    assert (await init_shared_pool_client()) is first
    await aclose_shared_pool_client()
    assert shared_pool_client() is None


@pytest.mark.asyncio
async def test_pool_exhaustion_returns_actionable_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.PoolTimeout("pool exhausted")

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OmnigentClientError, match="pool exhausted"):
        await client.list_agents()
