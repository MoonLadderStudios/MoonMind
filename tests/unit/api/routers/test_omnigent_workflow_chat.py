"""Router + primitive tests for the binding-scoped Workflow Chat facade.

MoonLadderStudios/MoonMind#3634: the native Omnigent web application reaches the
provider session only through
``/api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}``. Every request
independently authenticates, resolves the durable binding, authorizes the
caller and operation, rejects identity substitution, strips MoonMind
credentials, and forwards only to the server-resolved provider session.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_service.api.routers.omnigent_bridge import (
    CODE_SESSION_NOT_READY,
    WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    WorkflowChatFacadeError,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_create_embedded_facade,
    _get_execution_service,
    _claim_facade_message,
    _require_bridge_enabled,
    _validate_native_resource_path,
    workflow_chat_router,
)
from api_service.api.routers.retrieval_gateway import get_capability_registry
from api_service.auth_providers import get_current_user
from moonmind.omnigent import native_ui_compat
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
)
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
from moonmind.omnigent.control_plane.records import ControlPlaneOutcome
from moonmind.omnigent.control_plane.turn_admission import (
    CanonicalTurnAdmissionRejected,
)
from moonmind.omnigent.resume_decision import (
    SessionResumeDecision,
    SessionResumeOutcome,
)
from moonmind.omnigent.settings import resolved_proxy_forward_headers
from moonmind.omnigent.workflow_chat_facade import (
    CAP_CONTROL_UNSUPPORTED,
    CAP_INTERRUPT_TURN,
    CAP_SEND_MESSAGE,
    CAP_VIEW_TRANSCRIPT,
    WorkflowChatFacadeError,
    assert_no_identity_substitution,
    match_facade_operation,
    recompute_capabilities,
    required_capability_for_event,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

_USER_ID = uuid4()
_UNSET = object()

_PROVIDER_SESSION_ID = "prov-sess-1"
_CHAT_BINDING_ID = "brs-1"
# The durable bridge-session key is server-owned and (once #3633 lands) distinct
# from the browser-facing chatBindingId. Journal reads must use this key.
_BRIDGE_SESSION_ID = "brs-internal-1"


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "%2e%2e/secret",
        "%252e%252e/secret",
        "/etc/passwd",
        "C:\\secret",
        "\\\\host\\share",
    ],
)
def test_native_resource_path_rejects_escape_forms(path: str) -> None:
    with pytest.raises(WorkflowChatFacadeError):
        _validate_native_resource_path(path)


def test_native_resource_path_accepts_scoped_relative_path() -> None:
    _validate_native_resource_path("src/package/file name.py")


def test_websocket_stream_is_explicitly_allowlisted() -> None:
    match = match_facade_operation(
        "WEBSOCKET", f"v1/sessions/{_CHAT_BINDING_ID}/stream"
    )

    assert match is not None
    assert match.operation.name == "stream_events_websocket"
    assert match.params["session_id"] == _CHAT_BINDING_ID
    assert match_facade_operation("WEBSOCKET", "v1/sessions/other/control") is None


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
    grants = {name: True for name in CAPABILITY_NAMES}
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
            "callerAuthorities": {str(_USER_ID): grants},
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            },
        },
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


def _event(sequence: int, *, metadata: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=f"evt-{sequence}",
        bridge_session_id=_BRIDGE_SESSION_ID,
        sequence=sequence,
        timestamp=SimpleNamespace(isoformat=lambda: "2026-08-07T00:00:00+00:00"),
        direction="host_to_moonmind",
        event_type="response.delta",
        normalized_status="running",
        text_preview="hello",
        artifact_ref=None,
        metadata_=metadata if metadata is not None else {},
    )


class _FakeStore:
    def __init__(
        self,
        *,
        row: Any = _UNSET,
        events: list[Any] | None = None,
        rows_by_call: list[Any] | None = None,
    ) -> None:
        self._row = _row() if row is _UNSET else row
        # When provided, successive ``get_bridge_session`` calls return each row
        # in turn (the last is sticky), so a test can simulate the durable state
        # changing between the initial load and the mutation-handoff re-read.
        self._rows_by_call = list(rows_by_call) if rows_by_call else None
        self._get_calls = 0
        self._events = events or []
        self.appended: list[dict[str, Any]] = []
        self.lifecycle: list[dict[str, Any]] = []
        self.claimed: set[str] = set()
        self.event_query_keys: list[str] = []

    def _next_row(self):
        if self._rows_by_call is not None:
            index = min(self._get_calls, len(self._rows_by_call) - 1)
            self._get_calls += 1
            return self._rows_by_call[index]
        return self._row

    async def get_bridge_session(self, bridge_session_id: str):
        return self._next_row()

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        # #3633 dedicated-column resolution seam used by the facade.
        return self._next_row()

    async def get_session_by_provider_session_id(self, session_id: str):
        if self._row and getattr(self._row, "omnigent_session_id", None) == session_id:
            return self._row
        return None

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        self.event_query_keys.append(bridge_session_id)
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

    async def claim_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        event_identity: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if event_identity in self.claimed:
            return False
        self.claimed.add(event_identity)
        self.lifecycle.append(
            {
                "kind": "claim",
                "event_type": event_type,
                "event_identity": event_identity,
                "metadata": metadata or {},
            }
        )
        return True

    async def record_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        event_identity: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ):
        self.lifecycle.append(
            {
                "kind": "record",
                "event_type": event_type,
                "event_identity": event_identity,
                "metadata": metadata or {},
            }
        )
        return self._row

    async def get_lifecycle_event_metadata(
        self,
        idempotency_key: str,
        *,
        event_identity: str,
    ) -> dict[str, Any] | None:
        # Mirror the durable store: return the metadata of the immutable claim
        # already recorded for this event identity, so a replayed idempotency key
        # can be reconciled against the digest it was first bound to.
        for entry in reversed(self.lifecycle):
            if entry["event_identity"] == event_identity:
                return dict(entry["metadata"])
        return None


class _FakeProxy:
    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.posted: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.resolved: list[dict[str, Any]] = []
        self.resources: list[tuple[str, str, str | None]] = []

    async def get_session(self, session_id: str):
        self.sessions.append(session_id)
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
        self.stopped.append(session_id)
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


class _FakeEmbeddedFacade:
    """Embedded-host facade contract exercised by the second supported host mode.

    The changed router has separate embedded branches for messages, cleanup,
    approvals, resources, catalog reads, and actor propagation; this fake lets
    the suite cover the embedded facade boundary rather than only proxy mode.
    """

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
            "moonmind": {"workflowId": "mm:w1", "bridgeSessionId": _BRIDGE_SESSION_ID},
        }

    async def list_agents(self):
        return [{"id": "agent-1", "name": "codex", "host_id": "host-1"}]

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

    async def resolve_elicitation(
        self, *, session_id: str, elicitation_id: str, payload, actor=None
    ):
        self.resolved.append(
            {
                "session_id": session_id,
                "elicitation_id": elicitation_id,
                "payload": payload,
                "actor": actor,
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
    host_protocol_mode: str = HOST_PROTOCOL_MODE_PROXY,
) -> tuple[TestClient, _FakeProxy, _FakeStore]:
    app = FastAPI()
    app.include_router(workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH)
    proxy = proxy or _FakeProxy()
    store = store or _FakeStore()
    registry = registry or _fake_registry()
    config = SimpleNamespace(host_protocol_mode=host_protocol_mode)
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


def _build_embedded(
    *,
    owner_id: Any = _USER_ID,
    embedded: _FakeEmbeddedFacade | None = None,
    store: _FakeStore | None = None,
    registry: Any | None = None,
    service: Any | None = None,
) -> tuple[TestClient, _FakeEmbeddedFacade, _FakeStore]:
    """Build a client whose bridge runs in the embedded host protocol mode."""

    app = FastAPI()
    app.include_router(workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH)
    embedded = embedded or _FakeEmbeddedFacade()
    store = store or _FakeStore()
    registry = registry or _fake_registry()
    config = SimpleNamespace(host_protocol_mode=HOST_PROTOCOL_MODE_EMBEDDED)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: (
        service or _FakeService(owner_id)
    )
    app.dependency_overrides[_get_bridge_store] = lambda: store
    # In embedded mode the proxy is absent; the embedded facade services routes.
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: embedded
    app.dependency_overrides[get_capability_registry] = lambda: registry
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    return TestClient(app), embedded, store


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


def test_native_boot_metadata_is_local_and_safe() -> None:
    client, proxy, _store = _build()

    info = client.get(_path("v1/info"))
    current_user = client.get(_path("v1/me"))
    projects = client.get(_path("v1/sessions/projects"))

    assert info.status_code == 200
    assert info.json()["accounts_enabled"] is False
    assert info.json()["needs_setup"] is False
    assert current_user.status_code == 200
    assert current_user.json() == {
        "user_id": "workflow-owner",
        "is_admin": False,
    }
    assert projects.status_code == 200
    assert projects.json() == []
    # Boot probes never acquire the upstream account or project authority.
    assert proxy.resources == []


def test_session_catalog_contains_only_the_bound_virtual_session() -> None:
    client, _proxy, _store = _build()

    response = client.get(_path("v1/sessions"))

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["has_more"] is False
    assert body["first_id"] == _CHAT_BINDING_ID
    assert body["last_id"] == _CHAT_BINDING_ID
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == _CHAT_BINDING_ID
    assert body["data"][0]["readOnly"] is False
    assert _PROVIDER_SESSION_ID not in response.text


def test_non_owner_gets_non_enumerating_binding_unknown() -> None:
    metadata = {**_row().metadata_, "callerAuthorities": {}}
    client, _proxy, _store = _build(
        owner_id=uuid4(), store=_FakeStore(row=_row(metadata_=metadata))
    )

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


def test_native_task_mutation_uses_effective_change_goal_authority() -> None:
    row = _row()
    metadata = dict(row.metadata_)
    caller_authorities = dict(metadata["callerAuthorities"])
    caller_grants = dict(caller_authorities[str(_USER_ID)])
    caller_grants["changeGoal"] = False
    caller_authorities[str(_USER_ID)] = caller_grants
    metadata["callerAuthorities"] = caller_authorities
    client, _proxy, _store = _build(store=_FakeStore(row=_row(metadata_=metadata)))

    response = client.patch(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1"),
        json={"completed": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"


def test_native_http_mutation_is_scanned_receipted_and_replay_safe(
    monkeypatch,
) -> None:
    _force_high_security(monkeypatch)
    calls: list[dict[str, Any]] = []
    response_body = json.dumps(
        {"ok": True, "session_id": _PROVIDER_SESSION_ID}
    ).encode()

    class _Content:
        async def read(self, _limit: int) -> bytes:
            return response_body

    class _Response:
        status = 307
        headers = {
            "content-type": "application/json",
            "location": f"/v1/sessions/{_PROVIDER_SESSION_ID}/tasks/task-1",
        }
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return _Response()

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession",
        lambda **_kwargs: _Client(),
    )
    client, _proxy, store = _build()
    path = _path(f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1")
    headers = {"Idempotency-Key": "native-task-1"}

    first = client.patch(
        path,
        json={"completed": True},
        headers=headers,
        follow_redirects=False,
    )
    replay = client.patch(
        path,
        json={"completed": True},
        headers=headers,
        follow_redirects=False,
    )

    assert first.status_code == 307
    assert replay.status_code == 307
    expected_location = (
        f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{_CHAT_BINDING_ID}/omnigent/"
        f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1"
    )
    assert first.headers["location"] == expected_location
    assert replay.headers["location"] == expected_location
    assert len(calls) == 1
    claim = next(entry for entry in store.lifecycle if entry["kind"] == "claim")
    assert claim["metadata"]["controlOutcome"] == "pending"
    assert claim["metadata"]["scanSurface"] == "native_mutation"
    posted = next(
        entry
        for entry in store.lifecycle
        if entry["metadata"].get("controlOutcome") == "posted"
    )
    assert posted["metadata"]["normalizedResult"]["statusCode"] == 307


_STOCK_NATIVE_MUTATION_CASES = (
    ("POST", "resources/terminals", {"cols": 80}, "terminal_create"),
    ("DELETE", "resources/terminals/t-1", None, "terminal_close"),
    (
        "POST",
        "resources/environments/default/shell",
        {"command": "pwd"},
        "terminal_shell",
    ),
    (
        "PUT",
        "resources/environments/default/filesystem/src/main.py",
        {"content": "hello"},
        "workspace_edit",
    ),
    (
        "PATCH",
        "resources/environments/default/filesystem/src/main.py",
        {"content": "hello"},
        "workspace_edit",
    ),
    (
        "DELETE",
        "resources/environments/default/filesystem/src/main.py",
        None,
        "workspace_delete",
    ),
    ("POST", "resources/files", {"name": "note.txt"}, "resource_upload"),
    ("POST", "resources/files/file-1/attach", {}, "resource_attach"),
    ("GET", "browser", None, "browser_pane"),
    ("POST", "browser/open", {"url": "about:blank"}, "browser_pane"),
    ("DELETE", "browser/open", None, "browser_pane"),
    ("POST", "subagents/agent-1/interrupt", {}, "subagent_control"),
    ("POST", "tasks/task-1", {"title": "follow up"}, "task_mutate"),
    ("PATCH", "tasks/task-1", {"completed": True}, "task_mutate"),
    ("POST", "reconnect", {}, "session_reconnect"),
)


def test_stock_native_mutation_cases_exactly_cover_the_pinned_inventory() -> None:
    base_operation_names = {
        operation.name for operation in native_ui_compat.FACADE_OPERATIONS
    }
    pinned_inventory = {
        (route.name, method)
        for route in native_ui_compat.NATIVE_UI_ROUTES
        if route.transport == native_ui_compat.TRANSPORT_HTTP
        and route.mutation
        and route.name not in base_operation_names
        for method in route.methods
    }
    exercised = {
        (operation, method)
        for method, _suffix, _payload, operation in _STOCK_NATIVE_MUTATION_CASES
    }

    assert exercised == pinned_inventory


_STOCK_NATIVE_READ_CASES = (
    ("items", "session_items", "relay"),
    ("resources/terminals", "terminal_view", "relay"),
    ("resources/terminals/t-1", "terminal_status", "relay"),
    ("resources/terminals/t-1/logs", "execution_logs", "relay"),
    ("resources/files/file-1/content", "resource_download", "resource"),
    ("subagents", "subagent_tree", "relay"),
    ("tasks", "task_todo", "relay"),
    (None, "host_liveness", "local"),
    (None, "runner_liveness", "local"),
)


def test_stock_native_read_cases_exactly_cover_the_pinned_inventory() -> None:
    base_operation_names = {
        operation.name for operation in native_ui_compat.FACADE_OPERATIONS
    }
    pinned_inventory = {
        (route.name, method)
        for route in native_ui_compat.NATIVE_UI_ROUTES
        if route.transport == native_ui_compat.TRANSPORT_HTTP
        and not route.mutation
        and route.name not in base_operation_names
        for method in route.methods
    }
    exercised = {
        (operation, "GET")
        for _suffix, operation, _owner in _STOCK_NATIVE_READ_CASES
    }

    assert exercised == pinned_inventory


@pytest.mark.parametrize(
    ("suffix", "operation", "owner"),
    _STOCK_NATIVE_READ_CASES,
)
def test_every_additional_stock_native_read_uses_the_bound_identity(
    monkeypatch,
    suffix: str | None,
    operation: str,
    owner: str,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.resolved_api_token",
        lambda: "upstream-only",
    )
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.resolved_server_url",
        lambda: "https://stock-omnigent.invalid",
    )
    upstream_calls: list[dict[str, Any]] = []
    client_headers: list[dict[str, str]] = []
    response_body = json.dumps(
        {"ok": True, "session_id": _PROVIDER_SESSION_ID}
    ).encode()

    class _Content:
        async def read(self, _limit: int) -> bytes:
            return response_body

    class _Response:
        status = 200
        headers = {"content-type": "application/json"}
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Client:
        def __init__(self, *, headers=None, **_kwargs):
            client_headers.append(dict(headers or {}))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, request_method: str, url: str, **kwargs):
            upstream_calls.append({"method": request_method, "url": url, **kwargs})
            return _Response()

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession", _Client
    )
    client, proxy, _store = _build()
    if operation == "host_liveness":
        path = _path("v1/hosts")
    elif operation == "runner_liveness":
        path = _path(f"v1/runners/{_CHAT_BINDING_ID}/status")
    else:
        path = _path(f"v1/sessions/{_CHAT_BINDING_ID}/{suffix}")
    if operation == "session_items":
        path += "?limit=80&order=desc"

    response = client.get(
        path,
        headers={
            "Authorization": "Bearer moonmind-browser",
            "Cookie": "moonmind=session",
            "X-CSRF-Token": "browser-csrf",
        },
    )

    assert response.status_code == 200
    assert _PROVIDER_SESSION_ID not in response.text
    if owner == "relay":
        assert len(upstream_calls) == 1
        assert _PROVIDER_SESSION_ID in upstream_calls[0]["url"]
        assert _CHAT_BINDING_ID not in upstream_calls[0]["url"]
        if operation == "session_items":
            assert upstream_calls[0]["url"].endswith("?limit=80&order=desc")
        assert client_headers == [
            {"Accept": "*/*", "Authorization": "Bearer upstream-only"}
        ]
        serialized_headers = json.dumps(client_headers).lower()
        assert "moonmind-browser" not in serialized_headers
        assert "cookie" not in serialized_headers
        assert "csrf" not in serialized_headers
    elif owner == "resource":
        assert upstream_calls == []
        assert proxy.resources == [
            ("session_file", _PROVIDER_SESSION_ID, "file-1")
        ]
    else:
        assert upstream_calls == []
        assert proxy.resources == []


@pytest.mark.parametrize(
    ("method", "suffix", "payload", "operation"),
    _STOCK_NATIVE_MUTATION_CASES,
)
def test_every_stock_native_http_mutation_has_a_complete_durable_receipt(
    monkeypatch,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
    operation: str,
) -> None:
    """Exercise the complete pinned mutation inventory at the router boundary.

    This is intentionally parametrized from the stock route families rather
    than merely checking compatibility-map metadata: every request must reach
    the real authorization, capability, scan, idempotency, credential, and
    durable-receipt path required by MoonLadderStudios/MoonMind#3632.
    """

    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.resolved_api_token",
        lambda: "upstream-only",
    )
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.resolved_server_url",
        lambda: "https://stock-omnigent.invalid",
    )
    upstream_calls: list[dict[str, Any]] = []
    client_headers: list[dict[str, str]] = []
    response_body = json.dumps(
        {"ok": True, "session_id": _PROVIDER_SESSION_ID, "requestId": "up-1"}
    ).encode()

    class _Content:
        async def read(self, _limit: int) -> bytes:
            return response_body

    class _Response:
        status = 200
        headers = {"content-type": "application/json"}
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Client:
        def __init__(self, *, headers=None, **_kwargs):
            client_headers.append(dict(headers or {}))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, request_method: str, url: str, **kwargs):
            upstream_calls.append({"method": request_method, "url": url, **kwargs})
            return _Response()

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession", _Client
    )
    client, _proxy, store = _build()
    path = _path(f"v1/sessions/{_CHAT_BINDING_ID}/{suffix}")
    request_kwargs: dict[str, Any] = {
        "headers": {
            "Idempotency-Key": f"native-{method.lower()}-{operation}",
            "Authorization": "Bearer moonmind-browser",
            "Cookie": "moonmind=session",
            "X-CSRF-Token": "browser-csrf",
        }
    }
    if payload is not None:
        request_kwargs["json"] = payload

    response = client.request(method, path, **request_kwargs)

    assert response.status_code == 200
    expected_upstream_calls = 0 if operation == "session_reconnect" else 1
    assert len(upstream_calls) == expected_upstream_calls
    replay = client.request(method, path, **request_kwargs)
    assert replay.status_code == response.status_code
    assert replay.content == response.content
    assert len(upstream_calls) == expected_upstream_calls
    if upstream_calls:
        assert _PROVIDER_SESSION_ID in upstream_calls[0]["url"]
        assert _CHAT_BINDING_ID not in upstream_calls[0]["url"]
        assert client_headers == [
            {
                "Accept": "*/*",
                **(
                    {"Content-Type": "application/json"}
                    if payload is not None
                    else {}
                ),
                "Authorization": "Bearer upstream-only",
                "Idempotency-Key": f"native-{method.lower()}-{operation}",
            }
        ]
        serialized_headers = json.dumps(client_headers).lower()
        assert "moonmind-browser" not in serialized_headers
        assert "cookie" not in serialized_headers
        assert "csrf" not in serialized_headers

    claim = next(entry for entry in store.lifecycle if entry["kind"] == "claim")
    posted_records = [
        entry
        for entry in store.lifecycle
        if entry["kind"] == "record"
        and entry["metadata"].get("controlOutcome") == "posted"
    ]
    assert len(posted_records) == 1
    posted = posted_records[0]
    assert claim["metadata"]["controlType"] == operation
    receipt = posted["metadata"]
    assert receipt["receiptSchemaVersion"] == (
        "moonmind.omnigent.mutation-receipt.v1"
    )
    assert receipt["controlType"] == operation
    assert receipt["actor"] == str(_USER_ID)
    assert receipt["workflowId"] == "mm:w1"
    assert receipt["runId"] == "run-1"
    assert receipt["stepExecutionId"] == "step-1"
    assert receipt["bridgeSessionId"] == _BRIDGE_SESSION_ID
    assert receipt["providerSessionId"] == _PROVIDER_SESSION_ID
    assert receipt["controlIdempotencyKey"]
    assert receipt["requestTime"]
    assert receipt["dispatchTime"]
    assert receipt["completionTime"]
    assert receipt["normalizedResult"]["statusCode"] == 200


def test_native_http_mutation_response_failure_records_delivery_unknown(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *args, **kwargs: False,
    )

    class _Content:
        async def read(self, limit: int) -> bytes:
            return b"x" * limit

    class _Response:
        status = 200
        headers = {"content-type": "application/octet-stream"}
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def request(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.aiohttp.ClientSession",
        lambda **_kwargs: _Client(),
    )
    client, _proxy, store = _build()

    response = client.patch(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1"),
        json={"completed": True},
        headers={"Idempotency-Key": "native-oversized-response"},
    )
    replay = client.patch(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1"),
        json={"completed": True},
        headers={"Idempotency-Key": "native-oversized-response"},
    )

    assert response.status_code == 502
    assert replay.status_code == 409
    unknown = next(
        entry
        for entry in store.lifecycle
        if entry["kind"] == "record"
        and entry["metadata"].get("controlOutcome") == "delivery_unknown"
    )
    assert unknown["metadata"]["controlType"] == "task_mutate"
    assert unknown["metadata"]["completionTime"]


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


def test_duplicate_query_identity_values_are_all_validated() -> None:
    client, _proxy, _store = _build()

    response = client.get(
        _path("health") + f"?sessionId=other-session&sessionId={_CHAT_BINDING_ID}"
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_session_substitution"


def test_native_wildcard_path_rejects_encoded_traversal() -> None:
    client, _proxy, _store = _build()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}/subagents/" "%252e%252e/%252e%252e/secret"
        )
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"


def test_embedded_mode_rejects_proxy_only_native_transport() -> None:
    client, _proxy, _store = _build(host_protocol_mode="embedded")

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/tasks"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_bridge_mode_unsupported"


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


def test_stop_control_uses_distinct_authority() -> None:
    # The browser facade carries message/interrupt authority only. Destructive
    # session controls (stop/clear/cleanup/terminal_cleanup) need their own
    # authority the facade does not grant, so a caller with sendMessage cannot
    # reach them by naming an off-allowlist event type.
    registry = _fake_registry()
    client, proxy, _store = _build(registry=registry)

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "stop"},
    )

    assert response.status_code == 200
    registry.revoke_scope.assert_called_once()
    assert proxy.posted == []
    assert proxy.stopped == [_PROVIDER_SESSION_ID]


@pytest.mark.parametrize(
    "event_type",
    ["stop", "stop_session", "clear_session", "cleanup_session", "terminal_cleanup"],
)
def test_destructive_controls_denied_without_distinct_grant(
    event_type: str,
) -> None:
    grants = {name: True for name in CAPABILITY_NAMES}
    grants[required_capability_for_event(event_type)] = False
    row = _row(
        metadata_={
            **_row().metadata_,
            "callerAuthorities": {str(_USER_ID): grants},
        }
    )
    client, proxy, _store = _build(store=_FakeStore(row=row))

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": event_type},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"
    assert proxy.posted == []


def test_resolve_elicitation_persists_scan_evidence_in_mutation_audit(
    monkeypatch,
) -> None:
    # A successful elicitation resolution must persist the bounded native-scan
    # evidence in the durable mutation audit, not discard it, so the exact
    # approval payload's scan is provable after process logs rotate.
    _force_high_security(monkeypatch)
    client, proxy, store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve", "note": "looks good"},
    )

    assert response.status_code == 200
    posted = next(
        entry
        for entry in store.lifecycle
        if entry["metadata"].get("controlOutcome") == "posted"
        and entry["metadata"].get("controlType") == "resolve_elicitation"
    )
    metadata = posted["metadata"]
    assert metadata.get("scanOutcome") == "allow"
    assert metadata.get("payloadDigest")
    assert metadata.get("scanContractVersion")
    assert metadata.get("scannerPolicyRef")
    assert metadata.get("highSecurityMode") is True
    assert metadata["receiptSchemaVersion"] == "moonmind.omnigent.mutation-receipt.v1"
    assert metadata["actor"] == str(_USER_ID)
    assert metadata["workflowId"] == "mm:w1"
    assert metadata["runId"] == "run-1"
    assert metadata["stepExecutionId"] == "step-1"
    assert metadata["agentRunId"] == "ar-1"
    assert metadata["bridgeSessionId"] == _BRIDGE_SESSION_ID
    assert metadata["providerSessionId"] == _PROVIDER_SESSION_ID
    assert metadata["moonmindRequestId"]
    assert metadata["requestTime"]
    assert metadata["dispatchTime"]
    assert metadata["completionTime"]
    assert metadata["durableAuditRef"].startswith("omnigent-bridge-event://")


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


@pytest.mark.parametrize(
    ("suffix", "operation", "value", "binary"),
    [
        ("resources/environments/default/changes", "changed_files", None, False),
        ("resources/environments/default/filesystem", "workspace_files", None, False),
        (
            "resources/environments/default/filesystem/src/main.py",
            "workspace_file",
            "src/main.py",
            True,
        ),
        (
            "resources/environments/default/diff/src/main.py",
            "workspace_diff",
            "src/main.py",
            True,
        ),
        ("resources/files", "session_files", None, False),
        ("resources/files/file-1/content", "session_file", "file-1", True),
    ],
)
def test_every_stock_resource_read_is_bound_and_authorized(
    suffix: str,
    operation: str,
    value: str | None,
    binary: bool,
) -> None:
    client, proxy, _store = _build()

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/{suffix}"))

    assert response.status_code == 200
    assert proxy.resources == [(operation, _PROVIDER_SESSION_ID, value)]
    assert _PROVIDER_SESSION_ID not in response.text
    if binary:
        assert response.content == b"RAW-BYTES"


def test_resource_index_delegated_to_bound_session() -> None:
    client, proxy, _store = _build()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}" "/resources/environments/default/changes"
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
    assert proxy.resources == [("workspace_file", _PROVIDER_SESSION_ID, "src/main.py")]


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


# --- SSE/WebSocket replay, reconnect, and revocation -------------------------


def _websocket_headers(*, origin: str = "http://testserver") -> dict[str, str]:
    return {"Origin": origin}


def test_websocket_stream_delivers_virtualized_events_and_terminal_close() -> None:
    event = _event(
        1,
        metadata={
            "providerSession": _PROVIDER_SESSION_ID,
            "nested": {"session_id": _PROVIDER_SESSION_ID},
        },
    )
    store = _FakeStore(row=_row(status="completed"), events=[event])
    client, _proxy, _store = _build(store=store)

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers=_websocket_headers(),
    ) as websocket:
        delivered = websocket.receive_json()
        terminal = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()

    assert delivered["type"] == "bridge_event"
    assert delivered["sequence"] == 1
    assert delivered["data"]["bridgeSessionId"] == _CHAT_BINDING_ID
    assert delivered["data"]["sessionId"] == _CHAT_BINDING_ID
    assert delivered["data"]["session_id"] == _CHAT_BINDING_ID
    assert _PROVIDER_SESSION_ID not in str(delivered)
    assert terminal["type"] == "terminal"
    assert terminal["data"]["status"] == "completed"
    assert closed.value.code == 1000
    # A second read confirms that no final event committed between the page read
    # and terminal-state observation.
    assert store.event_query_keys == [_BRIDGE_SESSION_ID, _BRIDGE_SESSION_ID]


def test_websocket_reconnect_resumes_after_cursor() -> None:
    store = _FakeStore(row=_row(status="completed"), events=[_event(1), _event(2)])
    client, _proxy, _store = _build(store=store)

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream?cursor=1"),
        headers=_websocket_headers(),
    ) as websocket:
        delivered = websocket.receive_json()
        terminal = websocket.receive_json()

    assert delivered["sequence"] == 2
    assert terminal["type"] == "terminal"


def test_websocket_reports_retention_gap_before_replay() -> None:
    store = _FakeStore(row=_row(status="active"), events=[_event(5), _event(6)])
    client, _proxy, _store = _build(store=store)

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers=_websocket_headers(),
    ) as websocket:
        gap = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()

    assert gap == {
        "type": "retention_gap",
        "data": {"requestedAfter": 0, "earliestAvailable": 5},
    }
    assert closed.value.code == 1000


def test_websocket_confirms_terminal_drain_before_closing() -> None:
    final_event = _event(1)

    class _TerminalRaceStore(_FakeStore):
        def __init__(self) -> None:
            super().__init__(row=_row(status="completed"))
            self.page_calls = 0

        async def list_event_page(
            self, bridge_session_id: str, *, after: int, limit: int
        ):
            self.page_calls += 1
            self.event_query_keys.append(bridge_session_id)
            if self.page_calls == 1:
                return SimpleNamespace(
                    rows=[],
                    has_more=False,
                    latest_sequence=0,
                    earliest_sequence=None,
                )
            if self.page_calls in {2, 3}:
                rows = [final_event] if after < final_event.sequence else []
                return SimpleNamespace(
                    rows=rows,
                    has_more=False,
                    latest_sequence=1,
                    earliest_sequence=1,
                )
            return SimpleNamespace(
                rows=[],
                has_more=False,
                latest_sequence=1,
                earliest_sequence=1,
            )

    store = _TerminalRaceStore()
    client, _proxy, _store = _build(store=store)

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers=_websocket_headers(),
    ) as websocket:
        delivered = websocket.receive_json()
        terminal = websocket.receive_json()

    assert delivered["type"] == "bridge_event"
    assert delivered["sequence"] == 1
    assert terminal["type"] == "terminal"
    assert store.page_calls == 4


def test_websocket_closes_when_transcript_capability_is_revoked(monkeypatch) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._FACADE_STREAM_REAUTH_EVERY_POLLS",
        1,
    )
    revoked = _row()
    metadata = dict(revoked.metadata_)
    authorities = dict(metadata["callerAuthorities"])
    grants = dict(authorities[str(_USER_ID)])
    grants["viewTranscript"] = False
    authorities[str(_USER_ID)] = grants
    metadata["callerAuthorities"] = authorities
    revoked = _row(metadata_=metadata)
    store = _FakeStore(rows_by_call=[_row(), revoked])
    client, _proxy, _store = _build(store=store)

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers=_websocket_headers(),
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()

    assert closed.value.code == 4403
    assert closed.value.reason == "omnigent_chat_operation_denied"


@pytest.mark.parametrize("origin", ["", "https://foreign.example"])
def test_websocket_rejects_missing_or_foreign_origin(origin: str) -> None:
    client, _proxy, _store = _build()
    headers = {} if not origin else _websocket_headers(origin=origin)

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"), headers=headers
        ):
            pass

    assert closed.value.code == 4403
    assert closed.value.reason == "omnigent_chat_origin_denied"


@pytest.mark.parametrize(
    ("owner_id", "suffix"),
    [
        (uuid4(), f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        (_USER_ID, "v1/sessions/other-binding/stream"),
        (
            _USER_ID,
            f"v1/sessions/{_CHAT_BINDING_ID}/stream?endpoint=https://upstream.invalid",
        ),
    ],
)
def test_websocket_rejects_unauthorized_or_substituted_identity(
    owner_id: Any, suffix: str
) -> None:
    store = None
    if owner_id != _USER_ID:
        metadata = {**_row().metadata_, "callerAuthorities": {}}
        store = _FakeStore(row=_row(metadata_=metadata))
    client, _proxy, _store = _build(owner_id=owner_id, store=store)

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_path(suffix), headers=_websocket_headers()):
            pass

    assert closed.value.code == 4404


def test_websocket_closes_when_authority_is_revoked_midstream(monkeypatch) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._FACADE_STREAM_REAUTH_EVERY_POLLS",
        1,
    )
    service = _FakeService(_USER_ID, deny_after=1)
    metadata = {**_row().metadata_, "callerAuthorities": {}}
    client, _proxy, _store = _build(
        service=service, store=_FakeStore(row=_row(metadata_=metadata))
    )

    with client.websocket_connect(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"),
        headers=_websocket_headers(),
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()

    assert closed.value.code == 4403
    assert closed.value.reason == "omnigent_chat_binding_unknown"
    assert service.calls == 2


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
    # Force reauthorization on the first poll so the revoked authority is
    # observed immediately rather than after the default cadence. The setattr
    # target is addressed by dotted path so the module is not imported twice.
    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge._FACADE_STREAM_REAUTH_EVERY_POLLS",
        1,
    )
    metadata = {**_row().metadata_, "callerAuthorities": {}}
    store = _FakeStore(
        row=_row(status="active", metadata_=metadata), events=[_event(1)]
    )
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


# --- Capability policy intersection (interventionCapabilities) ---------------


def test_recompute_capabilities_intersects_binding_policy() -> None:
    # A stored policy that disables sendMessage must remove it even on a live,
    # non-terminal session; it can never re-grant a status-denied capability.
    caps = recompute_capabilities("active", policy_capabilities={"sendMessage": False})
    assert caps[CAP_SEND_MESSAGE] is False
    assert caps[CAP_INTERRUPT_TURN] is True
    assert caps[CAP_VIEW_TRANSCRIPT] is True

    # A policy cannot grant a mutation on a terminal (read-only) session.
    terminal = recompute_capabilities(
        "completed", policy_capabilities={"sendMessage": True}
    )
    assert terminal[CAP_SEND_MESSAGE] is False


def test_message_denied_when_policy_disables_send() -> None:
    store = _FakeStore(
        row=_row(metadata_={"interventionCapabilities": {"sendMessage": False}})
    )
    client, proxy, _store = _build(store=store)

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"
    assert proxy.posted == []


# --- Composer event allowlist ------------------------------------------------


def test_required_capability_for_event_allowlist() -> None:
    assert required_capability_for_event("message") == CAP_SEND_MESSAGE
    assert required_capability_for_event("user.message") == CAP_SEND_MESSAGE
    assert required_capability_for_event("interrupt") == CAP_INTERRUPT_TURN
    assert required_capability_for_event("stop_session") == "stopSession"
    assert required_capability_for_event("clear_session") == "replaceSession"
    assert required_capability_for_event("harvest_session") == "harvestEvidence"
    assert required_capability_for_event("cleanup_session") == "cleanupSession"
    assert required_capability_for_event("weird") == CAP_CONTROL_UNSUPPORTED


def test_interrupt_is_forwarded() -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "interrupt"},
    )

    assert response.status_code == 200
    assert proxy.posted == [
        {"session_id": _PROVIDER_SESSION_ID, "type": "interrupt", "actor": None}
    ]


# --- Recursive identity guard ------------------------------------------------


def test_identity_guard_scans_nested_body_and_lists() -> None:
    # A forbidden identity hidden inside nested event data or a list item is
    # rejected, not only the body root and one metadata mapping.
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id=None,
            body={"type": "message", "data": {"endpoint": "http://evil"}},
        )
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id=None,
            body={"type": "message", "items": [{"provider_session_id": "other"}]},
        )
    with pytest.raises(WorkflowChatFacadeError):
        assert_no_identity_substitution(
            chat_binding_id=_CHAT_BINDING_ID,
            path_session_id=None,
            body={"data": {"session_id": "other-session"}},
        )


def test_nested_identity_substitution_rejected_via_router() -> None:
    client, _proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"provider_session_id": "other"}},
    )

    assert response.status_code == 403
    # ``provider_session_id`` is a forbidden server-owned identity key, hidden a
    # level deep inside event ``data`` — the recursive scan still rejects it.
    assert response.json()["detail"]["code"] == "omnigent_chat_identity_substitution"


# --- Recursive topology redaction --------------------------------------------


def test_list_response_topology_redacted_recursively() -> None:
    class _TopoProxy(_FakeProxy):
        async def list_agents(self):
            return [
                {
                    "id": "agent-1",
                    "endpoint": "http://internal",
                    "host_id": "host-1",
                    "runner_id": "runner-1",
                    "nested": {"moonmind": {"secret": True}, "keep": "ok"},
                }
            ]

    client, _proxy, _store = _build(proxy=_TopoProxy())

    response = client.get(_path("v1/agents"))

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == "agent-1"
    # Topology keys are dropped at every mapping depth, not only the root.
    assert "endpoint" not in body[0]
    assert "host_id" not in body[0]
    assert "runner_id" not in body[0]
    assert body[0]["nested"] == {"keep": "ok"}
    assert "internal" not in response.text


# --- Outbound secret scan (fail-closed high-security mode) -------------------


def _force_high_security(monkeypatch) -> None:
    # The native-scan contract resolves the effective mode through the function
    # bound in its own namespace, so patch it there to force high-security mode
    # for the whole outbound-scan path under test.
    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *a, **k: True,
    )


def test_high_security_blocks_secret_bearing_message(monkeypatch) -> None:
    _force_high_security(monkeypatch)
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "data": {"content": [{"type": "text", "text": "ghp_" + "a" * 36}]},
        },
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "omnigent_chat_content_blocked"
    # Only the redacted finding category + safe location are surfaced, never the
    # detected value or the message body.
    assert detail["findings"] == [
        {"category": "token", "location": "body.data.content[0].text"}
    ]
    assert "ghp_" not in response.text
    # The blocked content never reached the provider.
    assert proxy.posted == []


def test_high_security_allows_clean_message_and_forwards(monkeypatch) -> None:
    _force_high_security(monkeypatch)
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 200
    # A clean message is forwarded unchanged after an allow result.
    assert len(proxy.posted) == 1


def test_high_security_disabled_forwards_original_unchanged(monkeypatch) -> None:
    # Default (disabled) mode: the scan returns allow and the payload forwards
    # unchanged even when it would look secret-like under high-security mode.
    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *a, **k: False,
    )
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "data": {"content": [{"type": "text", "text": "ghp_" + "a" * 36}]},
        },
    )

    assert response.status_code == 200
    assert len(proxy.posted) == 1


def test_high_security_fails_closed_on_uninspectable_binary_part(monkeypatch) -> None:
    _force_high_security(monkeypatch)
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "data": {"content": [{"type": "input_image", "image_url": "blob://x"}]},
        },
    )

    # A binary/opaque part is outside the text-scan contract: enforcement is
    # unavailable, distinct from a content block, and nothing is forwarded.
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "omnigent_chat_enforcement_unavailable"
    assert proxy.posted == []


def test_high_security_scanner_error_fails_closed(monkeypatch) -> None:
    _force_high_security(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("scanner down")

    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.scan_outbound_bundle", _boom
    )
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    # An unavailable/erroring scanner fails closed rather than forwarding.
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "omnigent_chat_enforcement_unavailable"
    assert proxy.posted == []


def test_high_security_scans_elicitation_response(monkeypatch) -> None:
    _force_high_security(monkeypatch)
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve", "note": "token=" + "z" * 24},
    )

    # Approval-response text is a scanned outbound surface and blocks on a secret.
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_content_blocked"
    assert proxy.resolved == []


def test_message_claim_records_scanned_payload_digest(monkeypatch) -> None:
    _force_high_security(monkeypatch)
    client, proxy, store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 200
    # The idempotency claim durably names the exact payload that passed the scan,
    # so an allow result cannot be reused after the content changes.
    claim = next(entry for entry in store.lifecycle if entry["kind"] == "claim")
    assert claim["metadata"].get("scannedPayloadDigest")
    assert claim["metadata"]["receiptSchemaVersion"] == (
        "moonmind.omnigent.mutation-receipt.v1"
    )
    assert claim["metadata"]["controlOutcome"] == "pending"
    assert claim["metadata"]["requestTime"]
    assert not any(
        entry["kind"] == "record"
        and entry["metadata"].get("controlOutcome") == "pending"
        for entry in store.lifecycle
    )


@pytest.mark.asyncio
async def test_canonical_settlement_suppresses_facade_ledger_redelivery() -> None:
    store = _FakeStore()

    async def claim_canonical_turn_command(**_kwargs: Any) -> Any:
        return SimpleNamespace(outcome=ControlPlaneOutcome.ALREADY_APPLIED)

    store.claim_canonical_turn_command = claim_canonical_turn_command  # type: ignore[attr-defined]

    claimed = await _claim_facade_message(
        store=store,  # type: ignore[arg-type]
        row=_row(),
        event_type="message",
        actor="operator",
        idempotency_key="canonical-replay-1",
        payload_digest="sha256:" + "a" * 64,
    )

    assert claimed is False
    assert store.lifecycle == []


@pytest.mark.asyncio
async def test_canonical_admission_rejection_is_a_session_not_ready_conflict() -> None:
    """A refused admission is actionable, not a Workflow Chat server error.

    The canonical claim raises ``CanonicalTurnAdmissionRejected`` when the
    session cannot accept the turn -- notably once cleanup has completed. Only
    ``CanonicalTurnAuthorityUnavailable`` was translated, so chat messages,
    steering frames, and facade approvals surfaced a 500 instead of the typed
    409 the provider-session endpoint already returns.
    """

    store = _FakeStore()

    async def claim_canonical_turn_command(**_kwargs: Any) -> Any:
        raise CanonicalTurnAdmissionRejected(
            SessionResumeOutcome(
                decision=SessionResumeDecision.COLD_RESTORE,
                reason_codes=("cleanup_complete",),
            )
        )

    store.claim_canonical_turn_command = claim_canonical_turn_command  # type: ignore[attr-defined]

    with pytest.raises(WorkflowChatFacadeError) as excinfo:
        await _claim_facade_message(
            store=store,  # type: ignore[arg-type]
            row=_row(),
            event_type="message",
            actor="operator",
            idempotency_key="cleanup-complete-1",
            payload_digest="sha256:" + "a" * 64,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == CODE_SESSION_NOT_READY
    assert "cold_restore" in str(excinfo.value)
    assert store.lifecycle == []


# --- Approval authority ------------------------------------------------------


def test_resolve_elicitation_denied_when_policy_disables_approval() -> None:
    store = _FakeStore(
        row=_row(metadata_={"interventionCapabilities": {"resolveElicitation": False}})
    )
    client, proxy, _store = _build(store=store)

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "omnigent_chat_operation_denied"
    assert proxy.resolved == []


# --- Idempotent message submission -------------------------------------------


def test_message_idempotency_key_dedupes_replay() -> None:
    client, proxy, store = _build()
    headers = {"Idempotency-Key": "browser-retry-1"}
    payload = {"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}}

    first = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"), json=payload, headers=headers
    )
    second = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"), json=payload, headers=headers
    )

    assert first.status_code == 200
    assert second.status_code == 200
    # Exactly one provider turn was issued despite the retry.
    assert len(proxy.posted) == 1
    # A duplicate receives the canonical persisted prior result, not a newly
    # synthesized acknowledgement with different semantics.
    assert second.json() == first.json()


def test_message_idempotency_key_reuse_with_different_payload_conflicts() -> None:
    # Reusing an accepted key for a *different* message must not be reported as a
    # benign deduplication (which would silently drop the changed message); it
    # fails closed so the changed content is never forwarded unacknowledged.
    client, proxy, store = _build()
    headers = {"Idempotency-Key": "browser-retry-1"}
    first = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
        headers=headers,
    )
    second = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "data": {"content": [{"type": "text", "text": "changed"}]},
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "omnigent_chat_idempotency_conflict"
    # The conflicting second message was never forwarded to the provider.
    assert len(proxy.posted) == 1


def test_message_records_durable_control_audit() -> None:
    client, proxy, store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 200
    # A durable claim (idempotency) and a durable "posted" control audit exist.
    kinds = {entry["kind"] for entry in store.lifecycle}
    assert "claim" in kinds
    assert any(
        entry["metadata"].get("controlOutcome") == "posted" for entry in store.lifecycle
    )


def test_message_receipt_retains_caller_compare_and_set_values() -> None:
    client, _proxy, store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "expectedSessionEpoch": 2,
            "expectedTerminalState": "active",
            "expectedAgentProfileDigest": "sha256:agent",
            "expectedProviderProfileGeneration": 4,
            "expectedLaunchSnapshotRef": "artifact://launch",
            "expectedPolicyDigest": "sha256:policy",
            "data": {"content": [{"type": "text", "text": "hi"}]},
        },
    )

    assert response.status_code == 200
    receipts = [
        entry["metadata"]
        for entry in store.lifecycle
        if entry["metadata"].get("receiptSchemaVersion")
    ]
    assert receipts
    assert all(item["expectedSessionEpoch"] == 2 for item in receipts)
    assert all(item["expectedTerminalState"] == "active" for item in receipts)
    assert all(
        item["expectedAgentProfileDigest"] == "sha256:agent" for item in receipts
    )
    assert all(item["expectedProviderProfileGeneration"] == 4 for item in receipts)
    assert all(
        item["expectedLaunchSnapshotRef"] == "artifact://launch" for item in receipts
    )
    assert all(item["expectedPolicyDigest"] == "sha256:policy" for item in receipts)


# --- Mutation-handoff revalidation (compare-and-set) -------------------------


def test_message_rejected_when_session_terminalizes_midrequest() -> None:
    # The row is active when first loaded, then terminal on the handoff re-read.
    store = _FakeStore(rows_by_call=[_row(status="active"), _row(status="completed")])
    client, proxy, _store = _build(store=store)

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_read_only"
    assert proxy.posted == []


def test_message_rejects_caller_supplied_stale_session_epoch() -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            "expectedSessionEpoch": 1,
            "data": {"content": [{"type": "text", "text": "hi"}]},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_not_ready"
    assert proxy.posted == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expectedAgentProfileDigest", "sha256:stale"),
        ("expectedProviderProfileGeneration", 3),
        ("expectedLaunchSnapshotRef", "artifact://stale-launch"),
        ("expectedPolicyDigest", "sha256:stale-policy"),
    ],
)
def test_message_rejects_stale_immutable_authority(field: str, value: Any) -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={
            "type": "message",
            field: value,
            "data": {"content": [{"type": "text", "text": "hi"}]},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_not_ready"
    assert proxy.posted == []


def test_elicitation_rejects_caller_supplied_wrong_elicitation() -> None:
    client, proxy, _store = _build()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve", "expectedElicitation": "el-other"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "omnigent_chat_session_not_ready"
    assert proxy.resolved == []


# --- Terminal durable read after provider cleanup ----------------------------


def test_terminal_binding_serves_durable_snapshot_without_provider_session() -> None:
    store = _FakeStore(
        row=_row(
            status="completed",
            omnigent_session_id="",
            terminal_refs={"summary": "done"},
            diagnostics_ref="art://diag",
        )
    )
    client, proxy, _store = _build(store=store)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _CHAT_BINDING_ID
    assert body["readOnly"] is True
    assert body["providerSessionAvailable"] is False
    # Served from the durable projection; no upstream get_session call was made.
    assert proxy.sessions == []


def test_terminal_catalog_contains_durable_session_without_provider_session() -> None:
    store = _FakeStore(
        row=_row(
            status="completed",
            omnigent_session_id="",
            terminal_refs={"summary": "done"},
            diagnostics_ref="art://diag",
        )
    )
    client, proxy, _store = _build(store=store)

    response = client.get(_path("v1/sessions"))

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == [_CHAT_BINDING_ID]
    assert body["data"][0]["readOnly"] is True
    assert body["has_more"] is False
    assert proxy.sessions == []


def test_terminal_items_page_uses_captured_snapshot_after_provider_cleanup(
    monkeypatch,
) -> None:
    metadata = dict(_row().metadata_)
    authority = dict(metadata["capabilityAuthority"])
    authority["providerSessionId"] = _PROVIDER_SESSION_ID
    metadata["capabilityAuthority"] = authority
    store = _FakeStore(
        row=_row(
            status="completed",
            omnigent_session_id="",
            final_snapshot_ref="artifact://captured-final",
            metadata_=metadata,
        )
    )

    class _Gateway:
        async def read_text(self, artifact_ref: str) -> str:
            assert artifact_ref == "artifact://captured-final"
            return json.dumps(
                {
                    "items": [
                        {"id": "item-1", "session_id": _PROVIDER_SESSION_ID},
                        {"id": "item-2", "session_id": _PROVIDER_SESSION_ID},
                    ]
                }
            )

    monkeypatch.setattr(
        "api_service.api.routers.omnigent_bridge.LocalOmnigentArtifactGateway",
        _Gateway,
    )
    client, proxy, _store = _build(store=store)

    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/items"),
        params={"limit": "1", "order": "desc"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is True
    assert body["first_id"] == "item-2"
    assert body["last_id"] == "item-2"
    assert body["data"] == [
        {"id": "item-2", "session_id": _CHAT_BINDING_ID}
    ]
    assert _PROVIDER_SESSION_ID not in response.text
    assert proxy.sessions == []


# --- Strict stream cursors ---------------------------------------------------


@pytest.mark.parametrize("param", ["cursor", "since"])
def test_stream_rejects_malformed_cursor(param: str) -> None:
    store = _FakeStore(row=_row(status="active"), events=[_event(1)])
    client, _proxy, _store = _build(store=store)

    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"), params={param: "-3"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "omnigent_chat_malformed_payload"

    non_numeric = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"), params={param: "abc"}
    )
    assert non_numeric.status_code == 400


# --- SSE journal key + visibility filter -------------------------------------


def test_stream_uses_resolved_bridge_session_key() -> None:
    store = _FakeStore(
        row=_row(status="completed", bridge_session_id=_BRIDGE_SESSION_ID),
        events=[_event(1)],
    )
    client, _proxy, _store = _build(store=store)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"))

    assert response.status_code == 200
    # Journal reads used the durable bridge-session key, not the chatBindingId.
    assert _store.event_query_keys
    assert all(key == _BRIDGE_SESSION_ID for key in _store.event_query_keys)
    # The browser transcript exposes only the opaque chatBindingId.
    assert _BRIDGE_SESSION_ID not in response.text
    assert _CHAT_BINDING_ID in response.text


def test_facade_resolves_via_chat_binding_column() -> None:
    # #3633: the browser id is the dedicated chat_binding_id column, distinct
    # from bridge_session_id. Resolving it as a bridge_session_id must not work.
    class _ColumnOnlyStore(_FakeStore):
        async def get_bridge_session(self, bridge_session_id: str):
            return None

    client, _proxy, _store = _build(store=_ColumnOnlyStore())

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 200
    assert response.json()["id"] == _CHAT_BINDING_ID


def test_stream_excludes_non_visible_lifecycle_rows() -> None:
    visible = _event(1)
    hidden = _event(
        2, metadata={"moonmind": {"workflowChatVisible": False, "source": "lifecycle"}}
    )
    store = _FakeStore(row=_row(status="completed"), events=[visible, hidden])
    client, _proxy, _store = _build(store=store)

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/stream"))

    assert response.status_code == 200
    # The visible row is streamed; the internal lifecycle row is not.
    assert "id: 1" in response.text
    assert "id: 2" not in response.text


# --- Embedded host protocol mode boundary ------------------------------------


def test_embedded_message_forwarded_with_actor() -> None:
    client, embedded, _store = _build_embedded()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/events"),
        json={"type": "message", "data": {"content": [{"type": "text", "text": "hi"}]}},
    )

    assert response.status_code == 200
    assert len(embedded.posted) == 1
    # The embedded branch propagates the caller as actor (proxy mode does not).
    assert embedded.posted[0]["actor"] == str(_USER_ID)
    assert embedded.posted[0]["session_id"] == _PROVIDER_SESSION_ID


def test_embedded_snapshot_virtualizes_and_gates_capabilities() -> None:
    client, embedded, _store = _build_embedded()

    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}"))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _CHAT_BINDING_ID
    assert "host_id" not in body
    assert "moonmind" not in body
    assert body["capabilities"][CAP_SEND_MESSAGE] is True


def test_embedded_resolve_elicitation_propagates_actor() -> None:
    client, embedded, _store = _build_embedded()

    response = client.post(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/elicitations/el-9/resolve"),
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert embedded.resolved == [
        {
            "session_id": _PROVIDER_SESSION_ID,
            "elicitation_id": "el-9",
            "payload": {"decision": "approve"},
            "actor": str(_USER_ID),
        }
    ]


def test_embedded_resource_read_delegated() -> None:
    client, embedded, _store = _build_embedded()

    response = client.get(
        _path(
            f"v1/sessions/{_CHAT_BINDING_ID}" "/resources/environments/default/changes"
        )
    )

    assert response.status_code == 200
    assert embedded.resources == [("changed_files", _PROVIDER_SESSION_ID, None)]


def test_embedded_catalog_read() -> None:
    client, embedded, _store = _build_embedded()

    response = client.get(_path("v1/agents"))

    assert response.status_code == 200
    body = response.json()
    # Topology stripped even from the embedded catalog list response.
    assert body == [{"id": "agent-1", "name": "codex"}]
