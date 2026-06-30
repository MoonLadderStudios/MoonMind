"""MM-1059 fake Omnigent server coverage for streaming-gateway execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web

from moonmind.omnigent.execute import (
    LocalOmnigentArtifactGateway,
    run_omnigent_execution,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class FakeOmnigentServer:
    def __init__(self, *, supports_diff: bool) -> None:
        self.supports_diff = supports_diff
        self.session_ids: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.first_message_posted = asyncio.Event()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/agents", self.list_agents)
        app.router.add_post("/v1/sessions", self.create_session)
        app.router.add_get("/v1/sessions/{session_id}", self.get_session)
        app.router.add_post("/v1/sessions/{session_id}/events", self.post_event)
        app.router.add_get("/v1/sessions/{session_id}/stream", self.stream)
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/environments/default/changes",
            self.list_changed_files,
        )
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/environments/default/filesystem",
            self.list_workspace_files,
        )
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/environments/default/"
            "filesystem/{path:.+}",
            self.get_workspace_file,
        )
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/environments/default/"
            "diff/{path:.+}",
            self.get_workspace_diff,
        )
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/files",
            self.list_session_files,
        )
        app.router.add_get(
            "/v1/sessions/{session_id}/resources/files/{file_id}/content",
            self.get_session_file_content,
        )
        return app

    async def list_agents(self, _request: web.Request) -> web.Response:
        return web.json_response({"agents": [{"id": "agent-1", "name": "codex"}]})

    async def create_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["agent_id"] == "agent-1"
        assert payload["workspace"] == "https://github.com/org/repo#main"
        assert payload["labels"]["moonmind.issue"] == "MM-1059"
        session_id = f"session-{len(self.session_ids) + 1}"
        self.session_ids.append(session_id)
        return web.json_response({"id": session_id})

    async def get_session(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": request.match_info["session_id"],
                "status": (
                    "completed" if self.first_message_posted.is_set() else "running"
                ),
                "summary": "fake Omnigent completed",
                "githubPrUrl": "https://github.example/org/repo/pull/42",
            }
        )

    async def post_event(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.events.append(payload)
        self.first_message_posted.set()
        return web.json_response({"pending_id": "pending-1"})

    async def stream(self, _request: web.Request) -> web.StreamResponse:
        await self.first_message_posted.wait()
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await response.prepare(_request)
        await response.write(b'data: {"session":{"status":"running"}}\n\n')
        await response.write(b'data: {"type":"response.completed"}\n\n')
        await response.write_eof()
        return response

    async def list_changed_files(self, _request: web.Request) -> web.Response:
        return web.json_response({"items": [{"path": "src/app.py"}]})

    async def list_workspace_files(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "items": [
                    {"path": "README.md", "type": "file"},
                    {"path": "src/app.py", "type": "file"},
                    {"path": "src", "type": "directory"},
                ]
            }
        )

    async def get_workspace_file(self, request: web.Request) -> web.Response:
        path = request.match_info["path"]
        body = {
            "README.md": b"# Fake repo\n",
            "src/app.py": b"print('fake')\n",
        }[path]
        return web.Response(body=body, content_type="text/plain")

    async def get_workspace_diff(self, request: web.Request) -> web.Response:
        if not self.supports_diff:
            return web.json_response({"error": "diff unavailable"}, status=404)
        path = request.match_info["path"]
        return web.Response(
            body=f"diff --git a/{path} b/{path}\n".encode("utf-8"),
            content_type="text/x-diff",
        )

    async def list_session_files(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"items": [{"id": "file-1", "filename": "session.log"}]}
        )

    async def get_session_file_content(self, _request: web.Request) -> web.Response:
        return web.Response(body=b"session file evidence\n", content_type="text/plain")


@pytest_asyncio.fixture
async def fake_omnigent_server(request: pytest.FixtureRequest):
    server = FakeOmnigentServer(supports_diff=bool(request.param))
    runner = web.AppRunner(server.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]
    try:
        yield server, f"http://{host}:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("fake_omnigent_server", [True, False], indirect=True)
async def test_omnigent_execute_harvests_resources_with_fake_server(
    fake_omnigent_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    server, server_url = fake_omnigent_server
    monkeypatch.setenv("OMNIGENT_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", server_url)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            workspaceSpec={
                "repository": "https://github.com/org/repo",
                "branch": "main",
            },
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex"},
                    "session": {"hostType": "managed"},
                    "prompt": {"text": "Implement MM-1059 fake-server scenario"},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.summary == "fake Omnigent completed"
    assert result.metadata["workspaceFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["sessionFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["githubPrUrl"] == "https://github.example/org/repo/pull/42"
    assert len(server.session_ids) == 1
    assert len(server.events) == 1

    manifest = json.loads(
        (tmp_path / "corr-1" / "output.omnigent.capture_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["workspaceFiles"][0]["path"] == "README.md"
    assert manifest["sessionFiles"][0]["filename"] == "session.log"
    if server.supports_diff:
        assert manifest["patchUnavailable"] is False
        assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"
    else:
        assert manifest["patchUnavailable"] is True
        assert "workspaceDiffsUnavailable" in manifest
