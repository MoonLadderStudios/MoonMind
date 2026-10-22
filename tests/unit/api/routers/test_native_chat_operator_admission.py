"""Mounted native chat uses real account-free admission, never a person stub."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import omnigent_bridge as bridge
from api_service.api.routers import omnigent_native_ui as ui
from api_service.db.base import get_async_session
from api_service.db.models import Base, User
from api_service.services.single_user_conversion import SingleUserConversionRun
from moonmind.security import auth_modes_4120
from tests.unit.api.routers.test_omnigent_native_ui import _INDEX_HTML, _FakeUpstream
from tests.unit.api.routers.test_omnigent_workflow_chat import (
    _fake_registry,
    _FakeProxy,
    _FakeStore,
    _row,
)

pytestmark = pytest.mark.asyncio
BINDING = "chatb_account_free"
BASE = f"/api/workflow-chat-bindings/{BINDING}/omnigent/v1/sessions/{BINDING}"


@pytest_asyncio.fixture
async def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    for key in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/chat.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            SingleUserConversionRun(
                preflight_digest="chat-fresh",
                status="complete",
                result_json={
                    "published": True,
                    "decision": {"disposition": "fresh_init"},
                },
            )
        )
        await session.commit()

    async def sessions():
        async with factory() as session:
            original_get = session.get

            async def get(model, *args, **kwargs):
                assert model is not User, "native chat must not resolve a person"
                return await original_get(model, *args, **kwargs)

            session.get = get
            yield session

    row = _row(chat_binding_id=BINDING)
    row.metadata_.pop("callerAuthorities")
    store = _FakeStore(row=row)
    proxy = _FakeProxy()
    upstream = _FakeUpstream(
        {
            "/": ui.NativeUiUpstreamResponse(
                status_code=200, content=_INDEX_HTML, media_type="text/html"
            )
        }
    )
    app = FastAPI()
    app.include_router(
        bridge.workflow_chat_router, prefix=bridge.WORKFLOW_CHAT_BINDINGS_MOUNT_PATH
    )
    app.include_router(ui.native_ui_router, prefix=ui.NATIVE_UI_MOUNT_PATH)
    app.dependency_overrides[get_async_session] = sessions
    app.dependency_overrides[bridge._get_execution_service] = lambda: None
    app.dependency_overrides[bridge._get_bridge_store] = lambda: store
    app.dependency_overrides[bridge._get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[bridge._get_create_embedded_facade] = lambda: None
    app.dependency_overrides[bridge.get_capability_registry] = _fake_registry
    app.dependency_overrides[bridge._require_bridge_enabled] = lambda: SimpleNamespace(
        enabled=True, host_protocol_mode="upstream_omnigent_server_proxy"
    )
    app.dependency_overrides[ui.get_native_ui_upstream] = lambda: upstream
    yield SimpleNamespace(
        app=app, factory=factory, store=store, proxy=proxy, row=row, upstream=upstream
    )
    await engine.dispose()


async def test_account_free_bootstrap_messages_reconnect_and_interrupt(chat):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        bootstrap = await client.get(f"/omnigent-ui/workflow-chat/{BINDING}?embedded=1")
        assert bootstrap.status_code == 200, bootstrap.text
        assert '"sendMessage":true' in bootstrap.text
        for key in ("first-message", "follow-up"):
            response = await client.post(
                BASE + "/events",
                json={"type": "message", "data": {"text": key}},
                headers={"Idempotency-Key": key},
            )
            assert response.status_code == 200, response.text
            replay = await client.post(
                BASE + "/events",
                json={"type": "message", "data": {"text": key}},
                headers={"Idempotency-Key": key},
            )
            assert replay.status_code == 200, replay.text
        reconnect = await client.post(BASE + "/reconnect", json={})
        assert reconnect.status_code == 200, reconnect.text
        assert len(chat.proxy.posted) == 2
        response = await client.post(BASE + "/events", json={"type": "interrupt"})
        assert response.status_code == 200, response.text
    assert {item["session_id"] for item in chat.proxy.posted} == {
        chat.row.omnigent_session_id
    }
    assert chat.proxy.stopped == []
    assert chat.store.lifecycle
    assert all(
        item["metadata"].get("actor") == "operator"
        for item in chat.store.lifecycle
        if item["metadata"] and "actor" in item["metadata"]
    )
    async with chat.factory() as session:
        assert (await session.execute(select(User))).scalars().all() == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer runtime-token"},
        {
            "X-MoonMind-Execution-Fanout": "v1",
            "Authorization": "Bearer invalid-capability",
        },
        {"X-MoonMind-Worker-Token": "retired-worker-token"},
        {"X-Moonmind-User": "forged-operator"},
    ],
)
async def test_machine_peer_cannot_open_native_chat(chat, headers):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app, client=("172.20.0.7", 51234)),
        base_url="http://localhost",
    ) as client:
        for path in (BASE, f"/omnigent-ui/workflow-chat/{BINDING}"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 401, response.text
    assert not chat.proxy.sessions and not chat.upstream.paths


async def test_operator_chat_still_denies_cross_site_and_session_substitution(chat):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            BASE + "/events",
            json={"type": "message", "data": {"text": "evil"}},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403, response.text
        response = await client.get(
            BASE.replace("/sessions/" + BINDING, "/sessions/foreign")
        )
        assert response.status_code == 403, response.text
    assert not chat.proxy.posted and not chat.proxy.sessions


async def test_operator_keeps_approval_control_under_bound_policy(chat):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            BASE + "/elicitations/el-9/resolve", json={"decision": "approve"}
        )
        assert response.status_code == 200, response.text
        assert len(chat.proxy.resolved) == 1
        assert chat.proxy.resolved[0]["session_id"] == chat.row.omnigent_session_id
        chat.row.metadata_["capabilityAuthority"]["launchPolicy"][
            "resolveElicitation"
        ] = False
        response = await client.post(
            BASE + "/elicitations/el-10/resolve", json={"decision": "approve"}
        )
        assert response.status_code == 403, response.text
        assert len(chat.proxy.resolved) == 1


@pytest.mark.parametrize("change", ["stale", "generation", "profile", "operator"])
async def test_operator_cannot_bypass_binding_capability_authority(chat, change):
    authority = chat.row.metadata_["capabilityAuthority"]
    if change == "stale":
        authority["fresh"] = False
    elif change == "generation":
        authority["providerProfileGeneration"] += 1
    elif change == "profile":
        authority["agentProfile"]["sendMessage"] = False
    else:
        chat.row.metadata_["callerAuthorities"] = {"operator": {"viewTranscript": True}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        response = await client.post(
            BASE + "/events",
            json={"type": "message", "data": {"text": "not authorized"}},
        )
        assert response.status_code == 403, response.text
    assert not chat.proxy.posted and not chat.store.claimed


async def test_browser_disconnect_reconnect_and_retry_preserve_session(chat):
    from fastapi.testclient import TestClient

    from tests.unit.api.routers.test_omnigent_workflow_chat import _event

    payload = {"type": "message", "data": {"text": "keep running"}}
    with TestClient(
        chat.app, base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        sent = client.post(
            BASE + "/events", json=payload, headers={"Idempotency-Key": "browser-loss"}
        )
        assert sent.status_code == 200, sent.text
        chat.store._events = [_event(1)]
        with client.websocket_connect(
            "ws://localhost" + BASE + "/stream", headers={"Origin": "http://localhost"}
        ) as socket:
            assert socket.receive_json()["sequence"] == 1
        assert chat.proxy.stopped == []
        assert chat.row.status == "active"
        chat.store._events.append(_event(2))
        chat.row.status = "completed"
        with client.websocket_connect(
            "ws://localhost" + BASE + "/stream?cursor=1",
            headers={"Origin": "http://localhost"},
        ) as socket:
            event = socket.receive_json()
            assert event["sequence"] == 2
            assert chat.row.omnigent_session_id not in str(event)
            assert socket.receive_json()["type"] == "terminal"
        chat.row.status = "active"
        retry = client.post(
            BASE + "/events", json=payload, headers={"Idempotency-Key": "browser-loss"}
        )
        assert retry.status_code == 200, retry.text
        assert retry.json() == sent.json()
    assert len(chat.proxy.posted) == 1
    assert chat.proxy.stopped == []


async def test_open_websocket_rechecks_operator_ingress_without_canceling_work(
    chat, monkeypatch
):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from tests.unit.api.routers.test_omnigent_workflow_chat import _event

    monkeypatch.setattr(bridge, "_FACADE_STREAM_REAUTH_EVERY_POLLS", 1)
    chat.store._events = [_event(1)]
    with TestClient(
        chat.app, base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        with client.websocket_connect(
            "ws://localhost" + BASE + "/stream", headers={"Origin": "http://localhost"}
        ) as socket:
            assert socket.receive_json()["sequence"] == 1
            monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://approved.example")
            socket.send_text("poll")
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 4403
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "ws://localhost" + BASE + "/stream",
                headers={"Origin": "http://localhost"},
            ),
        ):
            pass
    assert chat.row.status == "active"
    assert chat.proxy.stopped == []


async def test_terminal_chat_evidence_survives_unavailable_provider(chat):
    chat.row.status = "completed"
    chat.row.omnigent_session_id = ""
    chat.row.terminal_refs = {"summary": "saved result"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        response = await client.get(BASE)
        assert response.status_code == 200, response.text
        assert response.json()["readOnly"] is True
        assert response.json()["providerSessionAvailable"] is False
    assert chat.proxy.sessions == []


async def test_actual_binding_discovery_uses_account_free_admission(chat, monkeypatch):
    from unittest.mock import AsyncMock

    from api_service.api.routers import executions
    from moonmind.omnigent.bridge_store import ChatBindingResolution

    service = SimpleNamespace(
        describe_execution=AsyncMock(
            return_value=SimpleNamespace(owner_id="retained-human")
        )
    )
    resolution = ChatBindingResolution(
        state="available",
        read_only=False,
        chat_binding_id=BINDING,
        workflow_id="mm:w1",
        run_id="run-1",
        step_execution_id="step-1",
        logical_step_id="step",
        capabilities={"viewTranscript": True},
        unavailable_reason=None,
    )
    monkeypatch.setattr(
        executions,
        "OmnigentBridgeSessionStore",
        lambda *_a: SimpleNamespace(
            resolve_chat_binding=AsyncMock(return_value=resolution)
        ),
    )
    chat.app.include_router(executions.router)
    chat.app.dependency_overrides[executions._get_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/executions/mm:w1/chat-binding")
        assert response.status_code == 200, response.text
        assert response.json()["chatBindingId"] == BINDING


@pytest.mark.parametrize(
    "credential",
    ["valid", "expired", "invalid", "empty", "fanout-marker", "worker-marker"],
)
async def test_presented_credentials_cannot_fall_back_to_local_operator(
    chat, credential
):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from moonmind.security.execution_fanout_capabilities import (
        mint_execution_fanout_capability,
        verify_execution_fanout_capability,
    )

    headers = {"Origin": "http://localhost"}
    if credential in {"valid", "expired"}:
        token = mint_execution_fanout_capability(
            secret="test-only-key",
            parent_workflow_id="mm:w1",
            agent_run_id="ar-1",
            session_id="session-1",
            runtime_id="runtime-1",
            source_kind="omnigent",
            lifetime_seconds=60,
            now=1 if credential == "expired" else None,
        )
        if credential == "valid":
            assert (
                verify_execution_fanout_capability(
                    token, secret="test-only-key"
                ).parent_workflow_id
                == "mm:w1"
            )
        headers["Authorization"] = f"Bearer {token}"
    elif credential == "fanout-marker":
        headers["X-MoonMind-Execution-Fanout"] = "v1"
    elif credential == "worker-marker":
        headers["X-MoonMind-Worker-Token"] = "retired-worker"
    else:
        headers["Authorization"] = "" if credential == "empty" else "Bearer invalid"
    with TestClient(
        chat.app, base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        for path in (BASE, f"/omnigent-ui/workflow-chat/{BINDING}"):
            response = client.get(path, headers=headers)
            assert response.status_code == 401, response.text
        with (
            pytest.raises(WebSocketDisconnect) as rejected,
            client.websocket_connect(
                "ws://localhost" + BASE + "/stream", headers=headers
            ),
        ):
            pass
        assert rejected.value.code == 4401
    assert not chat.proxy.sessions and not chat.upstream.paths


@pytest.fixture
def trusted_chat_clock(monkeypatch):
    from api_service.api import workflow_chat_access

    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "http://chat.example")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "172.20.0.7")
    monkeypatch.setenv("MOONMIND_PROXY_IDENTITY_NAMESPACE", "test-ingress")
    monkeypatch.delenv("MOONMIND_PROXY_IDENTITY_HEADER", raising=False)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        workflow_chat_access, "monotonic", lambda: clock.now, raising=False
    )
    return clock


async def test_cached_ingress_transcript_socket_expires_and_reconnects(
    chat, trusted_chat_clock
):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from moonmind.security.operator_admission import STREAM_REVALIDATION_SECONDS
    from tests.unit.api.routers.test_omnigent_workflow_chat import _event

    headers = {"Origin": "http://chat.example", "X-Moonmind-User": "asserted-operator"}
    chat.store._events = [_event(1)]
    with TestClient(
        chat.app, base_url="http://chat.example", client=("172.20.0.7", 50000)
    ) as client:
        payload = {"type": "message", "data": {"text": "keep working"}}
        message_headers = {**headers, "Idempotency-Key": "across-expiry"}
        accepted = client.post(BASE + "/events", json=payload, headers=message_headers)
        assert accepted.status_code == 200, accepted.text
        with client.websocket_connect(
            "ws://chat.example" + BASE + "/stream", headers=headers
        ) as socket:
            assert socket.receive_json()["sequence"] == 1
            trusted_chat_clock.now += STREAM_REVALIDATION_SECONDS
            chat.store._events.append(_event(2))
            socket.send_text("poll")
            with pytest.raises(WebSocketDisconnect) as expired:
                socket.receive_json()
            assert expired.value.code == 4403
        assert chat.row.status == "active"
        retry = client.post(BASE + "/events", json=payload, headers=message_headers)
        assert retry.status_code == 200, retry.text
        assert retry.json() == accepted.json()
        assert len(chat.proxy.posted) == 1
        # Cached ingress assertions cannot discover upstream revocation. A fresh
        # handshake requires a fresh assertion; the expired connection cannot
        # extend its own authority by rechecking its captured headers.
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "ws://chat.example" + BASE + "/stream?cursor=1",
                headers={"Origin": "http://chat.example"},
            ),
        ):
            pass
        chat.row.status = "completed"
        with client.websocket_connect(
            "ws://chat.example" + BASE + "/stream?cursor=1", headers=headers
        ) as socket:
            assert socket.receive_json()["sequence"] == 2
            assert socket.receive_json()["type"] == "terminal"
    assert chat.proxy.stopped == []


async def test_cached_ingress_active_sse_expires(chat, trusted_chat_clock, monkeypatch):
    from moonmind.security.operator_admission import STREAM_REVALIDATION_SECONDS
    from tests.unit.api.routers.test_omnigent_workflow_chat import _event

    original_get = chat.store.get_bridge_session
    original_page = chat.store.list_event_page
    chat.store._events = [_event(1)]

    async def get_session(*args, **kwargs):
        trusted_chat_clock.now = 100.0 + STREAM_REVALIDATION_SECONDS
        return await original_get(*args, **kwargs)

    async def page(*args, **kwargs):
        if kwargs["after"] >= 1:
            chat.store._events = [_event(1), _event(2)]
            chat.row.status = "completed"
        return await original_page(*args, **kwargs)

    chat.store.get_bridge_session = get_session
    chat.store.list_event_page = page
    monkeypatch.setattr(bridge, "_BRIDGE_STREAM_POLL_SECONDS", 0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat.app, client=("172.20.0.7", 50000)),
        base_url="http://chat.example",
    ) as client:
        response = await client.get(
            BASE + "/stream", headers={"X-Moonmind-User": "asserted-operator"}
        )
        assert response.status_code == 200, response.text
        assert "id: 1\n" in response.text
        assert "id: 2\n" not in response.text
        assert "event: error\n" in response.text
    assert chat.row.status == "active"
    assert chat.proxy.stopped == []


async def test_cached_ingress_native_relay_expires_without_browser_input(
    chat, trusted_chat_clock, monkeypatch
):
    import asyncio
    from unittest.mock import AsyncMock

    import aiohttp
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from moonmind.security.operator_admission import STREAM_REVALIDATION_SECONDS

    class Upstream:
        closed = False
        send_str = AsyncMock()
        send_bytes = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def close(self):
            self.closed = True

        def __aiter__(self):
            async def messages():
                yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="before expiry")
                await asyncio.sleep(0.01)
                trusted_chat_clock.now += STREAM_REVALIDATION_SECONDS
                await asyncio.sleep(0.03)
                yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="after expiry")

            return messages()

    upstream = Upstream()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def ws_connect(self, *_args, **_kwargs):
            return upstream

    monkeypatch.setattr(bridge.aiohttp, "ClientSession", lambda **kwargs: Client())
    monkeypatch.setattr(bridge, "_NATIVE_WS_REAUTH_SECONDS", 0.001)
    with (
        TestClient(
            chat.app, base_url="http://chat.example", client=("172.20.0.7", 50000)
        ) as client,
        client.websocket_connect(
            "ws://chat.example" + BASE + "/resources/terminals/t1/attach",
            headers={
                "Origin": "http://chat.example",
                "X-Moonmind-User": "asserted-operator",
            },
        ) as socket,
    ):
        assert socket.receive_text() == "before expiry"
        with pytest.raises(WebSocketDisconnect) as expired:
            socket.receive_text()
        assert expired.value.code == 4403
    assert upstream.closed
    upstream.send_str.assert_not_awaited()
    upstream.send_bytes.assert_not_awaited()
    assert chat.row.status == "active"
    assert chat.proxy.stopped == []


async def test_cached_revalidation_never_renews_admission_and_caps_wait(
    chat, trusted_chat_clock
):
    from fastapi import HTTPException, Request

    from api_service.api.workflow_chat_access import (
        get_workflow_chat_access,
        revalidate_workflow_chat_access,
        workflow_chat_stream_timeout,
    )
    from moonmind.security.operator_admission import STREAM_REVALIDATION_SECONDS

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "scheme": "http",
        "server": ("chat.example", 80),
        "client": ("172.20.0.7", 50000),
        "headers": [
            (b"host", b"chat.example"),
            (b"x-moonmind-user", b"asserted-operator"),
        ],
    }
    connection = Request(scope)
    async with chat.factory() as session:
        admission = await get_workflow_chat_access(connection, session)
    assert (
        workflow_chat_stream_timeout(connection, admission, 999)
        == STREAM_REVALIDATION_SECONDS
    )
    trusted_chat_clock.now += STREAM_REVALIDATION_SECONDS - 0.5
    await revalidate_workflow_chat_access(connection, admission)
    assert workflow_chat_stream_timeout(connection, admission, 2) == 0.5
    trusted_chat_clock.now += 0.5
    with pytest.raises(HTTPException):
        await revalidate_workflow_chat_access(connection, admission)
    fresh_scope = {key: value for key, value in scope.items() if key != "state"}
    with pytest.raises(HTTPException):
        workflow_chat_stream_timeout(Request(fresh_scope), admission, 2)
