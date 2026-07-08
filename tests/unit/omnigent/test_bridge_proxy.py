"""Unit tests for the Omnigent Bridge Host Protocol Facade / Proxy.

MM-1155 (source: MM-1140): proxy-mode create/get behavior, OB-§8.3 host
validation exposed through the facade, and ``session.created`` emission via the
STORY-002 store.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.omnigent.bridge_config import parse_bridge_config
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
    validate_bridge_host_fields,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

pytestmark = [pytest.mark.asyncio]


class _FakeRow:
    def __init__(self, session_id: str | None = None) -> None:
        self.omnigent_session_id = session_id
        self.target_metadata: dict[str, Any] = {}


class _FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, _FakeRow] = {}
        self.attached: list[tuple[str, str]] = []
        self.created_events: list[dict[str, Any]] = []

    async def get_or_create(
        self, *, request, endpoint_ref, agent_id, agent_name, target_metadata
    ) -> _FakeRow:
        row = self.rows.get(request.idempotency_key)
        if row is None:
            row = _FakeRow()
            row.target_metadata = dict(target_metadata)
            self.rows[request.idempotency_key] = row
        return row

    async def attach_session(self, key: str, session_id: str) -> _FakeRow:
        self.attached.append((key, session_id))
        self.rows[key].omnigent_session_id = session_id
        return self.rows[key]

    async def record_session_created(
        self, key: str, *, session_id: str, agent_id=None, endpoint_ref=None
    ) -> _FakeRow:
        # Mirror the real store: session.created is recorded once per session
        # (idempotent), so create/reuse retries never duplicate the event.
        if not any(evt["key"] == key for evt in self.created_events):
            self.created_events.append(
                {
                    "key": key,
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "endpoint_ref": endpoint_ref,
                }
            )
        return self.rows[key]


class _FakeClient:
    def __init__(self, *, create_error: Exception | None = None) -> None:
        self.created_payloads: list[dict[str, Any]] = []
        self.create_error = create_error
        self.get_calls: list[str] = []

    async def list_agents(self) -> list[dict[str, Any]]:
        return [{"id": "agent-1", "name": "codex"}]

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created_payloads.append(payload)
        if self.create_error is not None:
            raise self.create_error
        return {"id": "sess-9"}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        self.get_calls.append(session_id)
        return {"id": session_id, "status": "running", "summary": "ok"}


def _binding(key: str = "idem-1") -> BridgePrincipalBinding:
    return BridgePrincipalBinding(
        workflow_id="mm:w1",
        correlation_id="corr-1",
        idempotency_key=key,
        agent_run_id="ar-1",
    )


def _proxy(store: _FakeStore, client: _FakeClient, **kwargs) -> OmnigentBridgeSessionProxy:
    return OmnigentBridgeSessionProxy(
        run_store=store,
        client=client,
        default_agent_name="codex",
        **kwargs,
    )


async def test_create_session_forwards_persists_and_emits() -> None:
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    response = await proxy.create_session(request=req, binding=_binding())

    # Forwarded to the stock Omnigent Server with idempotency + traceability.
    assert client.created_payloads[0]["idempotency_key"] == "idem-1"
    assert client.created_payloads[0]["labels"]["moonmind.issue"] == "MM-1155"
    assert client.created_payloads[0]["workspace"] == "https://github.com/org/repo#main"
    # Session id persisted before first message, then session.created emitted.
    assert store.attached == [("idem-1", "sess-9")]
    assert store.created_events == [
        {
            "key": "idem-1",
            "session_id": "sess-9",
            "agent_id": "agent-1",
            "endpoint_ref": "default",
        }
    ]
    # Omnigent-shaped snapshot enriched with the MoonMind binding.
    assert response["id"] == "sess-9"
    assert response["status"] == "running"
    assert response["moonmind"]["workflowId"] == "mm:w1"
    assert response["moonmind"]["idempotencyKey"] == "idem-1"
    assert response["moonmind"]["reused"] is False
    assert response["moonmind"]["sourceIssue"] == "MM-1155"


async def test_create_session_reuse_is_idempotent() -> None:
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    await proxy.create_session(request=req, binding=_binding())
    response = await proxy.create_session(request=req, binding=_binding())

    assert len(client.created_payloads) == 1
    assert len(store.attached) == 1
    assert len(store.created_events) == 1
    assert response["moonmind"]["reused"] is True


async def test_create_session_records_session_created_on_reuse_recovery() -> None:
    # Partial-failure recovery: a prior attempt attached the provider session id
    # but crashed before the session.created journal write landed. A retry that
    # reuses the attached session must still emit session.created (idempotently)
    # rather than dropping it, so no upstream create happens but the event is
    # recorded.
    store, client = _FakeStore(), _FakeClient()
    key = "idem-1"
    store.rows[key] = _FakeRow(session_id="sess-existing")
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    response = await proxy.create_session(request=req, binding=_binding(key))

    assert not client.created_payloads
    assert store.created_events == [
        {
            "key": "idem-1",
            "session_id": "sess-existing",
            "agent_id": "agent-1",
            "endpoint_ref": "default",
        }
    ]
    assert response["moonmind"]["reused"] is True


async def test_create_session_rejects_managed_host_id() -> None:
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(host_type="managed", host_id="h1")

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())
    assert excinfo.value.failure_class == "user_error"
    assert not client.created_payloads


async def test_create_session_rejects_external_repo_url() -> None:
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        host_type="external",
        host_id="h1",
        workspace="https://github.com/org/repo",
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())
    assert excinfo.value.failure_class == "user_error"


async def test_create_session_missing_provider_id_is_integration_error() -> None:
    store = _FakeStore()

    class _NoIdClient(_FakeClient):
        async def create_session(self, payload):  # type: ignore[override]
            return {}

    proxy = _proxy(store, _NoIdClient())
    req = BridgeSessionCreateRequest(
        host_type="managed", workspace="https://github.com/org/repo#main"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())
    assert excinfo.value.failure_class == "integration_error"


async def test_create_session_propagates_client_failure_class() -> None:
    store = _FakeStore()
    client = _FakeClient(
        create_error=OmnigentClientError(
            "boom", status_code=502, failure_class="integration_error"
        )
    )
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        host_type="managed", workspace="https://github.com/org/repo#main"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())
    assert excinfo.value.failure_class == "integration_error"
    assert excinfo.value.status_code == 502


async def test_get_session_proxies_snapshot() -> None:
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)

    snapshot = await proxy.get_session("sess-42")

    assert snapshot["id"] == "sess-42"
    assert client.get_calls == ["sess-42"]


async def test_get_session_maps_client_error_status() -> None:
    class _NotFoundClient(_FakeClient):
        async def get_session(self, session_id):  # type: ignore[override]
            raise OmnigentClientError(
                "missing", status_code=404, failure_class="user_error"
            )

    proxy = _proxy(_FakeStore(), _NotFoundClient())

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.get_session("nope")
    assert excinfo.value.status_code == 404
    assert excinfo.value.failure_class == "user_error"


async def test_list_agents_proxies() -> None:
    proxy = _proxy(_FakeStore(), _FakeClient())
    agents = await proxy.list_agents()
    assert agents == [{"id": "agent-1", "name": "codex"}]


async def test_non_proxy_mode_fails_fast() -> None:
    embedded = parse_bridge_config(
        {"compatibility": {"hostProtocolMode": "embedded_omnigent_compatible_server"}}
    )
    proxy = _proxy(_FakeStore(), _FakeClient(), config=embedded)
    req = BridgeSessionCreateRequest(
        host_type="managed", workspace="https://github.com/org/repo#main"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())
    assert excinfo.value.status_code == 501
    assert excinfo.value.failure_class == "system_error"


async def test_validate_bridge_host_fields_managed_allows_empty_workspace() -> None:
    validate_bridge_host_fields(host_type="managed", host_id=None, workspace=None)


async def test_validate_bridge_host_fields_managed_rejects_local_path() -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        validate_bridge_host_fields(
            host_type="managed", host_id=None, workspace="/home/x/repo"
        )
    assert excinfo.value.failure_class == "user_error"


async def test_validate_bridge_host_fields_external_requires_absolute_path() -> None:
    with pytest.raises(OmnigentBridgeError):
        validate_bridge_host_fields(
            host_type="external", host_id="h1", workspace="relative/dir"
        )
