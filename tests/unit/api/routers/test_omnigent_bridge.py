"""Router tests for the Omnigent Bridge Session API Facade.

MM-1155 (source: MM-1140): the public session routes are exposed at the
configured mount path, POST /v1/sessions validates the MoonMind principal +
workflow ownership, and bridge failure classes map onto HTTP status codes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    OMNIGENT_BRIDGE_MOUNT_PATH,
    _get_bridge_proxy,
    _get_execution_service,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError

_USER_ID = uuid4()

_CREATE_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions"
_AGENTS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/api/agents"
_EVENTS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/events"
_ELICITATION_RESOLVE_PATH = (
    f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/elicitations/el-1/resolve"
)

# Sentinel so ``_FakeProxy(session_owner=None)`` can distinguish "no owner
# bound" from "use the default mm:w1 owner".
_UNSET = object()


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="bridge@example.com", is_superuser=False)


class _FakeService:
    def __init__(self, owner_id: Any) -> None:
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


class _FakeProxy:
    def __init__(self, *, session_owner: Any | None = _UNSET) -> None:
        self.created: list[dict[str, Any]] = []
        self.posted_events: list[dict[str, Any]] = []
        self.resolved_elicitations: list[dict[str, Any]] = []
        self.create_error: OmnigentBridgeError | None = None
        # By default a read resolves to the mm:w1 owner used across the tests;
        # pass ``session_owner=None`` to simulate a session the bridge does not
        # own, or an explicit binding to simulate a foreign owner.
        self._session_owner = (
            SimpleNamespace(workflow_id="mm:w1", agent_run_id="ar-1")
            if session_owner is _UNSET
            else session_owner
        )

    async def create_session(self, *, request, binding):
        if self.create_error is not None:
            raise self.create_error
        self.created.append({"binding": binding, "request": request})
        return {"id": "sess-1", "status": "running", "moonmind": {"reused": False}}

    async def get_session_owner(self, session_id: str):
        return self._session_owner

    async def get_session(self, session_id: str):
        return {"id": session_id, "status": "completed"}

    async def post_event(self, *, session_id: str, event):
        self.posted_events.append({"session_id": session_id, "event": event})
        return {"ok": True, "type": event.type}

    async def resolve_elicitation(
        self,
        *,
        session_id: str,
        elicitation_id: str,
        payload,
    ):
        self.resolved_elicitations.append(
            {
                "session_id": session_id,
                "elicitation_id": elicitation_id,
                "payload": payload,
            }
        )
        return {"ok": True, "elicitationId": elicitation_id}

    async def list_agents(self):
        return [{"id": "agent-1", "name": "codex"}]


def _build(
    *, owner_id: Any = _USER_ID, proxy: _FakeProxy | None = None
) -> tuple[TestClient, _FakeProxy]:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    proxy = proxy or _FakeProxy()
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(owner_id)
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    return TestClient(app), proxy


def _create_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "agent_id": "agent-1",
        "host_type": "managed",
        "workspace": "https://github.com/org/repo#main",
        "labels": {
            "moonmind.workflow_id": "mm:w1",
            "moonmind.idempotency_key": "idem-1",
            "moonmind.correlation_id": "corr-1",
        },
    }
    body.update(overrides)
    return body


def test_create_session_success_at_mount_path() -> None:
    client, proxy = _build()
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 200
    assert resp.json()["id"] == "sess-1"
    assert len(proxy.created) == 1
    binding = proxy.created[0]["binding"]
    assert binding.workflow_id == "mm:w1"
    assert binding.idempotency_key == "idem-1"
    assert binding.correlation_id == "corr-1"


def test_create_session_requires_idempotency_label() -> None:
    client, _ = _build()
    body = _create_body(labels={"moonmind.workflow_id": "mm:w1"})
    resp = client.post(_CREATE_PATH, json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "user_error"


def test_create_session_requires_workflow_label() -> None:
    client, _ = _build()
    body = _create_body(labels={"moonmind.idempotency_key": "idem-1"})
    resp = client.post(_CREATE_PATH, json=body)
    assert resp.status_code == 400


def test_create_session_denies_non_owner() -> None:
    client, proxy = _build(owner_id=uuid4())  # different owner
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "workflow_ownership_denied"
    assert not proxy.created


def test_create_session_maps_user_error_to_400() -> None:
    proxy = _FakeProxy()
    proxy.create_error = OmnigentBridgeError("bad host", failure_class="user_error")
    client, _ = _build(proxy=proxy)
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 400
    assert resp.json()["detail"]["message"] == "bad host"


def test_create_session_maps_integration_error_to_502() -> None:
    proxy = _FakeProxy()
    proxy.create_error = OmnigentBridgeError(
        "upstream down", failure_class="integration_error"
    )
    client, _ = _build(proxy=proxy)
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 502


def test_get_session_returns_snapshot_for_owner() -> None:
    client, _ = _build()  # user owns mm:w1 (the session owner)
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77")
    assert resp.status_code == 200
    assert resp.json() == {"id": "sess-77", "status": "completed"}


def test_get_session_unknown_session_is_not_found() -> None:
    # A provider session id the bridge does not own is never proxied upstream.
    proxy = _FakeProxy(session_owner=None)
    client, _ = _build(proxy=proxy)
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-unknown")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "omnigent_bridge_session_unknown"


def test_get_session_denies_non_owner() -> None:
    # The session is owned by mm:w1, but the caller does not own that workflow.
    client, _ = _build(owner_id=uuid4())
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "workflow_ownership_denied"


def test_list_agents_returns_catalog() -> None:
    client, _ = _build()
    resp = client.get(_AGENTS_PATH)
    assert resp.status_code == 200
    assert resp.json() == [{"id": "agent-1", "name": "codex"}]


def test_post_event_authorizes_and_delegates() -> None:
    client, proxy = _build()
    resp = client.post(_EVENTS_PATH, json={"type": "interrupt"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "type": "interrupt"}
    assert proxy.posted_events[0]["session_id"] == "sess-77"
    assert proxy.posted_events[0]["event"].type == "interrupt"


def test_post_event_unknown_session_is_not_found() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _ = _build(proxy=proxy)
    resp = client.post(_EVENTS_PATH, json={"type": "interrupt"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "omnigent_bridge_session_unknown"
    assert not proxy.posted_events


def test_resolve_elicitation_authorizes_and_delegates() -> None:
    client, proxy = _build()
    resp = client.post(_ELICITATION_RESOLVE_PATH, json={"answer": "yes"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "elicitationId": "el-1"}
    assert proxy.resolved_elicitations == [
        {
            "session_id": "sess-77",
            "elicitation_id": "el-1",
            "payload": {"answer": "yes"},
        }
    ]


def test_routes_registered_under_configured_mount_path() -> None:
    paths = {route.path for route in router.routes}
    assert "/v1/sessions" in paths
    assert "/v1/sessions/{session_id}" in paths
    assert "/v1/sessions/{session_id}/events" in paths
    assert "/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve" in paths
    assert "/api/agents" in paths
    assert OMNIGENT_BRIDGE_MOUNT_PATH == "/api/omnigent"


def test_superuser_owns_any_workflow() -> None:
    def _superuser():
        return SimpleNamespace(
            id=uuid4(), email="admin@example.com", is_superuser=True
        )

    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    proxy = _FakeProxy()
    app.dependency_overrides[get_current_user()] = _superuser
    # Service returns a foreign owner, but superuser bypasses ownership.
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(uuid4())
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    client = TestClient(app)

    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 200
    assert len(proxy.created) == 1
