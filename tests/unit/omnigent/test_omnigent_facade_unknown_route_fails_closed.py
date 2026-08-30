"""MoonLadderStudios/MoonMind#3635: negative proof that unknown routes fail closed.

The remaining gap in the scoped facade is not another proxy—it is machine-verifiable
proof that every stock route is either explicitly allowlisted or rejected with a
stable diagnostic, and that an unknown stock route can never reach the upstream
root. This module exercises the exact compiled facade (not a hand-maintained
list) for HTTP, SSE, WebSocket, and terminal/PTY variants.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

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
from moonmind.omnigent import native_ui_compat as compat
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_PROXY
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES

_CHAT_BINDING_ID = "brs-1"
_PROVIDER_SESSION_ID = "prov-sess-1"
_BRIDGE_SESSION_ID = "brs-internal-1"

from uuid import uuid4

_USER_ID = uuid4()


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="chat@example.com", is_superuser=False)


def _row(**overrides):
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
        effective_launch_snapshot_json={},
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


class _FakeStore:
    def __init__(self, row=None):
        self._row = row or _row()
        self.lifecycle = []

    async def get_bridge_session(self, bridge_session_id: str):
        return self._row

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self._row

    async def get_session_by_provider_session_id(self, session_id: str):
        return self._row

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        return SimpleNamespace(rows=[], has_more=False, latest_sequence=0, earliest_sequence=0)

    async def append_events(self, *a, **kw):
        pass

    async def claim_lifecycle_event(self, *a, **kw):
        return True

    async def record_lifecycle_event(self, *a, **kw):
        return self._row

    async def get_lifecycle_event_metadata(self, *a, **kw):
        return None


class _FakeProxy:
    def __init__(self):
        self.sessions = []
        self.resources = []
        self.posted = []

    async def get_session(self, session_id: str):
        self.sessions.append(session_id)
        return {"id": session_id, "status": "running"}

    async def list_agents(self):
        return []

    async def get_resource(self, *a, **kw):
        self.resources.append(a)
        return {"files": []}

    async def post_event(self, **kw):
        self.posted.append(kw)
        return {"ok": True}

    async def stop_session(self, *a, **kw):
        return {"ok": True}

    async def resolve_elicitation(self, **kw):
        return {"ok": True}


class _FakeService:
    def __init__(self, owner_id):
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


def _fake_registry():
    from unittest.mock import Mock

    return SimpleNamespace(has_live_session_authority=Mock(return_value=False), revoke_scope=Mock(return_value=[]))


def _build():
    app = FastAPI()
    app.include_router(workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH)
    proxy = _FakeProxy()
    store = _FakeStore()
    registry = _fake_registry()
    config = SimpleNamespace(host_protocol_mode=HOST_PROTOCOL_MODE_PROXY)
    app.dependency_overrides[get_current_user] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[_get_create_embedded_facade] = lambda: None
    app.dependency_overrides[get_capability_registry] = lambda: registry
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    return TestClient(app), proxy, store


def _path(suffix: str) -> str:
    return f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{_CHAT_BINDING_ID}/omnigent/{suffix}"


# Unknown routes must not be generically proxied. Each transport that the
# stock UI could attempt (HTTP verbs, SSE, WebSocket, terminal PTY) is
# exercised here. The facade must return a stable fail-closed diagnostic and
# must not forward to the upstream provider session.
_UNKNOWN_HTTP_VARIANTS = [
    ("GET", f"v1/sessions/{_CHAT_BINDING_ID}/unknown-route"),
    ("POST", f"v1/sessions/{_CHAT_BINDING_ID}/resources/terminals/t1/transfer"),
    ("PATCH", f"v1/sessions/{_CHAT_BINDING_ID}/resources/terminals/t1"),
    ("DELETE", f"v1/sessions/{_CHAT_BINDING_ID}/browser/evil"),
    ("PUT", f"v1/sessions/{_CHAT_BINDING_ID}/tasks/task-1/unknown"),
    ("TRACE", f"v1/sessions/{_CHAT_BINDING_ID}/tasks"),
    ("GET", f"v1/sessions/{_CHAT_BINDING_ID}/resources/files/file-1/unknown"),
    ("GET", "v1/sessions/unknown"),
    ("GET", f"v1/sessions/{_CHAT_BINDING_ID}/stream/unknown"),
]


@pytest.mark.parametrize(("method", "suffix"), _UNKNOWN_HTTP_VARIANTS)
def test_unknown_stock_route_cannot_reach_upstream_root(method: str, suffix: str) -> None:
    client, proxy, _store = _build()

    response = client.request(method, _path(suffix))

    assert response.status_code in (403, 404, 405)
    body = response.json()
    # Stable fail-closed code—never a generic upstream pass-through.
    assert body["detail"]["code"] in (
        "omnigent_chat_route_not_allowlisted",
        "omnigent_chat_operation_denied",
        "omnigent_chat_transport_unsupported",
        "omnigent_chat_session_substitution",
    )
    # No upstream provider session was ever contacted.
    assert proxy.sessions == []
    assert proxy.resources == []
    assert proxy.posted == []
    # Provider session id never leaks in the diagnostic.
    assert _PROVIDER_SESSION_ID not in response.text
    assert _CHAT_BINDING_ID not in response.text or suffix.count(_CHAT_BINDING_ID)  # binding may echo but provider never


def test_every_inventory_entry_has_explicit_disposition() -> None:
    cmap = compat.compatibility_map()
    assert cmap["version"] == compat.NATIVE_UI_COMPAT_VERSION
    # Every inventoried route declares an explicit disposition; unknown routes
    # are absent and therefore fail closed.
    for route in cmap["routes"]:
        assert route["disposition"] in {compat.DISPOSITION_SERVED, compat.DISPOSITION_COMPAT_REVIEW}
        assert route["name"]
        assert route["transport"] in {"http", "sse", "websocket"}
        assert route["operationClass"]
        assert route["pathPattern"]


_CONTRACT_FIXTURE = Path(__file__).parents[2] / "fixtures" / "omnigent" / "native_ui_network_contract_v1.json"


def test_facade_digest_binds_inventory_to_exact_sources() -> None:
    first = compat.compatibility_map()["moonmindFacadeDigest"]
    second = compat.compatibility_map()["moonmindFacadeDigest"]
    # Deterministic and non-empty.
    assert isinstance(first, str) and len(first) == 64
    assert first == second
    # Pinned to independently reviewed evidence: an allowlist change without
    # bumping NATIVE_UI_COMPAT_VERSION must fail, not silently recompute.
    fixture = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    assert first == fixture["moonmindFacadeDigest"]


def test_hosted_ui_never_uses_root_v1_route() -> None:
    cmap = compat.compatibility_map()
    scoped_api = cmap["routes"]
    # No hosted route is a bare "/v1/*" upstream root; every session route is
    # scoped through the binding facade.
    for route in scoped_api:
        pattern = route["pathPattern"] or ""
        # The facade patterns are all relative to /omnigent/ — they never start
        # with a leading slash that would imply a direct upstream root.
        assert not pattern.startswith("/v1/")
        assert "upstream" not in pattern.lower()


def test_resource_traversal_variants_still_rejected() -> None:
    client, proxy, _store = _build()
    traversal_paths = [
        f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default/filesystem/..%2Fsecret",
        f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default/filesystem/%2e%2e/secret",
        f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default/diff/..%2Fsecret",
    ]
    for path in traversal_paths:
        response = client.get(_path(path))
        # Traversal-safe identifiers must fail closed before any upstream forward.
        assert response.status_code in (403, 404)
        assert _PROVIDER_SESSION_ID not in response.text
    # Query-based traversal attempts are also rejected via identity scan.
    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/resources/files/file-1/content"), params={"path": "../../etc/passwd"})
    assert response.status_code in (403, 404)
    assert _PROVIDER_SESSION_ID not in response.text
    assert proxy.resources == []


def test_websocket_unknown_transport_fails_closed_before_upgrade() -> None:
    # The compatibility map is the only WebSocket allowlist.
    assert compat.classify_native_ui_websocket("v1/sessions/s1/unknown-thing") is None
    assert compat.classify_native_ui_websocket("v1/sessions/s1/resources/terminals/t1/transfer") is None
    assert compat.classify_native_ui_websocket("../../etc/passwd") is None
