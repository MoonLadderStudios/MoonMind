"""MoonLadderStudios/MoonMind#4013 AC5/AC6: boot-surface routes + denied-read envelope.

The pinned native UI issues four boot reads (harnesses, session agent,
environment root, child sessions) that previously 404'd at the binding facade,
plus a root wordmark asset that 404'd at the origin root. Each now has a tested
scoped implementation: bounded binding-local projections that never enumerate
unrelated provider authority and never leak the provider session id.

A denied essential read must explain itself visibly (redacted capability
reason) instead of surfacing as an opaque 403 that leaves a blank panel.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
    workflow_chat_router,
)
from api_service.api.routers.omnigent_bridge import get_capability_registry
from api_service.auth_providers import get_current_user
from moonmind.omnigent import native_ui_compat as compat
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_PROXY
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
from moonmind.omnigent.workflow_chat_facade import match_facade_operation

_CHAT_BINDING_ID = "chatb_boot123"
_PROVIDER_SESSION_ID = "prov-boot-1"
_BRIDGE_SESSION_ID = "brs-boot-1"

_USER_ID = uuid4()


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="chat@example.com", is_superuser=False)


def _grants(**overrides):
    grants = {name: True for name in CAPABILITY_NAMES}
    grants.update(overrides)
    return grants


def _row(**overrides):
    grants = _grants(**overrides.pop("grant_overrides", {}))
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


class _FakeStore:
    def __init__(self, row=None):
        self._row = row or _row()

    async def get_bridge_session(self, bridge_session_id: str):
        return self._row

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self._row

    async def get_session_by_provider_session_id(self, session_id: str):
        return self._row

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        return SimpleNamespace(
            rows=[], has_more=False, latest_sequence=0, earliest_sequence=0
        )

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
        self.sessions: list[str] = []

    async def get_session(self, session_id: str):
        self.sessions.append(session_id)
        return {"id": session_id, "status": "running"}

    async def list_agents(self):
        return []


class _FakeService:
    def __init__(self, owner_id):
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


def _fake_registry():
    from unittest.mock import Mock

    return SimpleNamespace(
        has_live_session_authority=Mock(return_value=False),
        revoke_scope=Mock(return_value=[]),
    )


def _build(row=None):
    app = FastAPI()
    app.include_router(workflow_chat_router, prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH)
    proxy = _FakeProxy()
    store = _FakeStore(row=row)
    registry = _fake_registry()
    config = SimpleNamespace(host_protocol_mode=HOST_PROTOCOL_MODE_PROXY)
    app.dependency_overrides[get_current_user] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[get_capability_registry] = lambda: registry
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    return TestClient(app, raise_server_exceptions=False), proxy


def _path(suffix: str) -> str:
    return f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{_CHAT_BINDING_ID}/omnigent/{suffix}"


# --- AC5: the four boot routes are allowlisted ---------------------------------


def test_boot_routes_match_facade_allowlist() -> None:
    assert match_facade_operation("GET", "v1/harnesses") is not None
    match = match_facade_operation("GET", f"v1/sessions/{_CHAT_BINDING_ID}/agent")
    assert match is not None and match.operation.name == "get_session_agent"
    match = match_facade_operation(
        "GET", f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default"
    )
    assert match is not None and match.operation.name == "get_session_environment"
    match = match_facade_operation(
        "GET", f"v1/sessions/{_CHAT_BINDING_ID}/child_sessions"
    )
    assert match is not None and match.operation.name == "list_child_sessions"


def test_boot_routes_are_served_in_compat_map() -> None:
    served = {
        route["name"]
        for route in compat.compatibility_map()["routes"]
        if route["disposition"] == compat.DISPOSITION_SERVED
    }
    assert {
        "list_harnesses",
        "get_session_agent",
        "get_session_environment",
        "list_child_sessions",
    } <= served


def test_boot_projections_are_binding_local_and_redacted() -> None:
    client, proxy = _build()
    for suffix in (
        "v1/harnesses",
        f"v1/sessions/{_CHAT_BINDING_ID}/agent",
        f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default",
        f"v1/sessions/{_CHAT_BINDING_ID}/child_sessions",
    ):
        response = client.get(_path(suffix))
        assert response.status_code == 200, (suffix, response.text)
        body = response.json()
        serialized = json.dumps(body)
        # No provider session id or upstream topology ever reaches the browser.
        assert _PROVIDER_SESSION_ID not in serialized
        assert "prov-boot" not in serialized
        # Every visible session identifier is the opaque chatBindingId.
        if suffix.endswith(("/agent", "/default")):
            assert body["chatBindingId"] == _CHAT_BINDING_ID
    # Local projections never forward upstream with provider credentials.
    assert proxy.sessions == []


def test_harness_catalog_is_empty_and_non_enumerating() -> None:
    client, _ = _build()
    body = client.get(_path("v1/harnesses")).json()
    assert body.get("object") == "list", body
    assert body["data"] == []
    assert body["has_more"] is False


def test_child_sessions_are_empty_and_non_enumerating() -> None:
    client, _ = _build()
    body = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/child_sessions")).json()
    assert body["object"] == "list"
    assert body["data"] == []


# --- AC6: denied essential reads carry a redacted reason ------------------------


def test_denied_environment_read_explains_itself_without_leaking() -> None:
    row = _row(grant_overrides={"readResources": False})
    client, _ = _build(row=row)
    response = client.get(
        _path(f"v1/sessions/{_CHAT_BINDING_ID}/resources/environments/default")
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "omnigent_chat_operation_denied"
    assert detail["requiredCapability"] == "readResources"
    assert isinstance(detail["disabledReason"], str) and detail["disabledReason"]
    serialized = json.dumps(detail)
    assert _PROVIDER_SESSION_ID not in serialized


def test_missing_immutable_authority_still_denies_boot_read() -> None:
    client, _ = _build(row=_row(effective_launch_snapshot_json={}))
    response = client.get(_path(f"v1/sessions/{_CHAT_BINDING_ID}/agent"))
    assert response.status_code == 403
    assert response.json()["detail"]["disabledReason"] == "immutable_authority_missing"
