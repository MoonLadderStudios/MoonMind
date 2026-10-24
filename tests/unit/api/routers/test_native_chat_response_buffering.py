"""Native reads must consume a bounded complete response before forwarding JSON."""

import asyncio
import json

import httpx
import pytest
from aiohttp import web

from api_service.api.routers import omnigent_bridge as bridge
from tests.unit.api.routers.test_omnigent_workflow_chat import (
    _CHAT_BINDING_ID,
    _PROVIDER_SESSION_ID,
    _build,
    _FakeStore,
    _path,
    _row,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    [
        "valid",
        "malformed",
        "empty",
        "oversized",
        "diagnostic",
        "denied_capture",
        "missing_transcript",
        "invalid_capture",
        "wrong_capture_identity",
        "empty_capture",
        "deleted_provider_identity",
    ],
)
async def test_native_transcript_read_is_complete_bounded_and_recoverable(
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

    upstream_calls = []

    async def stream(request):
        upstream_calls.append(request.path)
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
    store = None
    captured = shape in {
        "diagnostic",
        "denied_capture",
        "missing_transcript",
        "invalid_capture",
        "wrong_capture_identity",
        "empty_capture",
        "deleted_provider_identity",
    }
    if captured:

        class _Gateway:
            async def read_text(self, ref):
                if shape == "denied_capture":
                    raise bridge.OmnigentArtifactError("Synthetic denied artifact")
                value = {"status": "failed", "summary": "Synthetic transport error"}
                if shape == "empty_capture":
                    value = {}
                if shape == "invalid_capture":
                    value["items"] = "malformed"
                if shape == "wrong_capture_identity":
                    value["id"] = "another-provider-session"
                return json.dumps(value)

        monkeypatch.setattr(bridge, "LocalOmnigentArtifactGateway", _Gateway)
        row = _row(
            status="failed",
            final_snapshot_ref="artifact://omnigent/diagnostic",
            omnigent_session_id=(
                "" if shape == "missing_transcript" else _PROVIDER_SESSION_ID
            ),
        )
        row.metadata_["capabilityAuthority"]["providerSessionId"] = _PROVIDER_SESSION_ID
        if shape == "deleted_provider_identity":
            row.metadata_[bridge.PROVIDER_SESSION_DELETED_KEY] = "2026-01-01T00:00:00Z"
        store = _FakeStore(row=row)
    client, proxy, _store = _build(store=store)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://moonmind.test",
        ) as http:
            response = await http.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/items"))
        if shape in {"valid", "diagnostic"}:
            assert response.status_code == 200, response.text
            assert response.json() == json.loads(body)
        elif captured:
            assert response.status_code == 503, response.text
            assert (
                response.json()["detail"]["code"]
                == "omnigent_bridge_terminal_evidence_unavailable"
            )
        else:
            assert response.status_code == 502, response.text
            assert response.json()["detail"]["code"] in {
                "omnigent_bridge_upstream_payload",
                bridge.CODE_PAYLOAD_TOO_LARGE,
            }
        if shape == "diagnostic":
            for suffix in ("v1/sessions", f"v1/sessions/{_CHAT_BINDING_ID}"):
                response = client.get(_path(suffix))
                assert response.status_code == 200, response.text
                assert _PROVIDER_SESSION_ID not in response.text
                projected = response.json()
                if suffix == "v1/sessions":
                    projected = projected["data"][0]
                assert projected["readOnly"] is True
            assert proxy.sessions == [_PROVIDER_SESSION_ID, _PROVIDER_SESSION_ID]
            denied = client.post(
                _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
                json={"type": "message", "data": {"content": []}},
            )
            assert denied.status_code in {403, 409}
            assert proxy.posted == []
        elif captured:
            for suffix in ("v1/sessions", f"v1/sessions/{_CHAT_BINDING_ID}"):
                response = client.get(_path(suffix))
                assert response.status_code == 503, response.text
            assert upstream_calls == []
            assert proxy.sessions == []
    finally:
        await runner.cleanup()
