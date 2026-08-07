"""Router + primitive tests for the binding-scoped Workflow Chat facade.

MoonLadderStudios/MoonMind#3634: the native Omnigent web application reaches the
provider session only through
``/api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}``. Every request
independently authenticates, resolves the durable binding, authorizes the
caller and operation, rejects identity substitution, strips MoonMind
credentials, and forwards only to the server-resolved provider session.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_create_embedded_facade,
    _get_execution_service,
    _require_bridge_enabled,
    workflow_chat_router,
)
from api_service.api.routers.retrieval_gateway import get_capability_registry
from api_service.auth_providers import get_current_user
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_PROXY
from moonmind.omnigent.settings import resolved_proxy_forward_headers
from moonmind.omnigent.workflow_chat_facade import (
    CAP_SEND_MESSAGE,
    CAP_VIEW_TRANSCRIPT,
    WorkflowChatFacadeError,
    assert_no_identity_substitution,
    match_facade_operation,
    recompute_capabilities,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

_USER_ID = uuid4()
_UNSET = object()

_PROVIDER_SESSION_ID = "prov-sess-1"
_CHAT_BINDING_ID = "brs-1"


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="chat@example.com", is_superuser=False)


class _FakeService:
    def __init__(self, owner_id: Any, *, deny_after: int | None = None) -> None:
        self._owner_id = owner_id
        self._deny_after = deny_after
        self.calls = 0

    async def describe_execution(self, workflow_id: str):
        self.calls += 1
        if self._deny_after is not None and self.calls > self._deny_after:
            # Simulate the workflow authority being revoked mid-stream.
            return SimpleNamespace(owner_id=uuid4())
        return SimpleNamespace(owner_id=self._owner_id)


def _row(**overrides: Any) -> SimpleNamespace:
    values = dict(
        bridge_session_id=_CHAT_BINDING_ID,
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
        metadata_={},
        diagnostics_ref=None,
        capture_manifest_ref=None,
        initial_snapshot_ref=None,
        final_snapshot_ref=None,
        raw_events_ref=None,
        normalized_events_ref=None,
        external_state_ref=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _event(sequence: int) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=f"evt-{sequence}",
        bridge_session_id=_CHAT_BINDING_ID,
        sequence=sequence,
        timestamp=SimpleNamespace(isoformat=lambda: "2026-08-07T00:00:00+00:00"),
        direction="host_to_moonmind",
        event_type="response.delta",
        normalized_status="running",
        text_preview="hello",
        artifact_ref=None,
        metadata_={},
    )


class _FakeStore:
    def __init__(self, *, row: Any = _UNSET, events: list[Any] | None = None) -> None:
        self._row = _row() if row is _UNSET else row
        self._events = events or []
        self.appended: list[dict[str, Any]] = []

    async def get_bridge_session(self, bridge_session_id: str):
        return self._row

    async def get_session_by_provider_session_id(self, session_id: str):
        if self._row and getattr(self._row, "omnigent_session_id", None) == session_id:
            return self._row
        return None

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        rows = [event for event in self._events if event.sequence > after]
        return SimpleNamespace(
            rows=rows[:limit],
            has_more=len(rows) > limit,
            latest_sequence=max((event.sequence for event in self._events), default=0),
            earliest_sequence=min(
                (event.sequence for event in self._events), default=None
            ),
        )

    async def append_events(self, bridge_session_id: str, events: list[dict[str, Any]]):
        self.appended.extend(events)


class _FakeProxy:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.resolved: list[dict[str, Any]] = []
        self.resources: list[tuple[str, str, str | None]] = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "status": "running",
            "providerSessionField": session_id,
            "host_id": "host-1",
            "moonmind": {"workflowId": "mm:w1", "bridgeSessionId": _CHAT_BINDING_ID},
        }

    async def list_agents(self):
        return [{"id": "agent-1", "name": "codex"}]

    async def get_resource(self, operation: str, session_id: str, value=None):
        self.resources.append((operation, session_id, value))
        if operation in {"workspace_file", "workspace_diff", "session_file"}:
            return b"RAW-BYTES"
        return {"files": [{"path": "src/main.py", "session": session_id}]}

    async def post_event(self, *, session_id: str, event, actor=None):
        self.posted.append(
            {"session_id": session_id, "type": event.type, "actor": actor}
        )
        return {"ok": True, "type": event.type, "session_id": session_id}

    async def stop_session(self, session_id: str):
        return {"ok": True, "status": "stopped", "session_id": session_id}

    async def resolve_elicitation(
        self, *, session_id: str, elicitation_id: str, payload, actor=None
    ):
        self.resolved.append(
            {
                "session_id": session_id,
                "elicitation_id": elicitation_id,
                "payload": payload,
            }
        )
        return {"ok": True, "elicitationId": elicitation_id, "session_id": session_id}


def _fake_registry() -> SimpleNamespace:
    return SimpleNamespace(
        has_live_session_authority=Mock(return_value=False),
        revoke_scope=Mock(return_value=[]),
    )


def _build(
    *,
    owner_id: Any = _USER_ID,
    proxy: _FakeProxy | None = None,
    store: _FakeStore | None = None,
    registry: Any | None = None,
    service: Any | None = None,
) -> tuple[TestClient, _FakeProxy, _FakeStore]:
    app = FastAPI()
    app.include_router(
        workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH
    )
    proxy = proxy or _FakeProxy()
    store = store or _FakeStore()
    registry = registry or _fake_registry()
    config = SimpleNamespace(host_protocol_mode=HOST_PROTOCOL_MODE_PROXY)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: (
        service or _FakeService(owner_id)
    )
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[_get_create_embedded_facade] = lambda: None
    app.dependency_overrides[get_capability_registry] = lambda: registry
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    return TestClient(app), proxy, store


def _path(suffix: str, *, binding: str = _CHAT_BINDING_ID) -> str:
    return f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{binding}/omnigent/{suffix}"


# --- Snapshot / bootstrap ----------------------------------------------------


def test_owner_snapshot_virtualizes_provider_identity() -> None:
    client, _proxy, _store = _build()

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 200
    body = response.json()
    # The provider session id is virtualized to the chatBindingId and never
    # leaks; upstream topology and MoonMind binding metadata are stripped.
    assert body["id"] == _CHAT_BINDING_ID
    assert _PROVIDER_SESSION_ID not in response.text
    assert body["providerSessionField"] == _CHAT_BINDING_ID
    assert "moonmind" not in body
    assert "host_id" not in body
    # Capabilities are recomputed from trusted state on every request.
    assert body["capabilities"][CAP_SEND_MESSAGE] is True
    assert body["readOnly"] is False


def test_non_owner_gets_non_enumerating_binding_unknown() -> None:
    client, _proxy, _store = _build(owner_id=uuid4())

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "omnigent_chat_binding_unknown"


def test_unknown_binding_is_non_enumerating() -> None:
    client, _proxy, _store = _build(store=_FakeStore(row=None))

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "omnigent_chat_binding_unknown"


# --- Allowlist ---------------------------------------------------------------


def test_route_not_allowlisted_is_rejected() -> None:
    client, _proxy, _store = _build()

    # Session creation is never exposed through the browser facade.
    response = client.post(_path("v1/sessions"))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "omnigent_chat_route_not_allowlisted"


def test_delete_method_not_allowlisted() -> None:
    client, _proxy, _store = _build()

    response = client.request("DELETE", _path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code in (404, 405)


# --- Identity substitution ---------------------------------------------------


def test_session_substitution_in_path_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.get(_path("v1/sessions/some-other-session"))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_session_substitution"


def test_identity_substitution_in_query_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}"),
        params={"endpoint": "http://evil.internal"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_identity_substitution"


def test_identity_substitution_in_body_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "host_id": "evil-host"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_identity_substitution"


def test_session_id_body_mismatch_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "session_id": "other-session"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_session_substitution"


def test_identity_substitution_header_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}"),
        headers={"X-Omnigent-Endpoint": "http://evil.internal"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_identity_substitution"


# --- Credential separation ---------------------------------------------------


def test_moonmind_credentials_cannot_cross_to_upstream() -> None:
    """The client the facade forwards through drops MoonMind credentials and
    injects only the server-side upstream token (OB-§16 rule 7)."""

    client = OmnigentHttpClient(
        base_url="https://omnigent.internal",
        api_token="upstream-secret",
        forward_headers={
            "authorization": "Bearer moonmind-jwt",
            "cookie": "session=abc",
            "x-csrf-token": "csrf",
            "x-moonmind-user": "user-1",
            "x-moonmind-authorization": "internal",
            "content-type": "application/json",
        },
        upstream_header_allowlist=resolved_proxy_forward_headers(),
    )

    headers = client._headers()

    assert headers["Authorization"] == "Bearer upstream-secret"
    assert "moonmind-jwt" not in headers["Authorization"]
    assert "Cookie" not in headers and "cookie" not in headers
    assert not any(name.lower().startswith("x-moonmind") for name in headers)
    assert not any(name.lower() == "x-csrf-token" for name in headers)


# --- Capabilities / read-only ------------------------------------------------


def test_terminal_session_snapshot_is_read_only() -> None:
    client, _proxy, _store = _build(store=_FakeStore(row=_row(status="completed")))

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 200
    body = response.json()
    assert body["readOnly"] is True
    assert body["capabilities"][CAP_SEND_MESSAGE] is False
    assert body["capabilities"][CAP_VIEW_TRANSCRIPT] is True


def test_terminal_session_rejects_message() -> None:
    client, _proxy, _store = _build(store=_FakeStore(row=_row(status="completed")))

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_read_only"


def test_session_without_provider_session_is_not_ready() -> None:
    client, _proxy, _store = _build(
        store=_FakeStore(row=_row(omnigent_session_id="", status="declared"))
    )

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_not_ready"


# --- Message / control forwarding -------------------------------------------


def test_message_forwarded_to_bound_provider_session() -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 200
    # Forwarded to the server-owned provider session, not the browser-visible id.
    assert proxy.posted == [
        {"session_id": _PROVIDER_SESSION_ID, "type": "message", "actor": None}
    ]
    # Response is virtualized back to the chatBindingId.
    assert response.json()["session_id"] == _CHAT_BINDING_ID
    assert _PROVIDER_SESSION_ID not in response.text


def test_stop_revokes_and_forwards() -> None:
    registry = _fake_registry()
    client, proxy, _store = _build(registry=registry)

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "stop"},
    )

    assert response.status_code == 200
    registry.revoke_scope.assert_called_once()


def test_resolve_elicitation_forwarded_to_bound_session() -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert proxy.resolved == [
        {
            "session_id": _PROVIDER_SESSION_ID,
            "elicitation_id": "el-9",
            "payload": {"decision": "approve"},
        }
    ]


def test_message_requires_json_content_type() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        content="type=message",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "omnigent_chat_unsupported_media_type"


def test_malformed_json_body_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        content="{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "omnigent_chat_malformed_payload"


def test_oversized_body_is_rejected() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        content=b"x" * (1 * 1024 * 1024 + 16),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "omnigent_chat_payload_too_large"


# --- Resources ---------------------------------------------------------------


def test_resource_index_delegated_to_bound_session() -> None:
    client, proxy, _store = _build()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}"
            "/resources/environments/default/changes"
        )
    )

    assert response.status_code == 200
    assert proxy.resources == [("changed_files", _PROVIDER_SESSION_ID, None)]


def test_resource_file_returns_bytes() -> None:
    client, proxy, _store = _build()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}"
            "/resources/environments/default/filesystem/src/main.py"
        )
    )

    assert response.status_code == 200
    assert response.content == b"RAW-BYTES"
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert proxy.resources == [
        ("workspace_file", _PROVIDER_SESSION_ID, "src/main.py")
    ]


def test_workspace_diff_media_type() -> None:
    client, proxy, _store = _build()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}"
            "/resources/environments/default/diff/src/main.py"
        )
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-diff")


def test_list_agents_metadata() -> None:
    client, _proxy, _store = _build()

    response = client.get(_path("v1/agents"))

    assert response.status_code == 200
    assert response.json() == [{"id": "agent-1", "name": "codex"}]


def test_liveness_probe_is_local() -> None:
    client, proxy, _store = _build()

    response = client.get(_path("health"))

    assert response.status_code == 200
    body = response.json()
    assert body["chatBindingId"] == _CHAT_BINDING_ID
    assert body["state"] == "available"
    assert body["readOnly"] is False
    assert proxy.resources == []  # never touched upstream


# --- SSE replay / reconnect / revocation ------------------------------------


def test_stream_replays_from_cursor_and_terminates() -> None:
    store = _FakeStore(row=_row(status="completed"), events=[_event(1), _event(2)])
    client, _proxy, _store = _build(store=store)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"))

    assert response.status_code == 200
    assert "id: 1" in response.text
    assert "id: 2" in response.text
    assert "event: terminal" in response.text


def test_stream_last_event_id_resumes_after_delivered() -> None:
    store = _FakeStore(row=_row(status="completed"), events=[_event(1), _event(2)])
    client, _proxy, _store = _build(store=store)

    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert "id: 2" in response.text
    assert "id: 1\n" not in response.text


def test_stream_reports_retention_gap() -> None:
    store = _FakeStore(row=_row(status="active"), events=[_event(5), _event(6)])
    client, _proxy, _store = _build(store=store)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"))

    assert response.status_code == 200
    assert "event: retention_gap" in response.text


def test_stream_stops_when_authority_revoked_midstream(monkeypatch) -> None:
    import api_service.api.routers.omnigent_bridge as module

    # Force reauthorization on the first poll so the revoked authority is
    # observed immediately rather than after the default cadence.
    monkeypatch.setattr(module, "_FACADE_STREAM_REAUTH_EVERY_POLLS", 1)
    store = _FakeStore(row=_row(status="active"), events=[_event(1)])
    # The binding is authorized when the stream opens, then denied on the next
    # authorization pass (deny_after=1).
    service = _FakeService(_USER_ID, deny_after=1)
    client, _proxy, _store = _build(store=store, service=service)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"))

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "omnigent_chat_binding_unknown" in response.text


# --- Primitive-level coverage ------------------------------------------------


def test_match_facade_operation_allowlist() -> None:
    assert match_facade_operation("GET", "v1/agents").operation.name == "list_agents"
    assert match_facade_operation("GET", "health").operation.name == "liveness"
    match = match_facade_operation("POST", "v1/sessions/abc/events")
    assert match.operation.name == "post_event"
    assert match.params["session_id"] == "abc"
    # Not allowlisted: generic reverse-proxy targets and wrong methods.
    assert match_facade_operation("POST", "v1/sessions") is None
    assert match_facade_operation("GET", "v1/sessions/abc/events") is None
    assert match_facade_operation("DELETE", "v1/sessions/abc") is None
    assert match_facade_operation("GET", "../../etc/passwd") is None


def test_recompute_capabilities_read_only_when_terminal() -> None:
    active = recompute_capabilities("active")
    terminal = recompute_capabilities("completed")

    assert active[CAP_SEND_MESSAGE] is True
    assert terminal[CAP_SEND_MESSAGE] is False
    assert terminal[CAP_VIEW_TRANSCRIPT] is True
    # Authority-boundary capabilities are never granted through the facade.
    assert active["changeModel"] is False
    assert active["mutateWorkspace"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"path_session_id": "other"},
        {"path_session_id": None, "query": {"host_id": "x"}},
        {"path_session_id": None, "body": {"type": "message", "endpoint": "x"}},
        {"path_session_id": None, "body": {"metadata": {"model": "gpt"}}},
        {"path_session_id": None, "headers": {"x-provider-session-id": "x"}},
    ],
)
def test_identity_guard_fails_closed(kwargs) -> None:
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(chat_binding_id=_CHAT_BINDING_ID, **kwargs)


def test_identity_guard_allows_bound_session_echo() -> None:
    # The bound id may legitimately be echoed in the body/query.
    assert_no_identity_substitution(
        chat_binding_id=_CHAT_BINDING_ID,
        path_session_id=_CHAT_BINDING_ID,
        query={"since": "3"},
        body={"type": "message", "session_id": _CHAT_BINDING_ID},
        headers={"content-type": "application/json"},
    )
