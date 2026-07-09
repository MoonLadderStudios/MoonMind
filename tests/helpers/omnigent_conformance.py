"""Reusable fake Omnigent host and bridge conformance helpers.

The fake host intentionally exposes stock Omnigent-shaped HTTP, SSE, and
resource endpoints. MoonMind-specific assertions live in tests that drive this
host through the bridge facade or execution adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aiohttp import web

BRIDGE_CONFORMANCE_SCENARIOS: tuple[str, ...] = (
    "successful_session_with_streamed_assistant_output",
    "failed_session_with_diagnostics",
    "stream_disconnect_and_snapshot_reconciliation",
    "retry_after_session_create_before_first_message",
    "retry_after_posting_state",
    "digest_mismatch_under_same_idempotency_key",
    "optional_diff_unavailable",
    "child_session_event_capture",
    "cancellation_via_interrupt_and_stop_session",
)


@dataclass(frozen=True, slots=True)
class RunningFakeOmnigentServer:
    server: FakeOmnigentServer
    base_url: str
    runner: web.AppRunner


class FakeOmnigentServer:
    """Importable fake Omnigent host for bridge conformance tests."""

    def __init__(
        self,
        *,
        supports_diff: bool = True,
        terminal_status: str = "completed",
        stream_disconnect: bool = False,
        include_child_session_event: bool = False,
    ) -> None:
        self.supports_diff = supports_diff
        self.terminal_status = terminal_status
        self.stream_disconnect = stream_disconnect
        self.include_child_session_event = include_child_session_event
        self.session_ids: list[str] = []
        self.create_payloads: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.first_message_posted = asyncio.Event()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/agents", self.list_agents)
        app.router.add_post("/v1/sessions", self.create_session)
        app.router.add_get("/v1/sessions/{session_id}", self.get_session)
        app.router.add_post("/v1/sessions/{session_id}/events", self.post_event)
        app.router.add_delete("/v1/sessions/{session_id}", self.delete_session)
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
        self.create_payloads.append(payload)
        session_id = f"session-{len(self.session_ids) + 1}"
        self.session_ids.append(session_id)
        return web.json_response({"id": session_id})

    async def get_session(self, request: web.Request) -> web.Response:
        status = (
            self.terminal_status if self.first_message_posted.is_set() else "running"
        )
        payload: dict[str, Any] = {
            "id": request.match_info["session_id"],
            "status": status,
            "summary": (
                "fake Omnigent failed"
                if status == "failed"
                else "fake Omnigent completed"
            ),
            "githubPrUrl": "https://github.example/org/repo/pull/42",
        }
        if status == "failed":
            payload["diagnostics"] = [
                {"code": "fake_failure", "message": "fake diagnostics"}
            ]
        return web.json_response(payload)

    async def post_event(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.events.append(payload)
        if payload.get("type") not in {"interrupt", "stop_session"}:
            self.first_message_posted.set()
        return web.json_response({"pending_id": "pending-1", "item_id": "item-1"})

    async def delete_session(self, request: web.Request) -> web.Response:
        self.events.append(
            {
                "type": "delete_session",
                "session_id": request.match_info["session_id"],
                "delete_branch": request.query.get("delete_branch"),
            }
        )
        return web.json_response({"ok": True})

    async def stream(self, request: web.Request) -> web.StreamResponse:
        await self.first_message_posted.wait()
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await response.prepare(request)
        await response.write(b'data: {"session":{"status":"running"}}\n\n')
        if self.include_child_session_event:
            await response.write(
                b'data: {"type":"session.child.created",'
                b'"session":{"id":"child-1"}}\n\n'
            )
        if self.stream_disconnect:
            await response.write_eof()
            return response
        await response.write(
            f'data: {{"type":"response.completed","session":{{"status":'
            f'"{self.terminal_status}"}}}}\n\n'.encode("utf-8")
        )
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


async def start_fake_omnigent_server(
    server: FakeOmnigentServer,
) -> RunningFakeOmnigentServer:
    runner = web.AppRunner(server.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]
    return RunningFakeOmnigentServer(
        server=server,
        base_url=f"http://{host}:{port}",
        runner=runner,
    )
