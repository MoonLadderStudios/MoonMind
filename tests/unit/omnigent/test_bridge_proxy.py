"""Unit tests for the Omnigent Bridge Host Protocol Facade / Proxy.

MM-1155 (source: MM-1140): proxy-mode create/get behavior, OB-§8.3 host
validation exposed through the facade, and ``session.created`` emission via the
STORY-002 store.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.bridge_config import parse_bridge_config
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
    _bound_resource_lists,
    _safe_resource_identifier,
    validate_bridge_host_fields,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

pytestmark = [pytest.mark.asyncio]


class _FakeRow:
    def __init__(
        self,
        session_id: str | None = None,
        *,
        workflow_id: str | None = None,
        agent_run_id: str | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        endpoint_ref: str | None = None,
    ) -> None:
        self.omnigent_session_id = session_id
        self.moonmind_workflow_id = workflow_id
        self.moonmind_agent_run_id = agent_run_id
        self.omnigent_agent_id = agent_id
        self.omnigent_agent_name = agent_name
        self.omnigent_endpoint_ref = endpoint_ref
        self.target_metadata: dict[str, Any] = {}


class _FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, _FakeRow] = {}
        self.attached: list[tuple[str, str]] = []
        self.created_events: list[dict[str, Any]] = []

    async def get_existing(self, idempotency_key: str) -> _FakeRow | None:
        return self.rows.get(idempotency_key)

    async def get_session_owner(self, session_id: str):
        for row in self.rows.values():
            if row.omnigent_session_id == session_id:
                return SimpleNamespace(
                    workflow_id=row.moonmind_workflow_id,
                    agent_run_id=row.moonmind_agent_run_id,
                )
        return None

    async def get_or_create(
        self,
        *,
        request,
        endpoint_ref,
        agent_id,
        agent_name,
        target_metadata,
        workflow_id=None,
        agent_run_id=None,
    ) -> _FakeRow:
        row = self.rows.get(request.idempotency_key)
        if row is None:
            # Mirror the real store: the *verified* MoonMind owner is persisted
            # from the explicit binding override, not the synthesized request's
            # correlation id.
            row = _FakeRow(
                workflow_id=workflow_id,
                agent_run_id=agent_run_id,
                agent_id=agent_id,
                agent_name=agent_name,
                endpoint_ref=endpoint_ref,
            )
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
        return self.rows.get(key)


class _FakeClient:
    def __init__(self, *, create_error: Exception | None = None) -> None:
        self.created_payloads: list[dict[str, Any]] = []
        self.create_error = create_error
        self.get_calls: list[str] = []
        self.posted_events: list[tuple[str, dict[str, Any]]] = []
        self.resolved_elicitations: list[tuple[str, str, dict[str, Any]]] = []
        self.list_agents_calls = 0

    async def list_agents(self) -> list[dict[str, Any]]:
        self.list_agents_calls += 1
        return [{"id": "agent-1", "name": "codex"}]

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created_payloads.append(payload)
        if self.create_error is not None:
            raise self.create_error
        return {"id": "sess-9"}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        self.get_calls.append(session_id)
        return {
            "id": session_id,
            "status": "running",
            "summary": "ok",
            "idempotency_key": "idem-1",
        }

    async def list_hosts(self) -> list[dict[str, Any]]:
        return [{"id": "host-1", "status": "ready"}]

    async def post_event(
        self, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.posted_events.append((session_id, payload))
        return {"ok": True, "type": payload["type"]}

    async def resolve_elicitation(
        self, session_id: str, elicitation_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.resolved_elicitations.append((session_id, elicitation_id, payload))
        return {"ok": True, "elicitationId": elicitation_id}

    async def list_changed_files(self, session_id: str) -> dict[str, Any]:
        return {"items": [{"path": "src/app.py"}]}

    async def list_workspace_files(self, session_id: str) -> dict[str, Any]:
        return {"items": [{"path": "README.md"}]}

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        return f"contents:{path}\n".encode()

    async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
        return f"diff -- {path}\n".encode()

    async def list_session_files(self, session_id: str) -> dict[str, Any]:
        return {"items": [{"id": "file-1", "filename": "session.log"}]}

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        return f"session:{file_id}\n".encode()


def _binding(key: str = "idem-1") -> BridgePrincipalBinding:
    return BridgePrincipalBinding(
        workflow_id="mm:w1",
        correlation_id="corr-1",
        idempotency_key=key,
        agent_run_id="ar-1",
    )


def _proxy(
    store: _FakeStore, client: _FakeClient, **kwargs
) -> OmnigentBridgeSessionProxy:
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
    assert (
        client.created_payloads[0]["labels"]["moonmind.issue"]
        == "MoonLadderStudios/MoonMind#3361"
    )
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
    assert response["moonmind"]["sourceIssue"] == "MoonLadderStudios/MoonMind#3361"


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
    store.rows[key] = _FakeRow(
        session_id="sess-existing",
        workflow_id="mm:w1",
        agent_run_id="ar-1",
        agent_id="agent-1",
        endpoint_ref="default",
    )
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


async def test_create_session_persists_verified_workflow_id() -> None:
    # OB-§8.2: the durable row must bind to the *verified* workflow id from the
    # principal binding, not the correlation id, even when they differ.
    store, client = _FakeStore(), _FakeClient()
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    await proxy.create_session(request=req, binding=_binding())

    row = store.rows["idem-1"]
    assert row.moonmind_workflow_id == "mm:w1"  # not the "corr-1" correlation id
    assert row.moonmind_agent_run_id == "ar-1"


async def test_create_session_rejects_cross_workflow_idempotency_key() -> None:
    # A key already bound to another workflow must fail closed before any
    # provider call, rather than returning that workflow's session snapshot.
    store, client = _FakeStore(), _FakeClient()
    store.rows["idem-1"] = _FakeRow(
        session_id="sess-existing",
        workflow_id="mm:other",
        agent_run_id="ar-other",
        agent_id="agent-1",
        endpoint_ref="default",
    )
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await proxy.create_session(request=req, binding=_binding())

    assert excinfo.value.failure_class == "user_error"
    assert excinfo.value.status_code == 409
    assert not client.created_payloads
    assert not store.created_events


async def test_attach_rejects_provider_session_owned_by_another_workflow() -> None:
    store, client = _FakeStore(), _FakeClient()
    store.rows["idem-1"] = _FakeRow(workflow_id="mm:w1", agent_run_id="ar-1")
    store.rows["other"] = _FakeRow(
        session_id="sess-other", workflow_id="mm:other", agent_run_id="ar-other"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await _proxy(store, client).attach_session(
            session_id="sess-other", binding=_binding()
        )

    assert excinfo.value.code == "omnigent_bridge_session_conflict"
    assert client.get_calls == []


async def test_attach_uses_correlation_id_when_agent_run_id_is_absent() -> None:
    store, client = _FakeStore(), _FakeClient()
    store.rows["idem-1"] = _FakeRow(
        session_id="sess-9",
        workflow_id="mm:w1",
        agent_run_id="corr-1",
        agent_id="agent-1",
    )
    binding = BridgePrincipalBinding(
        workflow_id="mm:w1",
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    response = await _proxy(store, client).attach_session(
        session_id="sess-9", binding=binding
    )

    assert response["id"] == "sess-9"
    assert response["moonmind"]["agentRunId"] == "corr-1"


async def test_attach_reconciles_existing_binding_without_provider_key() -> None:
    class _StockSnapshotClient(_FakeClient):
        async def get_session(self, session_id: str) -> dict[str, Any]:
            self.get_calls.append(session_id)
            return {"id": session_id, "status": "running"}

    store, client = _FakeStore(), _StockSnapshotClient()
    store.rows["idem-1"] = _FakeRow(
        session_id="sess-9",
        workflow_id="mm:w1",
        agent_run_id="ar-1",
        agent_id="agent-1",
    )

    response = await _proxy(store, client).attach_session(
        session_id="sess-9", binding=_binding()
    )

    assert response["id"] == "sess-9"
    assert store.attached == []


async def test_attach_requires_provider_key_for_new_binding() -> None:
    class _StockSnapshotClient(_FakeClient):
        async def get_session(self, session_id: str) -> dict[str, Any]:
            self.get_calls.append(session_id)
            return {"id": session_id, "status": "running"}

    store, client = _FakeStore(), _StockSnapshotClient()
    store.rows["idem-1"] = _FakeRow(
        workflow_id="mm:w1", agent_run_id="ar-1", agent_id="agent-1"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await _proxy(store, client).attach_session(
            session_id="sess-9", binding=_binding()
        )

    assert excinfo.value.code == "omnigent_bridge_session_conflict"
    assert store.attached == []


async def test_attach_records_reconciliation_evidence() -> None:
    store, client = _FakeStore(), _FakeClient()
    store.rows["idem-1"] = _FakeRow(
        workflow_id="mm:w1", agent_run_id="ar-1", agent_id="agent-1"
    )

    response = await _proxy(store, client).attach_session(
        session_id="sess-9", binding=_binding()
    )

    assert response["id"] == "sess-9"
    assert store.attached == [("idem-1", "sess-9")]
    assert store.created_events[0]["session_id"] == "sess-9"


async def test_create_session_reuse_skips_target_resolution() -> None:
    # A durable retry whose provider session is already attached must not
    # re-resolve the target: /api/agents being down must not fail the retry.
    class _AgentsDownClient(_FakeClient):
        async def list_agents(self):  # type: ignore[override]
            raise AssertionError("target resolution must be skipped on reuse")

    store, client = _FakeStore(), _AgentsDownClient()
    store.rows["idem-1"] = _FakeRow(
        session_id="sess-existing",
        workflow_id="mm:w1",
        agent_run_id="ar-1",
        agent_id="agent-7",
        endpoint_ref="custom",
    )
    proxy = _proxy(store, client)
    req = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    response = await proxy.create_session(request=req, binding=_binding())

    assert response["moonmind"]["reused"] is True
    assert client.list_agents_calls == 0
    assert not client.created_payloads
    # session.created recovery uses the persisted agent/endpoint, not a re-resolve.
    assert store.created_events == [
        {
            "key": "idem-1",
            "session_id": "sess-existing",
            "agent_id": "agent-7",
            "endpoint_ref": "custom",
        }
    ]


async def test_get_session_owner_returns_binding() -> None:
    store, client = _FakeStore(), _FakeClient()
    store.rows["idem-1"] = _FakeRow(
        session_id="sess-existing", workflow_id="mm:w1", agent_run_id="ar-1"
    )
    proxy = _proxy(store, client)

    owner = await proxy.get_session_owner("sess-existing")
    assert owner is not None
    assert owner.workflow_id == "mm:w1"

    assert await proxy.get_session_owner("unknown") is None


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


async def test_post_event_forwards_native_control() -> None:
    client = _FakeClient()
    proxy = _proxy(_FakeStore(), client)

    response = await proxy.post_event(
        session_id="sess-1",
        event=BridgeSessionEventRequest(type="interrupt"),
    )

    assert response == {"ok": True, "type": "interrupt"}
    assert client.posted_events == [("sess-1", {"type": "interrupt"})]


async def test_harvest_session_is_bridge_local_policy() -> None:
    client = _FakeClient()
    proxy = _proxy(_FakeStore(), client)

    response = await proxy.post_event(
        session_id="sess-1",
        event=BridgeSessionEventRequest(type="harvest_session"),
    )

    assert response["status"] == "completed"
    assert response["moonmind"]["bridgeLocal"] is True
    assert response["moonmind"]["hostNativeEquivalent"] is False
    assert response["resources"]["changedFiles"][0]["path"] == "src/app.py"
    assert (
        response["resources"]["changedFiles"][0]["content"]["text"]
        == "contents:src/app.py\n"
    )
    assert response["resources"]["workspaceFiles"][0]["path"] == "README.md"
    assert (
        response["resources"]["workspaceDiffs"][0]["content"]["text"]
        == "diff -- src/app.py\n"
    )
    assert response["resources"]["sessionFiles"][0]["filename"] == "session.log"
    assert (
        response["resources"]["sessionFiles"][0]["content"]["text"]
        == "session:file-1\n"
    )
    assert client.posted_events == []


async def test_clear_session_returns_explicit_new_session_policy() -> None:
    client = _FakeClient()
    proxy = _proxy(_FakeStore(), client)

    response = await proxy.post_event(
        session_id="sess-1",
        event=BridgeSessionEventRequest(type="clear_session"),
    )

    assert response["status"] == "requires_new_session"
    assert response["moonmind"]["bridgeLocal"] is True
    assert response["moonmind"]["hostNativeEquivalent"] is False
    assert (
        response["moonmind"]["diagnostics"][0]["code"]
        == "host_native_clear_session_unavailable"
    )
    assert client.posted_events == []


async def test_resolve_elicitation_proxies_compatibility_route() -> None:
    client = _FakeClient()
    proxy = _proxy(_FakeStore(), client)

    response = await proxy.resolve_elicitation(
        session_id="sess-1",
        elicitation_id="el-1",
        payload={"answer": "yes"},
    )

    assert response == {"ok": True, "elicitationId": "el-1"}
    assert client.resolved_elicitations == [("sess-1", "el-1", {"answer": "yes"})]


async def test_non_proxy_mode_fails_fast() -> None:
    embedded = parse_bridge_config(
        {
            "compatibility": {
                "hostProtocolMode": "embedded_omnigent_compatible_server"
            },
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy-conformance",
                    "liveSmokeEvidenceRef": "artifact://omnigent/live-smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/host-auth",
                }
            },
        }
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


@pytest.mark.parametrize(
    "path", ["../secret", "src/../secret", "/etc/passwd", r"src\\secret", "%252e%252e"]
)
async def test_resource_identifier_rejects_traversal_and_double_encoding(
    path: str,
) -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        _safe_resource_identifier(path)
    assert excinfo.value.code == "omnigent_bridge_resource_path_invalid"


async def test_resource_lists_are_bounded() -> None:
    result = _bound_resource_lists({"files": list(range(300))})
    assert len(result["files"]) == 250


async def test_resource_index_total_size_is_bounded() -> None:
    client = _FakeClient()

    async def oversized(_session_id: str) -> dict[str, Any]:
        return {"items": [{"path": "x" * (4 * 1024 * 1024)}]}

    client.list_workspace_files = oversized  # type: ignore[method-assign]
    with pytest.raises(OmnigentBridgeError) as excinfo:
        await _proxy(_FakeStore(), client).get_resource(
            "workspace_files", "sess-1"
        )
    assert excinfo.value.code == "omnigent_bridge_response_too_large"


@pytest.mark.parametrize(
    ("status_code", "optional", "expected"),
    [
        (401, False, "omnigent_bridge_upstream_auth"),
        (403, False, "omnigent_bridge_upstream_auth"),
        (404, False, "omnigent_bridge_upstream_missing"),
        (429, False, "omnigent_bridge_upstream_transport"),
        (500, False, "omnigent_bridge_upstream_transport"),
        (504, False, "omnigent_bridge_upstream_timeout"),
        (404, True, "omnigent_bridge_capability_unavailable"),
        (501, True, "omnigent_bridge_capability_unavailable"),
    ],
)
async def test_facade_error_mapping_is_stable(status_code, optional, expected) -> None:
    from moonmind.omnigent.bridge_proxy import _bridge_client_error
    from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

    error = _bridge_client_error(
        OmnigentClientError(
            "upstream failure",
            status_code=status_code,
            failure_class="integration_error",
        ),
        optional=optional,
    )
    assert error.code == expected
    assert error.failure_class == "integration_error"
    assert error.status_code == (502 if status_code in {401, 403} else status_code)
