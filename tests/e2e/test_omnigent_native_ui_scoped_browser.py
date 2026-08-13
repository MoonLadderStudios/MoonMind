"""Compiled scoped-UI browser regression for MoonLadderStudios/MoonMind#3685."""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright

from api_service.api.routers.omnigent_bridge import (
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
)
from api_service.api.routers.omnigent_native_ui import (
    NATIVE_UI_MOUNT_PATH,
    NativeUiUpstreamResponse,
    get_native_ui_compatibility,
    get_native_ui_upstream,
    native_ui_router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
from moonmind.omnigent.native_ui import NativeUiCompatibility

_BINDING = "chatb_browser_router_opaque"
_PROVIDER_SESSION = "provider-session-must-stay-server-side"
_USER_ID = uuid4()
_UI_DIST = (
    Path(__file__).resolve().parents[2]
    / "omnigent"
    / "omnigent"
    / "server"
    / "static"
    / "web-ui"
)


def _reserve_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _DiskUpstream:
    async def fetch(self, path: str) -> NativeUiUpstreamResponse:
        relative = "index.html" if path == "/" else path.removeprefix("/")
        target = (_UI_DIST / relative).resolve()
        if _UI_DIST.resolve() not in target.parents or not target.is_file():
            return NativeUiUpstreamResponse(
                status_code=404,
                content=b"",
                media_type="application/octet-stream",
            )
        return NativeUiUpstreamResponse(
            status_code=200,
            content=target.read_bytes(),
            media_type=mimetypes.guess_type(target.name)[0]
            or "application/octet-stream",
        )


class _Store:
    def __init__(self, row: SimpleNamespace) -> None:
        self.row = row

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self.row if chat_binding_id == _BINDING else None


def _canonical_row() -> SimpleNamespace:
    grants = {name: True for name in CAPABILITY_NAMES}
    return SimpleNamespace(
        bridge_session_id="bridge-canonical",
        canonical_bridge_session_id=None,
        moonmind_workflow_id="mm:workflow-browser",
        moonmind_run_id="run-browser",
        moonmind_agent_run_id="agent-browser",
        chat_binding_id=_BINDING,
        status="active",
        omnigent_session_id=_PROVIDER_SESSION,
        provider_profile_id="provider-browser",
        credential_generation=1,
        effective_launch_snapshot_json={
            "executionProfileRef": "profile://browser/1",
            "executionProfileDigest": "sha256:profile",
            "launchPolicyRef": "policy://browser/1",
            "snapshotRef": "artifact://launch-browser",
            "policyAuthority": {
                "snapshotRef": "artifact://policy-browser",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "callerAuthorities": {str(_USER_ID): grants},
            "capabilityAuthority": {
                "schemaVersion": "moonmind.omnigent.capability-authority.v1",
                "fresh": True,
                "providerProfileGeneration": 1,
                "providerSessionId": _PROVIDER_SESSION,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 1, "capabilities": grants},
            },
        },
    )


@pytest.fixture(scope="module")
def scoped_ui_server():
    assert (_UI_DIST / "index.html").is_file(), "Build the Omnigent web UI first."
    row = _canonical_row()
    store = _Store(row)
    app = FastAPI()
    requests: list[tuple[str, str]] = []
    posts: list[dict] = []

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        requests.append((request.method, request.url.path))
        return await call_next(request)

    @app.api_route(
        "/api/workflow-chat-bindings/{chat_binding_id}/omnigent/{path:path}",
        methods=["GET", "POST"],
    )
    async def scoped_facade(
        request: Request, chat_binding_id: str, path: str
    ) -> Response:
        assert chat_binding_id == _BINDING
        if request.method == "POST":
            posts.append(await request.json())
            return JSONResponse({"status": "accepted"})
        if path == f"v1/sessions/{_BINDING}":
            return JSONResponse(
                {
                    "id": _BINDING,
                    "title": "Workflow Chat",
                    "status": "idle",
                    "created_at": 1,
                    "updated_at": 1,
                    "labels": {},
                    "can_approve": True,
                    "permission_level": 3,
                }
            )
        if path.endswith("health"):
            return JSONResponse({"status": "ok", "runner_online": True})
        if path.endswith("stream"):
            return Response(content="", media_type="text/event-stream")
        return JSONResponse({"data": [], "has_more": False})

    app.include_router(native_ui_router, prefix=NATIVE_UI_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=_USER_ID, email="browser@example.com", is_superuser=False
    )
    app.dependency_overrides[_get_execution_service] = lambda: SimpleNamespace()
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_require_bridge_enabled] = lambda: SimpleNamespace(
        enabled=True, host_protocol_mode="upstream"
    )
    app.dependency_overrides[get_native_ui_upstream] = _DiskUpstream
    app.dependency_overrides[get_native_ui_compatibility] = lambda: (
        NativeUiCompatibility(
            ready=True,
            reported_version="browser-test",
            supported_versions=("browser-test",),
        )
    )

    port = _reserve_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    else:
        pytest.fail("Scoped native UI test server did not start.")

    try:
        yield SimpleNamespace(
            base_url=f"http://127.0.0.1:{port}",
            requests=requests,
            posts=posts,
            row=row,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_compiled_spa_stays_on_canonical_binding_through_router(
    scoped_ui_server, tmp_path: Path
) -> None:
    base = scoped_ui_server.base_url
    ui_path = f"{NATIVE_UI_MOUNT_PATH}/{_BINDING}/c/{_BINDING}?embedded=1"
    api_base = f"/api/workflow-chat-bindings/{_BINDING}/omnigent"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(base + ui_path, wait_until="domcontentloaded")

        expect(page.get_by_text("Page not found", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Open sidebar")).to_have_count(0)
        composer = page.get_by_label("Message the agent")
        expect(composer).to_be_visible(timeout=30_000)
        composer.fill("Continue through the canonical binding.")
        with page.expect_request(
            lambda request: request.method == "POST"
            and request.url.endswith(
                f"{api_base}/v1/sessions/{_BINDING}/events"
            )
        ):
            page.get_by_role("button", name="Send", exact=True).click()
        assert scoped_ui_server.posts

        first_url = page.url
        page.reload(wait_until="domcontentloaded")
        expect(page).to_have_url(first_url)

        scoped_ui_server.row.status = "completed"
        state = scoped_ui_server.row.metadata_["capabilityAuthority"]["state"]
        state["capabilities"] = {**state["capabilities"], "sendMessage": False}
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_placeholder("This Workflow Chat is read-only")).to_be_visible(
            timeout=30_000
        )
        browser.close()

    paths = [path for _method, path in scoped_ui_server.requests]
    application_paths = [path for path in paths if path.startswith("/api/")]
    assert application_paths
    assert all(path.startswith(api_base + "/") for path in application_paths)
    assert f"{api_base}/v1/sessions" not in application_paths
    assert not any(path.startswith("/v1/") for path in paths)
    assert _PROVIDER_SESSION not in json.dumps(scoped_ui_server.requests)

    (tmp_path / "moonmind-native-ui-router-browser.json").write_text(
        json.dumps(
            {
                "issue": "MoonLadderStudios/MoonMind#3685",
                "chatBindingIdStable": first_url.endswith(ui_path),
                "scopedApplicationRequestCount": len(application_paths),
                "postedMessageCount": len(scoped_ui_server.posts),
                "rootV1RequestCount": 0,
                "terminalReadOnly": True,
            },
            sort_keys=True,
        )
    )
