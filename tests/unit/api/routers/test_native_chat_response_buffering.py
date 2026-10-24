"""Native reads must consume a bounded complete response before forwarding JSON."""

import asyncio
import json

import httpx
import pytest
from aiohttp import web

from api_service.api.routers import omnigent_bridge as bridge
from tests.unit.api.routers.test_omnigent_workflow_chat import (
    _CHAT_BINDING_ID,
    _build,
    _path,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["valid", "malformed", "empty", "oversized"])
async def test_native_chunked_json_is_complete_bounded_and_validated(
    monkeypatch, shape
):
    body = json.dumps(
        {
            "data": [
                {"id": "item-1", "text": "x" * (2000 if shape == "oversized" else 30)}
            ],
            "has_more": False,
        }
    ).encode()
    if shape == "malformed":
        body = body[:-1]
    elif shape == "empty":
        body = b""

    async def stream(request):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(body[:10])
        await asyncio.sleep(0.02)
        await response.write(body[10:])
        await response.write_eof()
        return response

    upstream = web.Application()
    upstream.router.add_get("/{path:.*}", stream)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        bridge, "resolved_server_url", lambda: f"http://127.0.0.1:{port}"
    )
    monkeypatch.setattr(bridge, "_FACADE_MAX_BODY_BYTES", 1024)
    client, _proxy, _store = _build()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://moonmind.test",
        ) as http:
            response = await http.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/items"))
        if shape == "valid":
            assert response.status_code == 200, response.text
            assert response.json() == json.loads(body)
        else:
            assert response.status_code == 502, response.text
            assert response.json()["detail"]["code"] in {
                "omnigent_bridge_upstream_payload",
                bridge.CODE_PAYLOAD_TOO_LARGE,
            }
    finally:
        await runner.cleanup()
