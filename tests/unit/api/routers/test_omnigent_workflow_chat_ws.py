"""WebSocket facade tests for the binding-scoped Workflow Chat surface.

MoonLadderStudios/MoonMind#3635: every native-UI WebSocket connection (session
channel, terminal/PTY, browser pane, sub-agent/task, reconnect) must
independently authenticate, resolve the durable binding, authorize the caller,
reject caller-supplied identity, capability-check from recomputed state, and
validate the transport against the versioned compatibility map *before* upgrade.
Reviewed transports are relayed through a bounded server-owned bridge; unknown
transports fail closed non-enumeratingly; terminal/revoked
bindings and read-only viewers cannot open a live/mutating transport.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import aiohttp
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_service.api.routers.omnigent_bridge import (
    WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    WS_CLOSE_BINDING_UNKNOWN,
    WS_CLOSE_CAPABILITY_DENIED,
    WS_CLOSE_READ_ONLY,
    WS_CLOSE_SESSION_NOT_READY,
    WS_CLOSE_SUBPROTOCOL_REJECTED,
    WS_CLOSE_TRANSPORT_UNSUPPORTED,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_create_embedded_facade,
    _get_execution_service,
    _filter_session_updates_watch_frame,
    _relay_native_websocket,
    _require_bridge_enabled,
    _virtualize_session_updates_frame,
    workflow_chat_router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_PROXY
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES

_USER_ID = uuid4()
_UNSET = object()
_PROVIDER_SESSION_ID = "prov-sess-1"
_CHAT_BINDING_ID = "chatb-1"
_BRIDGE_SESSION_ID = "brs-internal-1"


@pytest.fixture(autouse=True)
def _stub_upstream_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    async def relay(*, browser: Any, **_: Any) -> None:
        await browser.accept()
        await browser.close(code=1000, reason="relayed")

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._relay_native_websocket", relay
    )


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="chat@example.com", is_superuser=False)


class _FakeService:
    def __init__(self, owner_id: Any) -> None:
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


def _row(**overrides: Any) -> SimpleNamespace:
    all_capabilities = {name: True for name in CAPABILITY_NAMES}
    caller_capabilities = {
        name: name
        in {"viewTranscript", "readResources", "viewTerminal", "viewSubagents"}
        for name in CAPABILITY_NAMES
    }
    values = dict(
        bridge_session_id=_BRIDGE_SESSION_ID,
        chat_binding_id=_CHAT_BINDING_ID,
        moonmind_workflow_id="mm:w1",
        moonmind_run_id="run-1",
        moonmind_agent_run_id="ar-1",
        step_execution_id="step-1",
        idempotency_key="idem-1",
        status="active",
        omnigent_session_id=_PROVIDER_SESSION_ID,
        omnigent_host_id="host-1",
        compatibility_profile="omnigent.server.v1",
        terminal_refs={},
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "callerAuthorities": {str(_USER_ID): caller_capabilities},
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": all_capabilities,
                "agentProfile": all_capabilities,
                "launchPolicy": all_capabilities,
                "state": {"sessionEpoch": 2, "capabilities": all_capabilities},
            },
        },
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeStore:
    def __init__(self, *, row: Any = _UNSET) -> None:
        self._row = _row() if row is _UNSET else row
        self.lifecycle: list[dict[str, Any]] = []
        self.claimed: set[str] = set()

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self._row

    async def claim_lifecycle_event(
        self, _idempotency_key: str, *, event_identity: str, metadata=None, **kwargs
    ) -> bool:
        if event_identity in self.claimed:
            return False
        self.claimed.add(event_identity)
        self.lifecycle.append({"kind": "claim", "metadata": metadata or {}, **kwargs})
        return True

    async def record_lifecycle_event(
        self, _idempotency_key: str, *, metadata=None, **kwargs
    ):
        self.lifecycle.append({"kind": "record", "metadata": metadata or {}, **kwargs})
        return self._row


def _build(
    *,
    owner_id: Any = _USER_ID,
    store: _FakeStore | None = None,
    service: Any | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH)
    store = store or _FakeStore()
    config = SimpleNamespace(host_protocol_mode=HOST_PROTOCOL_MODE_PROXY)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: (
        service or _FakeService(owner_id)
    )
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_get_bridge_proxy] = object
    app.dependency_overrides[_get_create_embedded_facade] = lambda: None
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    return TestClient(app)


def _ws_path(suffix: str, *, binding: str = _CHAT_BINDING_ID) -> str:
    return f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{binding}/omnigent/{suffix}"


def _connect_expect_close(
    client: TestClient, path: str, **kwargs: Any
) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path, **kwargs) as ws:
            ws.receive_text()
    return exc.value


# --- Authorization before upgrade --------------------------------------------


def test_global_updates_ws_relays_through_binding_scope() -> None:
    client = _build()
    disconnect = _connect_expect_close(client, _ws_path("v1/sessions/updates"))
    assert disconnect.code == 1000


def test_relay_keeps_server_to_browser_socket_alive_without_browser_input(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._NATIVE_WS_IDLE_SECONDS", 0.01
    )

    class _Browser:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed: list[int] = []

        async def accept(self, **_kwargs):
            return None

        async def receive(self):
            await asyncio.Event().wait()

        async def send_text(self, payload: str):
            self.sent.append(payload)

        async def send_bytes(self, _payload: bytes):
            return None

        async def close(self, *, code: int, **_kwargs):
            self.closed.append(code)

    class _Upstream:
        def __init__(self) -> None:
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def close(self):
            self.closed = True

        def __aiter__(self):
            async def messages():
                await asyncio.sleep(0.03)
                yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="output")

            return messages()

    upstream = _Upstream()

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def ws_connect(self, *_args, **_kwargs):
            return upstream

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession",
        lambda **_kwargs: _Client(),
    )

    async def authorized() -> bool:
        return True

    browser = _Browser()
    asyncio.run(
        _relay_native_websocket(
            browser=browser,
            upstream_url="ws://upstream.invalid/v1/sessions/updates",
            subprotocol=None,
            still_authorized=authorized,
        )
    )

    assert browser.sent == ["output"]
    assert browser.closed[-1] == 1000


def test_websocket_relay_injects_only_server_owned_upstream_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.resolved_api_token",
        lambda: "upstream-only",
    )
    observed: dict[str, Any] = {}

    class _Browser:
        headers = {
            "authorization": "Bearer moonmind-browser",
            "cookie": "moonmind=session",
            "x-csrf-token": "browser-csrf",
        }

        async def accept(self, **_kwargs):
            return None

        async def receive(self):
            return {"type": "websocket.disconnect"}

        async def close(self, **_kwargs):
            return None

    class _Upstream:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def close(self):
            self.closed = True

        def __aiter__(self):
            async def messages():
                await asyncio.Event().wait()
                yield None

            return messages()

    upstream = _Upstream()

    class _Client:
        def __init__(self, *, headers=None, **_kwargs):
            observed["headers"] = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def ws_connect(self, url: str, **kwargs):
            observed["url"] = url
            observed["connect"] = kwargs
            return upstream

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession", _Client
    )

    async def authorized() -> bool:
        return True

    asyncio.run(
        _relay_native_websocket(
            browser=_Browser(),
            upstream_url="wss://stock-omnigent.invalid/v1/sessions/provider-1",
            subprotocol="omnigent.workflow-chat.v1",
            still_authorized=authorized,
        )
    )

    assert observed["headers"] == {"Authorization": "Bearer upstream-only"}
    serialized = json.dumps(observed).lower()
    assert "moonmind-browser" not in serialized
    assert "cookie" not in serialized
    assert "csrf" not in serialized


def test_global_updates_frames_are_identity_scoped_and_virtualized() -> None:
    assert json.loads(
        _filter_session_updates_watch_frame(
            '{"type":"watch","session_ids":["chatb-1"]}',
            chat_binding_id=_CHAT_BINDING_ID,
            provider_session_id=_PROVIDER_SESSION_ID,
        )
    )["session_ids"] == [_PROVIDER_SESSION_ID]
    with pytest.raises(PermissionError):
        _filter_session_updates_watch_frame(
            '{"type":"watch","session_ids":["other"]}',
            chat_binding_id=_CHAT_BINDING_ID,
            provider_session_id=_PROVIDER_SESSION_ID,
        )
    virtualized = _virtualize_session_updates_frame(
        '{"type":"changed","items":[{"id":"prov-sess-1","hostId":"secret"}]}',
        chat_binding_id=_CHAT_BINDING_ID,
        provider_session_id=_PROVIDER_SESSION_ID,
    )
    assert json.loads(virtualized or "null") == {
        "type": "changed",
        "items": [{"id": _CHAT_BINDING_ID}],
    }


def test_dictation_ws_relays_through_binding_scope() -> None:
    client = _build()
    disconnect = _connect_expect_close(client, _ws_path("v1/dictation/stream"))
    assert disconnect.code == WS_CLOSE_CAPABILITY_DENIED

    store = _FakeStore(
        row=_row(
            metadata_={
                **_row().metadata_,
                "callerAuthorities": {
                    str(_USER_ID): {
                        **_row().metadata_["callerAuthorities"][str(_USER_ID)],
                        "mutateWorkspace": True,
                    }
                },
            }
        )
    )
    disconnect = _connect_expect_close(
        _build(store=store), _ws_path("v1/dictation/stream")
    )
    assert disconnect.code == 1000


def test_non_owner_ws_is_non_enumerating() -> None:
    # A caller who does not own the workflow gets the same close as an unknown
    # binding, so bindings cannot be enumerated over WebSocket.
    row = _row(metadata_={**_row().metadata_, "callerAuthorities": {}})
    client = _build(owner_id=uuid4(), store=_FakeStore(row=row))
    disconnect = _connect_expect_close(client, _ws_path("v1/sessions/updates"))
    assert disconnect.code == WS_CLOSE_BINDING_UNKNOWN
    assert disconnect.reason == "omnigent_chat_binding_unknown"


def test_unknown_binding_ws_is_non_enumerating() -> None:
    client = _build(store=_FakeStore(row=None))
    disconnect = _connect_expect_close(client, _ws_path("v1/sessions/updates"))
    assert disconnect.code == WS_CLOSE_BINDING_UNKNOWN


def test_unknown_ws_transport_fails_closed_with_diagnostic() -> None:
    # An unrecognized WebSocket route is never generically proxied.
    client = _build()
    disconnect = _connect_expect_close(client, _ws_path("v1/sessions/chatb-1/mystery"))
    assert disconnect.code == WS_CLOSE_TRANSPORT_UNSUPPORTED
    assert disconnect.reason == "omnigent_chat_transport_unsupported"


# --- Terminal / PTY gating ----------------------------------------------------


def test_terminal_attach_is_denied_by_capability() -> None:
    # Terminal input requires writeTerminal, which the facade never grants, so
    # even the workflow owner cannot write to a PTY (acceptance criteria 4-5).
    client = _build()
    disconnect = _connect_expect_close(
        client, _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach")
    )
    assert disconnect.code == WS_CLOSE_CAPABILITY_DENIED
    assert disconnect.reason == "omnigent_chat_operation_denied"


def test_terminal_attach_relays_when_durable_policy_explicitly_grants_it() -> None:
    store = _FakeStore(
        row=_row(
            metadata_={
                **_row().metadata_,
                "callerAuthorities": {
                    str(_USER_ID): {
                        **_row().metadata_["callerAuthorities"][str(_USER_ID)],
                        "attachTerminal": True,
                        "writeTerminal": True,
                    }
                },
            }
        )
    )
    client = _build(store=store)
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
    )
    assert disconnect.code == 1000


def test_terminal_input_frame_is_scanned_and_durably_receipted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *args, **kwargs: True,
    )
    captured: dict[str, Any] = {}

    async def relay(*, browser: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        await browser.accept()
        await browser.close(code=1000)

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._relay_native_websocket", relay
    )
    grants = dict(_row().metadata_["callerAuthorities"][str(_USER_ID)])
    grants.update({"attachTerminal": True, "writeTerminal": True})
    metadata = {
        **_row().metadata_,
        "callerAuthorities": {str(_USER_ID): grants},
    }
    store = _FakeStore(row=_row(metadata_=metadata))
    client = _build(store=store)

    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach"),
    )
    assert disconnect.code == 1000

    guard = captured["browser_frame_guard"]
    audit = captured["browser_frame_audit"]
    payload, receipt = asyncio.run(guard("echo hello", False))
    asyncio.run(audit(receipt, "posted"))

    assert payload == "echo hello"
    claim = next(entry for entry in store.lifecycle if entry["kind"] == "claim")
    assert claim["metadata"]["controlOutcome"] == "pending"
    assert claim["metadata"]["scanSurface"] == "websocket_frame"
    posted = next(entry for entry in store.lifecycle if entry["kind"] == "record")
    assert posted["metadata"]["controlOutcome"] == "posted"


def test_unknown_terminal_create_websocket_is_not_proxied() -> None:
    client = _build()
    disconnect = _connect_expect_close(
        client, _ws_path("v1/sessions/chatb-1/terminals")
    )
    assert disconnect.code == WS_CLOSE_TRANSPORT_UNSUPPORTED


def test_terminal_view_passes_capability_then_relays() -> None:
    # Terminal viewing is a read capability the owner holds, so it clears the
    # capability gate and reaches the compatibility-review disposition (proving
    # view is gated separately from input).
    client = _build()
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
    )
    assert disconnect.code == 1000


def test_unknown_browser_websocket_is_not_proxied() -> None:
    client = _build()
    disconnect = _connect_expect_close(client, _ws_path("v1/sessions/chatb-1/browser"))
    assert disconnect.code == WS_CLOSE_TRANSPORT_UNSUPPORTED


# --- Read-only viewer / terminal binding --------------------------------------


def test_read_only_binding_from_policy_denies_terminal_view() -> None:
    # A read-only viewer (policy withholds resource reads) cannot open the
    # terminal-view transport.
    store = _FakeStore(
        row=_row(
            metadata_={
                **_row().metadata_,
                "callerAuthorities": {
                    str(_USER_ID): {
                        **_row().metadata_["callerAuthorities"][str(_USER_ID)],
                        "viewTerminal": False,
                    }
                },
            }
        )
    )
    client = _build(store=store)
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
    )
    assert disconnect.code == WS_CLOSE_CAPABILITY_DENIED


def test_terminal_binding_closes_live_transport() -> None:
    # A terminal/revoked binding cannot open a new live transport (criterion 10).
    store = _FakeStore(row=_row(status="completed"))
    client = _build(store=store)
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
    )
    assert disconnect.code == WS_CLOSE_READ_ONLY
    assert disconnect.reason == "omnigent_chat_session_read_only"


def test_starting_binding_without_provider_session_is_not_ready() -> None:
    store = _FakeStore(row=_row(omnigent_session_id=None))
    client = _build(store=store)
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
    )
    assert disconnect.code == WS_CLOSE_SESSION_NOT_READY


# --- Identity substitution / subprotocol --------------------------------------


def test_ws_rejects_session_identity_substitution() -> None:
    # The path names a provider session id that does not equal the bound
    # chatBindingId -> substitution rejected before upgrade.
    client = _build()
    disconnect = _connect_expect_close(
        client,
        _ws_path(f"v1/sessions/{_PROVIDER_SESSION_ID}/resources/terminals/t1/attach"),
    )
    assert disconnect.code == WS_CLOSE_CAPABILITY_DENIED
    assert disconnect.reason == "omnigent_chat_session_substitution"


def test_ws_rejects_unlisted_subprotocol() -> None:
    client = _build()
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
        subprotocols=["evil.raw.protocol"],
    )
    assert disconnect.code == WS_CLOSE_SUBPROTOCOL_REJECTED


def test_ws_rejects_cross_origin_browser_connect() -> None:
    client = _build()
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach"),
        headers={"origin": "https://attacker.example"},
    )
    assert disconnect.code == WS_CLOSE_CAPABILITY_DENIED
    assert disconnect.reason == "omnigent_chat_ws_origin_rejected"


def test_ws_accepts_allowlisted_subprotocol_then_relays() -> None:
    client = _build()
    disconnect = _connect_expect_close(
        client,
        _ws_path("v1/sessions/chatb-1/resources/terminals/t1/attach?read_only=true"),
        subprotocols=["omnigent.workflow-chat.v1"],
    )
    assert disconnect.code == 1000
