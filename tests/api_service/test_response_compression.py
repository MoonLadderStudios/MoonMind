"""Gzip for dashboard assets and JSON; streams stay uncompressed and unbuffered."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from api_service.dashboard_static import DashboardStaticFiles
from api_service.main import app as main_app
from api_service.response_compression import add_response_compression

GZIP = {"Accept-Encoding": "gzip, br"}


def _app(dist_root: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/api/items")
    async def items() -> dict[str, list[str]]:
        return {"items": ["workflow"] * 500}

    async def _chunks(prefix: str):
        for index in range(3):
            yield f"{prefix} {index}\n".encode()
            await asyncio.sleep(0)

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(_chunks("data:"), media_type="text/event-stream")

    @app.get("/api/logs/stream")
    async def logs() -> StreamingResponse:
        return StreamingResponse(_chunks("log line"), media_type="text/plain")

    app.mount(
        "/static/workflow_console/dist",
        DashboardStaticFiles(directory=str(dist_root)),
        name="workflow-console-dist",
    )
    add_response_compression(app)
    return app


def test_compresses_dashboard_assets_and_json(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    script = "console.log('dashboard chunk');\n" * 200
    (assets / "dashboard-abc123.js").write_text(script, encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        asset = client.get(
            "/static/workflow_console/dist/assets/dashboard-abc123.js", headers=GZIP
        )
        data = client.get("/api/items", headers=GZIP)
        identity = client.get("/api/items", headers={"Accept-Encoding": "identity"})

    assert asset.headers["content-encoding"] == "gzip"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.text == script
    assert data.headers["content-encoding"] == "gzip"
    assert data.json() == {"items": ["workflow"] * 500}
    assert "content-encoding" not in identity.headers


async def _asgi_messages(app: FastAPI, path: str) -> list[dict]:
    """Drive the ASGI app directly: TestClient joins streamed chunks."""

    sent: list[dict] = []
    request_sent = False
    never = asyncio.Event()

    async def receive() -> dict:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"accept-encoding", b"gzip, br")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return sent


def test_leaves_event_and_log_streams_uncompressed_and_unbuffered(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    for path, prefix in (("/api/events", "data:"), ("/api/logs/stream", "log line")):
        messages = asyncio.run(_asgi_messages(app, path))

        start = messages[0]
        headers = {key.decode().lower() for key, _ in start["headers"]}
        assert "content-encoding" not in headers
        chunks = [m["body"] for m in messages[1:] if m.get("body")]
        # Each chunk is sent as written, not held back for compression.
        assert chunks == [f"{prefix} {index}\n".encode() for index in range(3)]


def test_main_app_compresses_json_responses() -> None:
    response = TestClient(main_app).get("/openapi.json", headers=GZIP)

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["openapi"]
