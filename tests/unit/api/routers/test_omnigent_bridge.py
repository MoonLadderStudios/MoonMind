"""Router tests for the Omnigent Bridge Session API Facade.

MM-1155 (source: MM-1140): the public session routes are exposed at the
configured mount path, POST /v1/sessions validates the MoonMind principal +
workflow ownership, and bridge failure classes map onto HTTP status codes.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    OMNIGENT_BRIDGE_MOUNT_PATH,
    _get_bridge_store,
    _get_bridge_proxy,
    _get_create_embedded_facade,
    _get_execution_service,
    _require_bridge_enabled,
    embedded_host_auth_preflight,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_proxy import (
    BridgeSessionEventRequest,
    OmnigentBridgeError,
)
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
)

_USER_ID = uuid4()

_CREATE_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions"
_AGENTS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/api/agents"
_HOSTS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/api/hosts"
_READINESS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/readiness"
_EVENTS_PATH = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/events"
_ELICITATION_RESOLVE_PATH = (
    f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/elicitations/el-1/resolve"
)

# Sentinel so ``_FakeProxy(session_owner=None)`` can distinguish "no owner
# bound" from "use the default mm:w1 owner".
_UNSET = object()


@pytest.fixture(autouse=True)
def _validated_embedded_evidence(monkeypatch):
    """Existing embedded-route tests exercise behavior beyond the #3425 gate."""

    module = importlib.import_module("api_service.api.routers.omnigent_bridge")

    async def resolved(_config):
        return {
            key: {"status": "passed"}
            for key in ("proxyConformance", "liveSmoke", "hostAuthConformance")
        }

    monkeypatch.setattr(module, "_resolve_embedded_evidence", resolved)


def test_readiness_reports_selected_mode_and_conformance_state(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.example.test")
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    client = TestClient(app)

    response = client.get(_READINESS_PATH)

    assert response.status_code == 200
    assert response.json()["selectedMode"] == "upstream_omnigent_server_proxy"
    assert response.json()["protocolProfile"] == "omnigent.server.v1"
    assert response.json()["conformanceState"] == "ready"


@pytest.mark.asyncio
async def test_embedded_preflight_gates_failed_host_auth(monkeypatch) -> None:
    host_auth_module = importlib.import_module("moonmind.omnigent.host_auth_profile")
    monkeypatch.setattr(
        host_auth_module, "assert_pinned_omnigent_auth_contract", lambda: None
    )
    monkeypatch.setitem(
        embedded_host_auth_preflight.__globals__,
        "_BRIDGE_CONFIG",
        parse_bridge_config({
            "enabled": True,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {"embedded": {
                "proxyConformanceEvidenceRef": "artifact://proxy",
                "liveSmokeEvidenceRef": "artifact://smoke",
                "hostAuthConformanceEvidenceRef": "artifact://auth",
            }},
        }),
    )
    monkeypatch.setitem(
        embedded_host_auth_preflight.__globals__, "_active_host_auth_profile",
        AsyncMock(
            return_value=HostAuthCredentialProfile(
                "managed", "env://ABSENT_HOST_TOKEN", 1
            )
        ),
    )
    result = await embedded_host_auth_preflight()
    assert result["ready"] is False
    assert result["code"] == "host_auth_secret_unavailable"
    assert "ABSENT_HOST_TOKEN" not in str(result)


def test_embedded_readiness_stays_gated_when_artifacts_are_invalid(monkeypatch) -> None:
    module = importlib.import_module("api_service.api.routers.omnigent_bridge")
    config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "arbitrary",
                    "liveSmokeEvidenceRef": "missing",
                    "hostAuthConformanceEvidenceRef": "unauthorized",
                }
            },
        }
    )

    async def invalid(_config):
        return {
            key: {
                "status": "failed",
                "reason": "evidence_unavailable_or_invalid",
            }
            for key in ("proxyConformance", "liveSmoke", "hostAuthConformance")
        }

    monkeypatch.setattr(module, "_resolve_embedded_evidence", invalid)
    monkeypatch.setattr(
        module,
        "_active_host_auth_profile",
        AsyncMock(
            side_effect=HostAuthProfileError(
                "host authentication is unavailable",
                code="host_auth_secret_unavailable",
            )
        ),
    )
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_require_bridge_enabled] = lambda: config

    response = TestClient(app).get(_READINESS_PATH)

    assert response.status_code == 200
    assert response.json()["conformanceState"] == "gated"
    assert response.json()["gateReason"] == "validated_embedded_evidence_required"


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
        self.resource_calls: list[tuple[str, str, str | None]] = []
        self.attached: list[str] = []
        self.deleted: list[str] = []
        self.create_error: OmnigentBridgeError | None = None
        self.stream_error: OmnigentBridgeError | None = None
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

    async def attach_session(self, *, session_id: str, binding):
        self.attached.append(session_id)
        return {"id": session_id, "moonmind": {"reused": True}}

    async def delete_session(self, session_id: str):
        self.deleted.append(session_id)
        return {"ok": True}

    async def stop_session(self, session_id: str):
        return await self.post_event(
            session_id=session_id,
            event=BridgeSessionEventRequest(type="stop"),
        )

    async def stream_events(self, session_id: str, *, after: int = 0):
        assert after == 0
        if self.stream_error is not None:
            raise self.stream_error
        yield {"type": "response.completed", "session": {"status": "completed"}}

    async def post_event(self, *, session_id: str, event, actor=None):
        self.posted_events.append({"session_id": session_id, "event": event})
        return {"ok": True, "type": event.type}

    async def resolve_elicitation(
        self,
        *,
        session_id: str,
        elicitation_id: str,
        payload,
        actor=None,
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

    async def list_hosts(self):
        return [{"id": "host-profile-bound", "status": "ready"}]

    async def get_resource(self, operation, session_id, value=None):
        self.resource_calls.append((operation, session_id, value))
        if operation in {"workspace_file", "workspace_diff", "session_file"}:
            return b"content"
        return {"files": [{"path": "src/main.py"}]}


class _FakeStore:
    def __init__(
        self,
        *,
        owner: Any | None = _UNSET,
        rows: list[Any] | None = None,
        session_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._owner = (
            SimpleNamespace(workflow_id="mm:w1", agent_run_id="ar-1")
            if owner is _UNSET
            else owner
        )
        self._rows = (
            rows
            if rows is not None
            else [
                SimpleNamespace(
                    event_id="evt-1",
                    bridge_session_id="brs-1",
                    sequence=1,
                    timestamp=SimpleNamespace(
                        isoformat=lambda: "2026-07-09T00:00:00+00:00"
                    ),
                    direction="host_to_moonmind",
                    event_type="response.delta",
                    normalized_status="running",
                    text_preview="hello from bridge",
                    artifact_ref=None,
                    metadata_={"responseId": "resp-1"},
                )
            ]
        )
        self._session_overrides = session_overrides or {}
        self.active_modes: dict[str, int] = {}

    async def active_host_protocol_modes(self, *, exclude_idempotency_key=None):
        self.excluded_idempotency_key = exclude_idempotency_key
        return self.active_modes

    async def get_bridge_session_owner(self, bridge_session_id: str):
        return self._owner

    async def list_events(self, bridge_session_id: str):
        return self._rows

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        rows = [row for row in self._rows if row.sequence > after]
        return SimpleNamespace(
            rows=rows[:limit],
            has_more=len(rows) > limit,
            latest_sequence=max((row.sequence for row in self._rows), default=0),
            earliest_sequence=min((row.sequence for row in self._rows), default=None),
        )

    async def get_bridge_session(self, bridge_session_id: str):
        return self._session()

    def _session(self):
        terminal_status = next(
            (
                row.normalized_status
                for row in reversed(self._rows)
                if row.normalized_status
                in {"completed", "failed", "canceled", "timed_out"}
            ),
            None,
        )
        values = dict(
            bridge_session_id="brs-1",
            moonmind_workflow_id=self._owner.workflow_id,
            moonmind_run_id="run-1",
            moonmind_agent_run_id=self._owner.agent_run_id,
            step_execution_id="step-1",
            idempotency_key="idem-1",
            status=terminal_status or "active",
            compatibility_profile="omnigent.server.v1",
            provider_profile_id="profile-1",
            host_binding_ref="host-ref",
            omnigent_session_id="session-ref",
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
        values.update(self._session_overrides)
        return SimpleNamespace(**values)

    async def resolve_projection_session(self, **kwargs):
        if self._owner is None:
            return None
        return self._session()


class _FakeEmbeddedFacade(_FakeProxy):
    def __init__(self) -> None:
        super().__init__()
        self.created: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.stream_afters: list[int] = []

    async def create_session(self, *, request, binding):
        self.created.append({"request": request, "binding": binding})
        return {
            "id": "emb_brs_1",
            "status": "creating",
            "moonmind": {
                "bridgeLocal": True,
                "hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED,
                "workflowId": binding.workflow_id,
            },
        }

    async def dispatch_runner(self, *, idempotency_key):
        return {"runnerId": "runner-1", "reused": False}

    async def get_session_owner(self, session_id: str):
        return self._session_owner

    async def stream_events(self, session_id: str, *, after: int = 0):
        assert session_id == "sess-77"
        self.stream_afters.append(after)
        yield {
            "schemaVersion": "moonmind.omnigent_bridge.event.v1",
            "sequence": 5,
            "type": "response.delta",
        }
        yield {
            "schemaVersion": "moonmind.omnigent_bridge.event.v1",
            "sequence": 5,
            "type": "terminal",
            "terminal": True,
        }

    async def stop_runner(self, *, session_id: str):
        self.stopped.append(session_id)
        return {"ok": True, "status": "stopped", "runnerId": "runner-1"}

    async def stop_session(self, session_id: str, *, payload=None, actor=None):
        return await self.stop_runner(session_id=session_id)


def _build(
    *,
    owner_id: Any = _USER_ID,
    proxy: _FakeProxy | None = None,
    store: _FakeStore | None = None,
    config: Any | None = None,
) -> tuple[TestClient, _FakeProxy, _FakeStore]:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    proxy = proxy or _FakeProxy()
    store = store or _FakeStore()
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(owner_id)
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[_get_bridge_store] = lambda: store
    if config is not None:
        app.dependency_overrides[_require_bridge_enabled] = lambda: config
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            app.dependency_overrides[_get_bridge_proxy] = lambda: None
            app.dependency_overrides[_get_create_embedded_facade] = lambda: proxy
    return TestClient(app), proxy, store


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
    client, proxy, _ = _build()
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 200
    assert resp.json()["id"] == "sess-1"
    assert len(proxy.created) == 1
    binding = proxy.created[0]["binding"]
    assert binding.workflow_id == "mm:w1"
    assert binding.idempotency_key == "idem-1"
    assert binding.correlation_id == "corr-1"


def test_create_session_blocks_mode_transition_with_active_other_mode() -> None:
    store = _FakeStore()
    store.active_modes = {HOST_PROTOCOL_MODE_EMBEDDED: 2, "unknown": 1}
    client, proxy, _ = _build(store=store)

    response = client.post(_CREATE_PATH, json=_create_body())

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "omnigent_bridge_mode_transition_blocked",
        "message": (
            "The configured Omnigent host protocol mode cannot take ownership "
            "while active sessions belong to another or an unknown mode. Drain "
            "or terminalize those sessions first."
        ),
        "selectedMode": "upstream_omnigent_server_proxy",
        "activeSessionModes": {
            HOST_PROTOCOL_MODE_EMBEDDED: 2,
            "unknown": 1,
        },
    }
    assert not proxy.created
    assert store.excluded_idempotency_key == "idem-1"


def test_create_session_requires_idempotency_label() -> None:
    client, _, _ = _build()
    body = _create_body(labels={"moonmind.workflow_id": "mm:w1"})
    resp = client.post(_CREATE_PATH, json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "user_error"


def test_create_session_requires_workflow_label() -> None:
    client, _, _ = _build()
    body = _create_body(labels={"moonmind.idempotency_key": "idem-1"})
    resp = client.post(_CREATE_PATH, json=body)
    assert resp.status_code == 400


def test_create_session_denies_non_owner() -> None:
    client, proxy, _ = _build(owner_id=uuid4())  # different owner
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "workflow_ownership_denied"
    assert not proxy.created


def test_create_session_maps_user_error_to_400() -> None:
    proxy = _FakeProxy()
    proxy.create_error = OmnigentBridgeError("bad host", failure_class="user_error")
    client, _, _ = _build(proxy=proxy)
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 400
    assert resp.json()["detail"]["message"] == "bad host"


def test_create_session_maps_integration_error_to_502() -> None:
    proxy = _FakeProxy()
    proxy.create_error = OmnigentBridgeError(
        "upstream down", failure_class="integration_error"
    )
    client, _, _ = _build(proxy=proxy)
    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 502


def test_get_session_returns_snapshot_for_owner() -> None:
    client, _, _ = _build()  # user owns mm:w1 (the session owner)
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77")
    assert resp.status_code == 200
    assert resp.json() == {"id": "sess-77", "status": "completed"}


def test_get_session_unknown_session_is_not_found() -> None:
    # A provider session id the bridge does not own is never proxied upstream.
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-unknown")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "omnigent_bridge_session_unknown"


def test_get_session_denies_non_owner() -> None:
    # The session is owned by mm:w1, but the caller does not own that workflow.
    client, _, _ = _build(owner_id=uuid4())
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "workflow_ownership_denied"


def test_list_agents_returns_catalog() -> None:
    client, _, _ = _build()
    resp = client.get(_AGENTS_PATH)
    assert resp.status_code == 200
    assert resp.json() == [{"id": "agent-1", "name": "codex"}]


@pytest.mark.parametrize("alias", ["agentId", "agent_id"])
def test_list_agents_accepts_upstream_identifier_aliases(alias: str) -> None:
    class _AliasedAgentProxy(_FakeProxy):
        async def list_agents(self):
            return [{alias: "agent-1", "name": "codex"}]

    client, _, _ = _build(proxy=_AliasedAgentProxy())

    response = client.get(_AGENTS_PATH)

    assert response.status_code == 200
    assert response.json()[0]["id"] == "agent-1"


def test_list_hosts_returns_bounded_profile_discovery() -> None:
    client, _, _ = _build()
    resp = client.get(_HOSTS_PATH)
    assert resp.status_code == 200
    assert resp.json() == [{"id": "host-profile-bound", "status": "ready"}]


def test_resource_route_authorizes_before_proxying() -> None:
    client, proxy, _ = _build()
    path = (
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/resources/"
        "environments/default/filesystem/src/main.py"
    )
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.content == b"content"
    assert proxy.resource_calls == [("workspace_file", "sess-77", "src/main.py")]


def test_resource_route_rejects_unknown_session_without_proxying() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown/resources/files"
    )
    assert resp.status_code == 404
    assert proxy.resource_calls == []


def test_attach_reconciles_existing_provider_session() -> None:
    client, proxy, _ = _build()
    resp = client.post(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/attach",
        json=_create_body(),
    )
    assert resp.status_code == 200
    assert proxy.attached == ["sess-77"]


def test_delete_authorizes_and_delegates() -> None:
    client, proxy, _ = _build()
    resp = client.delete(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77")
    assert resp.status_code == 200
    assert proxy.deleted == ["sess-77"]


def test_provider_stream_authorizes_and_proxies_sse() -> None:
    client, _, _ = _build()
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream")
    assert resp.status_code == 200
    assert '"type":"response.completed"' in resp.text
    assert "id:" not in resp.text


def test_embedded_stream_emits_durable_sse_cursor_and_resumes() -> None:
    monkeypatch_config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    embedded = _FakeEmbeddedFacade()
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = lambda: monkeypatch_config
    app.dependency_overrides[_get_create_embedded_facade] = lambda: embedded

    response = TestClient(app).get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream",
        headers={"Last-Event-ID": "4"},
    )

    assert response.status_code == 200
    assert embedded.stream_afters == [4]
    assert response.text.count("id: 5\n") == 2
    assert '"type":"response.delta"' in response.text
    assert '"type":"terminal"' in response.text


def test_embedded_public_routes_use_same_authorized_facade_boundary() -> None:
    config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    embedded = _FakeEmbeddedFacade()
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    app.dependency_overrides[_get_create_embedded_facade] = lambda: embedded
    client = TestClient(app)
    base = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77"

    responses = [
        client.get(_AGENTS_PATH),
        client.get(_HOSTS_PATH),
        client.get(base),
        client.post(f"{base}/attach", json=_create_body()),
        client.post(f"{base}/events", json={"type": "message"}),
        client.post(
            f"{base}/elicitations/el-1/resolve", json={"answer": "yes"}
        ),
        client.get(f"{base}/resources/files"),
        client.get(f"{base}/stream"),
        client.delete(base),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert embedded.posted_events[0]["event"].type == "message"
    assert embedded.resolved_elicitations[0]["elicitation_id"] == "el-1"
    assert embedded.resource_calls == [("session_files", "sess-77", None)]


def test_provider_stream_encodes_async_failure_without_disclosing_details() -> None:
    proxy = _FakeProxy()
    proxy.stream_error = OmnigentBridgeError(
        "upstream unavailable",
        failure_class="integration_error",
        status_code=502,
        code="omnigent_bridge_upstream_transport",
    )
    client, _, _ = _build(proxy=proxy)

    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream")

    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "omnigent_bridge_upstream_transport" in resp.text
    assert "upstream unavailable" not in resp.text


@pytest.mark.parametrize(
    ("upstream_status", "expected_status"),
    [(401, 401), (403, 403), (404, 404), (409, 409), (429, 429),
     (500, 500), (502, 502), (504, 504)],
)
def test_owner_routes_preserve_bounded_http_and_sse_failure_contract(
    upstream_status: int, expected_status: int
) -> None:
    code = f"omnigent_bridge_upstream_{upstream_status}"
    proxy = _FakeProxy()
    proxy.create_error = OmnigentBridgeError(
        "credential-shaped Bearer secret must stay bounded",
        failure_class="integration_error",
        status_code=upstream_status,
        code=code,
    )
    create_client, _, _ = _build(proxy=proxy)
    http_response = create_client.post(_CREATE_PATH, json=_create_body())
    assert http_response.status_code == expected_status
    assert http_response.json()["detail"]["code"] == code

    proxy.create_error = None
    proxy.stream_error = OmnigentBridgeError(
        "credential-shaped Bearer secret must stay bounded",
        failure_class="integration_error",
        status_code=upstream_status,
        code=code,
    )
    sse_response = create_client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream"
    )
    assert sse_response.status_code == 200
    assert "event: error" in sse_response.text
    assert code in sse_response.text
    assert "Bearer secret" not in sse_response.text


def test_all_resource_route_classes_delegate() -> None:
    client, proxy, _ = _build()
    base = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/resources"
    paths = [
        f"{base}/environments/default/changes",
        f"{base}/environments/default/filesystem",
        f"{base}/environments/default/filesystem/src/main.py",
        f"{base}/environments/default/diff/src/main.py",
        f"{base}/files",
        f"{base}/files/file-1/content",
    ]
    for path in paths:
        assert client.get(path).status_code == 200
    assert [call[0] for call in proxy.resource_calls] == [
        "changed_files",
        "workspace_files",
        "workspace_file",
        "workspace_diff",
        "session_files",
        "session_file",
    ]


def test_all_id_bearing_route_classes_reject_unknown_owner() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    base = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown"
    requests = [
        lambda: client.get(base),
        lambda: client.delete(base),
        lambda: client.post(f"{base}/events", json={"type": "interrupt"}),
        lambda: client.post(
            f"{base}/elicitations/el-1/resolve", json={"answer": "yes"}
        ),
        lambda: client.get(f"{base}/stream"),
        lambda: client.get(f"{base}/resources/environments/default/changes"),
        lambda: client.get(f"{base}/resources/environments/default/filesystem"),
        lambda: client.get(f"{base}/resources/environments/default/filesystem/a.txt"),
        lambda: client.get(f"{base}/resources/environments/default/diff/a.txt"),
        lambda: client.get(f"{base}/resources/files"),
        lambda: client.get(f"{base}/resources/files/file-1/content"),
    ]
    assert all(call().status_code == 404 for call in requests)
    assert proxy.attached == []
    assert proxy.deleted == []
    assert proxy.posted_events == []
    assert proxy.resolved_elicitations == []
    assert proxy.resource_calls == []


@pytest.mark.parametrize("resolution", ["deleted_binding", "ambiguous_binding"])
def test_all_owner_authorized_route_classes_fail_closed_before_upstream(
    resolution: str,
) -> None:
    """Deleted and ambiguous bindings share the non-enumerating API policy."""
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    base = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/{resolution}"
    requests = (
        lambda: client.get(base),
        lambda: client.get(f"{base}/stream"),
        lambda: client.post(f"{base}/events", json={"type": "interrupt"}),
        lambda: client.delete(base),
        lambda: client.get(f"{base}/resources/environments/default/changes"),
        lambda: client.get(f"{base}/resources/files"),
    )

    responses = [request() for request in requests]

    assert {response.status_code for response in responses} == {404}
    assert all(
        response.json()["detail"]["code"] == "omnigent_bridge_session_unknown"
        for response in responses
    )
    assert proxy.posted_events == []
    assert proxy.deleted == []
    assert proxy.resource_calls == []


def test_duplicate_authorization_headers_never_survive_api_or_sse_failures() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    secret_values = ("Bearer ghp_duplicate_one", "Bearer github_pat_duplicate_two")
    headers = [("Authorization", value) for value in secret_values]
    paths = (
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown",
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown/stream",
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown/resources/files",
    )

    responses = [client.request("GET", path, headers=headers) for path in paths]

    assert {response.status_code for response in responses} == {404}
    serialized = "\n".join(response.text for response in responses)
    assert all(secret not in serialized for secret in secret_values)
    assert proxy.resource_calls == []


@pytest.mark.parametrize("binding_state", ["unknown", "deleted", "ambiguous"])
def test_every_id_route_non_enumerates_unresolvable_bindings_without_upstream(
    binding_state: str,
) -> None:
    """All unresolved durable-binding states share the no-upstream policy."""

    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    base = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/session-{binding_state}"
    calls = [
        lambda: client.get(base),
        lambda: client.delete(base),
        lambda: client.post(f"{base}/events", json={"type": "interrupt"}),
        lambda: client.post(
            f"{base}/elicitations/el-1/resolve", json={"answer": "yes"}
        ),
        lambda: client.get(f"{base}/stream"),
        lambda: client.get(f"{base}/resources/environments/default/changes"),
        lambda: client.get(f"{base}/resources/environments/default/filesystem"),
        lambda: client.get(f"{base}/resources/environments/default/filesystem/a.txt"),
        lambda: client.get(f"{base}/resources/environments/default/diff/a.txt"),
        lambda: client.get(f"{base}/resources/files"),
        lambda: client.get(f"{base}/resources/files/file-1/content"),
    ]
    responses = [call() for call in calls]
    assert {response.status_code for response in responses} == {404}
    assert {
        response.json()["detail"]["code"] for response in responses
    } == {"omnigent_bridge_session_unknown"}
    assert proxy.posted_events == []
    assert proxy.resolved_elicitations == []
    assert proxy.resource_calls == []
    assert proxy.deleted == []


def test_duplicate_authorization_headers_never_reach_upstream_or_response() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    response = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/unknown/resources/files",
        headers=[
            ("Authorization", "Bearer user-jwt-secret-one"),
            ("Authorization", "Bearer user-jwt-secret-two"),
        ],
    )
    assert response.status_code == 404
    rendered = response.text
    assert "user-jwt-secret" not in rendered
    assert proxy.resource_calls == []


def test_post_event_authorizes_and_delegates() -> None:
    client, proxy, _ = _build()
    resp = client.post(_EVENTS_PATH, json={"type": "interrupt"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "type": "interrupt"}
    assert proxy.posted_events[0]["session_id"] == "sess-77"
    assert proxy.posted_events[0]["event"].type == "interrupt"


def test_post_event_unknown_session_is_not_found() -> None:
    proxy = _FakeProxy(session_owner=None)
    client, _, _ = _build(proxy=proxy)
    resp = client.post(_EVENTS_PATH, json={"type": "interrupt"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "omnigent_bridge_session_unknown"
    assert not proxy.posted_events


def test_resolve_elicitation_authorizes_and_delegates() -> None:
    client, proxy, _ = _build()
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


def test_resolve_elicitation_reports_structured_error_when_mode_has_no_facade() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_get_bridge_proxy] = lambda: None

    response = TestClient(app).post(
        _ELICITATION_RESOLVE_PATH, json={"answer": "yes"}
    )

    assert response.status_code == 501
    assert response.json()["detail"] == {
        "code": "omnigent_bridge_mode_unsupported",
        "message": "Unsupported bridge mode",
    }


def test_session_authorization_reports_structured_error_when_proxy_is_missing() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_get_bridge_proxy] = lambda: None

    response = TestClient(app).delete(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77"
    )

    assert response.status_code == 501
    assert response.json()["detail"] == {
        "code": "omnigent_bridge_mode_unsupported",
        "message": "Unsupported bridge mode",
    }


def test_resolve_bridge_session_projection_returns_latest_binding() -> None:
    client, _, _ = _build(store=_FakeStore(session_overrides={
        "provider_lease_id": "provider-lease-1",
        "credential_generation": 4,
        "host_lease_ref": "host-lease-1",
        "omnigent_host_id": "host-1",
        "omnigent_runner_id": "runner-1",
        "effective_launch_snapshot_json": {
            "hostMode": "on_demand_docker",
            "executionProfileRef": "codex-default@2",
            "launchPolicyRef": "restricted@3",
            "snapshotRef": "omnigent-launch:sha256:safe-ref",
        },
    }))
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/resolve?workflowId=mm%3Aw1"
    )
    assert resp.status_code == 200
    assert resp.json()["bridgeSessionId"] == "brs-1"
    assert resp.json()["workflowId"] == "mm:w1"
    expected_identity = {
        "providerLeaseRef": "provider-lease-1",
        "credentialGeneration": 4,
        "hostLeaseRef": "host-lease-1",
        "hostMode": "on_demand_docker",
        "executionProfileRef": "codex-default@2",
        "launchPolicyRef": "restricted@3",
        "effectiveLaunchSnapshotRef": "omnigent-launch:sha256:safe-ref",
        "omnigentHostRef": "host-1",
        "omnigentRunnerRef": "runner-1",
    }
    assert {key: resp.json()[key] for key in expected_identity} == expected_identity


def test_resolve_bridge_session_projection_filters_capabilities_to_booleans() -> None:
    store = _FakeStore(session_overrides={"metadata_": {"interventionCapabilities": {
        "sendFollowUp": True, "interruptTurn": False, "malformed": "yes", 7: True,
    }}})
    client, _, _ = _build(store=store)
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/resolve?workflowId=mm%3Aw1"
    )
    assert resp.status_code == 200
    assert resp.json()["capabilities"] == {"sendFollowUp": True, "interruptTurn": False}


def test_resolve_bridge_session_projection_denies_absent_capabilities() -> None:
    client, _, _ = _build(store=_FakeStore(session_overrides={"metadata_": {}}))
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/resolve?workflowId=mm%3Aw1"
    )
    assert resp.status_code == 200
    assert resp.json()["capabilities"] == {}


def test_list_bridge_session_events_returns_chat_projection_shape() -> None:
    client, _, _ = _build()
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bridgeSessionId"] == "brs-1"
    assert body["schemaVersion"] == "moonmind.bridge-session-events-page.v1"
    assert body["hasMore"] is False
    event = body["items"][0]
    assert event["sequence"] == 1
    assert event["stream"] == "stdout"
    assert event["text"] == "hello from bridge"
    assert event["kind"] == "assistant_message_delta"
    assert event["sessionId"] == "brs-1"
    assert event["metadata"]["source"] == "omnigent_bridge"


def test_get_bridge_session_resources_returns_authorized_terminal_projection() -> None:
    projection = {
        "schemaVersion": "moonmind.omnigent.resource_projection.v1",
        "completeness": "complete",
        "groups": [
            {"groupKey": "changed_files", "title": "Changed files", "resources": []}
        ],
    }
    client, _, _ = _build(
        store=_FakeStore(
            session_overrides={
                "status": "completed",
                "terminal_refs": {"resourceProjection": projection},
            }
        )
    )
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/resources")
    assert resp.status_code == 200
    assert resp.json() == projection


def test_list_bridge_session_events_handles_nullable_event_type() -> None:
    rows = [
        SimpleNamespace(
            event_id="evt-null",
            bridge_session_id="brs-1",
            sequence=1,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:00+00:00"),
            direction="host_to_moonmind",
            event_type=None,
            normalized_status="running",
            text_preview=None,
            artifact_ref=None,
            metadata_={},
        ),
        SimpleNamespace(
            event_id="evt-delta",
            bridge_session_id="brs-1",
            sequence=2,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:01+00:00"),
            direction="host_to_moonmind",
            event_type="response.output_text.delta",
            normalized_status="running",
            text_preview="chunk",
            artifact_ref=None,
            metadata_={},
        ),
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))

    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events")

    assert resp.status_code == 200
    events = resp.json()["items"]
    assert events[0]["stream"] == "stdout"
    assert events[0]["text"] == "Bridge session event."
    assert events[0]["kind"] == "system_annotation"
    assert events[1]["kind"] == "assistant_message_delta"


def test_stream_bridge_session_events_keeps_since_and_stops_on_terminal() -> None:
    rows = [
        SimpleNamespace(
            event_id="evt-1",
            bridge_session_id="brs-1",
            sequence=1,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:00+00:00"),
            direction="host_to_moonmind",
            event_type="response.output_text.delta",
            normalized_status="running",
            text_preview="old",
            artifact_ref=None,
            metadata_={},
        ),
        SimpleNamespace(
            event_id="evt-2",
            bridge_session_id="brs-1",
            sequence=2,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:01+00:00"),
            direction="host_to_moonmind",
            event_type="response.completed",
            normalized_status="completed",
            text_preview="done",
            artifact_ref=None,
            metadata_={},
        ),
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))

    with client.stream(
        "GET", f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/stream?since=1"
    ) as resp:
        body = resp.read().decode()

    assert resp.status_code == 200
    assert "evt-1" not in body
    assert "evt-2" in body
    assert "event: bridge_event" in body
    assert "id: 2" in body


def test_event_page_is_bounded_and_cursor_based() -> None:
    rows = [
        SimpleNamespace(
            event_id=f"evt-{sequence}",
            bridge_session_id="brs-1",
            sequence=sequence,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:00+00:00"),
            direction="host_to_moonmind",
            event_type="response.delta",
            normalized_status="running",
            text_preview=str(sequence),
            artifact_ref=None,
            metadata_={},
        )
        for sequence in range(1, 5)
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events?after=1&limit=2"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [item["sequence"] for item in body["items"]] == [2, 3]
    assert body["nextCursor"] == "3"
    assert body["hasMore"] is True
    assert body["latestSequence"] == 4


def test_list_bridge_session_events_denies_non_owner() -> None:
    client, _, _ = _build(owner_id=uuid4())
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events")
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"code": "omnigent_bridge_session_unknown"}


def test_unknown_and_foreign_sessions_have_identical_projection_failure() -> None:
    foreign, _, _ = _build(owner_id=uuid4())
    unknown, _, _ = _build(store=_FakeStore(owner=None))
    for suffix in ("events", "stream"):
        foreign_response = foreign.get(
            f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/{suffix}"
        )
        unknown_response = unknown.get(
            f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/{suffix}"
        )
        assert foreign_response.status_code == unknown_response.status_code == 404
        assert foreign_response.json() == unknown_response.json()


def test_stream_resumes_from_greatest_cursor_source() -> None:
    rows = [
        SimpleNamespace(
            event_id=f"evt-{sequence}",
            bridge_session_id="brs-1",
            sequence=sequence,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-07-09T00:00:00+00:00"),
            direction="host_to_moonmind",
            event_type="response.delta",
            normalized_status="completed" if sequence == 3 else "running",
            text_preview=str(sequence),
            artifact_ref=None,
            metadata_={},
        )
        for sequence in range(1, 4)
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))
    resp = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/stream?cursor=1",
        headers={"Last-Event-ID": "2"},
    )
    assert resp.status_code == 200
    assert "evt-1" not in resp.text
    assert "evt-2" not in resp.text
    assert "evt-3" in resp.text


def test_event_page_projects_unavailable_replay_as_explicit_retention_gap() -> None:
    rows = [
        SimpleNamespace(
            event_id=f"evt-{sequence}",
            bridge_session_id="brs-1",
            sequence=sequence,
            timestamp=SimpleNamespace(
                isoformat=lambda: "2026-07-09T00:00:00+00:00"
            ),
            direction="host_to_moonmind",
            event_type="response.completed" if sequence == 4 else "response.delta",
            normalized_status="completed" if sequence == 4 else "running",
            text_preview=str(sequence),
            artifact_ref=None,
            metadata_={},
        )
        for sequence in (3, 4)
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))

    response = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events?after=0"
    )

    assert response.status_code == 200
    assert response.json()["retentionGap"] == {
        "requestedAfter": 0,
        "earliestAvailable": 3,
    }
    assert [item["sequence"] for item in response.json()["items"]] == [3, 4]
    assert response.json()["terminal"] is True


def test_sse_projects_unavailable_replay_gap_and_stops_before_later_events() -> None:
    rows = [
        SimpleNamespace(
            event_id="evt-3",
            bridge_session_id="brs-1",
            sequence=3,
            timestamp=SimpleNamespace(
                isoformat=lambda: "2026-07-09T00:00:00+00:00"
            ),
            direction="host_to_moonmind",
            event_type="response.delta",
            normalized_status="running",
            text_preview="unavailable history follows",
            artifact_ref=None,
            metadata_={},
        )
    ]
    client, _, _ = _build(store=_FakeStore(rows=rows))

    response = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/stream?cursor=0"
    )

    assert response.status_code == 200
    assert "event: retention_gap" in response.text
    assert '"requestedAfter":0' in response.text
    assert '"earliestAvailable":3' in response.text
    assert "event: bridge_event" not in response.text


def test_terminal_page_falls_back_to_durable_session_evidence_without_events() -> None:
    store = _FakeStore(
        rows=[],
        session_overrides={
            "status": "failed",
            "terminal_refs": {
                "failureClass": "execution_error",
                "failureCode": "provider_failed",
                "summary": "failed before stream",
                "cleanupState": "completed",
                "leaseReleaseState": "released",
            },
            "diagnostics_ref": "artifact://diagnostics",
            "capture_manifest_ref": "artifact://capture",
            "final_snapshot_ref": "artifact://final",
            "external_state_ref": "artifact://external",
        },
    )
    client, _, _ = _build(store=store)
    resp = client.get(f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/events")
    assert resp.status_code == 200
    envelope = resp.json()["terminalEnvelope"]
    assert resp.json()["terminal"] is True
    assert envelope["failureClass"] == "execution_error"
    assert envelope["diagnosticsRef"] == "artifact://diagnostics"
    assert envelope["captureManifestRef"] == "artifact://capture"
    assert envelope["finalSnapshotRef"] == "artifact://final"
    assert envelope["externalStateRef"] == "artifact://external"
    assert envelope["cleanupState"] == "completed"
    assert envelope["leaseReleaseState"] == "released"


def test_routes_registered_under_configured_mount_path() -> None:
    paths = {route.path for route in router.routes}
    assert {
        "/api/agents",
        "/api/hosts",
        "/v1/sessions",
        "/v1/sessions/{session_id}",
        "/v1/sessions/{session_id}/attach",
        "/v1/sessions/{session_id}/events",
        "/v1/sessions/{session_id}/stream",
        "/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve",
        "/v1/sessions/{session_id}/resources/environments/default/changes",
        "/v1/sessions/{session_id}/resources/environments/default/filesystem",
        "/v1/sessions/{session_id}/resources/environments/default/filesystem/{path:path}",
        "/v1/sessions/{session_id}/resources/environments/default/diff/{path:path}",
        "/v1/sessions/{session_id}/resources/files",
        "/v1/sessions/{session_id}/resources/files/{file_id}/content",
        "/bridge-sessions/resolve",
        "/bridge-sessions/{bridge_session_id}/events",
        "/bridge-sessions/{bridge_session_id}/stream",
    } <= paths
    assert OMNIGENT_BRIDGE_MOUNT_PATH == "/api/omnigent"


def test_superuser_owns_any_workflow() -> None:
    def _superuser():
        return SimpleNamespace(id=uuid4(), email="admin@example.com", is_superuser=True)

    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    proxy = _FakeProxy()
    app.dependency_overrides[get_current_user()] = _superuser
    # Service returns a foreign owner, but superuser bypasses ownership.
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(uuid4())
    app.dependency_overrides[_get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[_get_bridge_store] = _FakeStore
    client = TestClient(app)

    resp = client.post(_CREATE_PATH, json=_create_body())
    assert resp.status_code == 200
    assert len(proxy.created) == 1


def test_create_session_available_in_embedded_mode() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = _FakeEmbeddedFacade()
    embedded_config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = lambda: embedded_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
    app.dependency_overrides[_get_bridge_store] = _FakeStore
    client = TestClient(app)

    resp = client.post(
        _CREATE_PATH,
        json=_create_body(host_type="external", host_id="host-1", workspace="/repo"),
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == "emb_brs_1"
    assert resp.json()["moonmind"]["bridgeLocal"] is True
    assert len(facade.created) == 1
    assert facade.created[0]["binding"].workflow_id == "mm:w1"


def test_stop_session_event_dispatches_to_embedded_exact_host_facade() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = _FakeEmbeddedFacade()
    embedded_config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = lambda: embedded_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
    client = TestClient(app)

    response = client.post(_EVENTS_PATH, json={"type": "stop"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "stopped",
        "runnerId": "runner-1",
    }
    assert facade.stopped == ["sess-77"]


def test_interrupt_embedded_control_is_explicitly_unsupported() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    facade = _FakeEmbeddedFacade()
    embedded_config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(_USER_ID)
    app.dependency_overrides[_require_bridge_enabled] = lambda: embedded_config
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
    client = TestClient(app)

    response = client.post(_EVENTS_PATH, json={"type": "interrupt"})

    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "omnigent_embedded_control_unsupported"
    assert facade.stopped == []


def test_public_openapi_uses_typed_mode_neutral_contracts() -> None:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    schema = app.openapi()
    paths = schema["paths"]

    expected = {
        _CREATE_PATH: ("post", "OmnigentSessionResponse"),
        f"{_CREATE_PATH}/{{session_id}}": ("get", "OmnigentSessionResponse"),
        f"{_CREATE_PATH}/{{session_id}}/attach": (
            "post",
            "OmnigentSessionResponse",
        ),
        _AGENTS_PATH: ("get", "OmnigentAgentResponse"),
        _HOSTS_PATH: ("get", "OmnigentHostResponse"),
    }
    for path, (method, model_name) in expected.items():
        operation = paths[path][method]
        assert model_name in str(operation["responses"]["200"])
        assert "OmnigentPublicErrorResponse" in str(operation["responses"]["404"])

    stream = paths[f"{_CREATE_PATH}/{{session_id}}/stream"]["get"]
    assert "moonmind.omnigent_bridge.event.v1" in str(stream["responses"]["200"])


def test_unknown_stream_schema_version_emits_stable_visible_error() -> None:
    class _FutureSchemaProxy(_FakeProxy):
        async def stream_events(self, session_id: str, *, after: int = 0):
            yield {
                "schemaVersion": "moonmind.omnigent_bridge.event.v2",
                "type": "response.delta",
            }

    config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )
    client, _, _ = _build(proxy=_FutureSchemaProxy(), config=config)
    response = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream"
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "omnigent_bridge_schema_version_unsupported" in response.text
    assert "moonmind.omnigent_bridge.event.v2" not in response.text


def test_proxy_stream_passes_through_untyped_upstream_frames() -> None:
    class _UntypedStreamProxy(_FakeProxy):
        async def stream_events(self, session_id: str, *, after: int = 0):
            yield {"session": {"status": "running"}}
            yield {"type": "response.completed", "session": {"status": "completed"}}

    client, _, _ = _build(proxy=_UntypedStreamProxy())

    response = client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream"
    )

    assert response.status_code == 200
    assert 'data: {"session":{"status":"running"}}' in response.text
    assert 'data: {"type":"response.completed"' in response.text
    assert "event: error" not in response.text


def test_proxy_and_embedded_share_unknown_and_non_owner_error_contracts() -> None:
    embedded_config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )

    def mode_client(*, embedded: bool, owner: Any | None, caller: Any) -> TestClient:
        facade = _FakeEmbeddedFacade() if embedded else _FakeProxy(session_owner=owner)
        facade._session_owner = owner
        app = FastAPI()
        app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
        app.dependency_overrides[get_current_user()] = _mock_user
        app.dependency_overrides[_get_execution_service] = lambda: _FakeService(caller)
        if embedded:
            app.dependency_overrides[_require_bridge_enabled] = lambda: embedded_config
            app.dependency_overrides[_get_bridge_proxy] = lambda: None
            app.dependency_overrides[_get_create_embedded_facade] = lambda: facade
        else:
            app.dependency_overrides[_get_bridge_proxy] = lambda: facade
        return TestClient(app)

    owner = SimpleNamespace(workflow_id="mm:w1", agent_run_id="ar-1")
    cases = ((None, _USER_ID, 404), (owner, uuid4(), 403))
    for binding, caller, expected_status in cases:
        proxy_client = mode_client(embedded=False, owner=binding, caller=caller)
        embedded_client = mode_client(embedded=True, owner=binding, caller=caller)
        for suffix, method, body in (
            ("", "get", None),
            ("", "delete", None),
            ("/events", "post", {"type": "message"}),
            ("/stream", "get", None),
        ):
            path = f"{_CREATE_PATH}/sess-77{suffix}"
            proxy_response = proxy_client.request(method, path, json=body)
            embedded_response = embedded_client.request(method, path, json=body)
            assert (
                proxy_response.status_code
                == embedded_response.status_code
                == expected_status
            )
            assert proxy_response.json() == embedded_response.json()
