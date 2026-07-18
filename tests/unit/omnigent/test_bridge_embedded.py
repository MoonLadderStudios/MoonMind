"""Unit tests for the embedded Omnigent-compatible host facade (MM-1164)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from api_service.api.routers import omnigent_bridge

from api_service.db.models import Base
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostHeartbeatRequest,
    EmbeddedHostRegisterRequest,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.host_auth_adapter import (
    HostCredentialGeneration,
    OmnigentHostAuthAdapter,
    UpstreamHostAuthError,
)
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeError,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/embedded.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


def _embedded_config():
    return parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy-conformance",
                    "liveSmokeEvidenceRef": "artifact://omnigent/live-smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/host-auth",
                }
            },
        }
    )


def _request():
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf-embedded",
        idempotencyKey="idem-embedded",
    )


def test_embedded_host_auth_delegates_upstream_runner_tunnel_token() -> None:
    context = verify_embedded_host_auth(
        headers={"X-Omnigent-Runner-Tunnel-Token": "runner-token"},
        config=_embedded_config(),
        configured_token="runner-token",
        runner_id="runner-1",
    )

    assert context.auth_mode == "upstream_runner_tunnel"
    assert context.protocol_profile == "omnigent.runner_tunnel.b95e41ec"
    assert context.runner_id == "runner-1"


def test_embedded_host_auth_rejects_missing_or_wrong_token() -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        verify_embedded_host_auth(
            headers={"X-Omnigent-Runner-Tunnel-Token": "wrong"},
            config=_embedded_config(),
            configured_token="runner-token",
            runner_id="runner-1",
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.failure_class == "user_error"


def test_embedded_host_auth_rejects_user_bearer_and_cookie_domains() -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        verify_embedded_host_auth(
            headers={
                "Authorization": "Bearer runner-token",
                "Cookie": "moonmind_session=runner-token",
                "X-Execution-Principal": "runner-token",
            },
            config=_embedded_config(),
            configured_token="runner-token",
            runner_id="runner-1",
        )

    assert excinfo.value.status_code == 401


def test_pinned_source_verifier_executes_without_importable_package() -> None:
    adapter = OmnigentHostAuthAdapter(
        credentials=(
            HostCredentialGeneration(
                secret_ref="env://OMNIGENT_HOST_RUNNER_TOKEN",
                generation=1,
                token="runner-token",
            ),
        )
    )

    identity = adapter.verify(
        Headers(raw=[(b"x-omnigent-runner-tunnel-token", b"runner-token")]),
        runner_id="runner-1",
    )

    assert identity.runner_id == "runner-1"


def test_padded_token_uses_upstream_normalization_and_selects_generation() -> None:
    adapter = OmnigentHostAuthAdapter(
        credentials=(
            HostCredentialGeneration(
                secret_ref="db://omnigent-host-current",
                generation=7,
                token="runner-token",
            ),
        )
    )

    identity = adapter.verify(
        {"X-Omnigent-Runner-Tunnel-Token": "  runner-token\t"},
        runner_id="runner-1",
    )

    assert identity.runner_id == "runner-1"
    assert identity.credential_generation == 7


def test_embedded_host_auth_rejects_duplicate_tunnel_token_headers() -> None:
    headers = Headers(
        raw=[
            (b"x-omnigent-runner-tunnel-token", b"wrong"),
            (b"x-omnigent-runner-tunnel-token", b"runner-token"),
        ]
    )

    with pytest.raises(UpstreamHostAuthError, match="exactly once"):
        OmnigentHostAuthAdapter(
            credentials=(
                HostCredentialGeneration(
                    secret_ref="env://OMNIGENT_HOST_RUNNER_TOKEN",
                    generation=1,
                    token="runner-token",
                ),
            )
        ).verify(headers, runner_id="runner-1")


def test_host_auth_rotation_selects_generation_without_exposing_token() -> None:
    adapter = OmnigentHostAuthAdapter(
        credentials=(
            HostCredentialGeneration(
                secret_ref="db://omnigent-host-current",
                generation=3,
                token="current-token",
            ),
            HostCredentialGeneration(
                secret_ref="db://omnigent-host-previous",
                generation=2,
                token="previous-token",
            ),
        )
    )

    identity = adapter.verify(
        {"X-Omnigent-Runner-Tunnel-Token": "previous-token"},
        runner_id="runner-1",
    )

    assert identity.credential_generation == 2
    assert "previous-token" not in repr(identity)


def test_host_auth_revocation_prevents_new_connections() -> None:
    adapter = OmnigentHostAuthAdapter(
        credentials=(
            HostCredentialGeneration(
                secret_ref="db://omnigent-host-revoked",
                generation=1,
                token="revoked-token",
                revoked=True,
            ),
            HostCredentialGeneration(
                secret_ref="db://omnigent-host-current",
                generation=2,
                token="current-token",
            ),
        )
    )

    with pytest.raises(UpstreamHostAuthError) as excinfo:
        adapter.verify(
            {"X-Omnigent-Runner-Tunnel-Token": "revoked-token"},
            runner_id="runner-1",
        )

    assert excinfo.value.code == "host_credential_rejected"
    assert excinfo.value.retryable is False
    assert "revoked-token" not in str(excinfo.value)


class _TunnelWebSocket:
    def __init__(self, frames, headers=None):
        self.frames = iter(frames)
        self.headers = Headers(headers or {})
        self.accepted = False
        self.closed = []
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))

    async def receive_text(self):
        try:
            return next(self.frames)
        except StopIteration as exc:
            raise WebSocketDisconnect() from exc

    async def send_text(self, value):
        self.sent.append(value)


@pytest.mark.asyncio
async def test_stock_runner_tunnel_hello_keepalive_and_reconnect(monkeypatch) -> None:
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="runner-1",
        credential_generation=2,
    )

    async def authenticate(**_kwargs):
        return auth

    async def authorize(self, **_kwargs):
        return object()

    monkeypatch.setattr(omnigent_bridge, "_embedded_websocket_auth_context", authenticate)
    monkeypatch.setattr(omnigent_bridge, "_require_bridge_enabled", _embedded_config)
    monkeypatch.setattr(
        OmnigentEmbeddedHostProtocolFacade, "authorize_host", authorize
    )
    for _ in range(2):
        websocket = _TunnelWebSocket([
            '{"kind":"hello","runner_version":"0.4.0","frame_protocol_version":1}',
            '{"kind":"ping","ts":42}',
        ])
        await omnigent_bridge.embedded_omnigent_runner_tunnel(websocket, "runner-1")
        assert websocket.accepted is True
        assert websocket.closed == []
        assert websocket.sent == ['{"kind": "pong", "ts": 42}']


@pytest.mark.asyncio
async def test_stock_runner_tunnel_rejection_is_stable_and_secret_free(monkeypatch) -> None:
    secret = "credential-sentinel-never-emit"

    async def reject(**_kwargs):
        raise OmnigentBridgeError(
            secret, failure_class="user_error", status_code=401,
            code="host_credential_rejected",
        )

    monkeypatch.setattr(omnigent_bridge, "_embedded_websocket_auth_context", reject)
    monkeypatch.setattr(omnigent_bridge, "_require_bridge_enabled", _embedded_config)
    websocket = _TunnelWebSocket([])
    await omnigent_bridge.embedded_omnigent_runner_tunnel(websocket, "runner-1")
    assert websocket.accepted is True
    assert websocket.closed == [(4004, "runner tunnel authentication failed")]
    assert secret not in repr(websocket.__dict__)


def test_embedded_auth_context_uses_service_side_generation() -> None:
    context = verify_embedded_host_auth(
        headers={"X-Omnigent-Runner-Tunnel-Token": "runner-token"},
        config=_embedded_config(),
        configured_token="runner-token",
        credential_generation=7,
        credential_secret_ref="db://omnigent-host-token",
        runner_id="runner-1",
    )

    assert context.credential_generation == 7


@pytest.mark.asyncio
async def test_registration_rejects_runner_identity_substitution(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="runner-authenticated",
        credential_generation=1,
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.register_host(
            request=EmbeddedHostRegisterRequest(runnerId="runner-attacker"),
            auth=auth,
        )

    assert excinfo.value.status_code == 403

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.register_host(
            request=EmbeddedHostRegisterRequest(hostId="host-attacker"),
            auth=auth,
        )

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_register_and_heartbeat_return_embedded_bridge_shape(store) -> None:
    async def authorized(_host_id):
        return [SimpleNamespace(
            omnigent_host_id="runner-1", credential_generation=1,
            host_binding_ref="binding-1", host_lease_ref="host-lease-1",
            metadata_={},
        )]

    store.list_sessions_for_embedded_host = authorized
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="runner-1",
        credential_generation=1,
    )

    registered = await facade.register_host(
        request=EmbeddedHostRegisterRequest(hostId="runner-1", runnerId="runner-1"),
        auth=auth,
    )
    heartbeat = await facade.heartbeat(
        host_id="runner-1",
        request=EmbeddedHostHeartbeatRequest(status="running"),
        auth=auth,
    )

    assert registered["status"] == "registered"
    assert heartbeat["moonmind"]["hostProtocolMode"] == HOST_PROTOCOL_MODE_EMBEDDED


@pytest.mark.asyncio
async def test_pinned_auth_and_durable_lease_authorize_without_boundary_mocks(store) -> None:
    await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="runner-1",
    )
    auth = verify_embedded_host_auth(
        headers={"X-Omnigent-Runner-Tunnel-Token": "runner-token"},
        config=_embedded_config(),
        configured_token="runner-token",
        runner_id="runner-1",
    )
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )

    response = await facade.heartbeat(
        host_id="runner-1",
        request=EmbeddedHostHeartbeatRequest(),
        auth=auth,
    )

    assert response["hostId"] == "runner-1"


@pytest.mark.asyncio
async def test_host_level_actions_require_durable_binding_and_current_generation(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_embedded_config())
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="runner-1",
        credential_generation=1,
    )
    with pytest.raises(OmnigentBridgeError) as missing:
        await facade.heartbeat(
            host_id="runner-1", request=EmbeddedHostHeartbeatRequest(), auth=auth
        )
    assert missing.value.code == "host_binding_mismatch"

    async def stale_binding(_host_id):
        return [SimpleNamespace(
            omnigent_host_id="runner-1", credential_generation=2,
            host_binding_ref="binding-1", host_lease_ref="lease-1", metadata_={},
        )]

    store.list_sessions_for_embedded_host = stale_binding
    with pytest.raises(OmnigentBridgeError) as stale:
        await facade.heartbeat(
            host_id="runner-1", request=EmbeddedHostHeartbeatRequest(), auth=auth
        )
    assert stale.value.code == "host_credential_stale"


@pytest.mark.asyncio
async def test_embedded_session_events_append_to_same_bridge_event_model(store) -> None:
    row = await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )

    response = await facade.ingest_session_event(
        host_id="host-1",
        session_id="sess-embedded",
        request=EmbeddedHostSessionEventRequest(
            type="response.delta",
            data={"text": "hello"},
        ),
        auth=EmbeddedHostAuthContext(
            auth_mode="upstream_runner_tunnel",
            protocol_profile="omnigent.runner_tunnel.b95e41ec",
            runner_id="host-1",
            credential_generation=1,
        ),
    )
    events = await store.list_events(row.bridge_session_id)

    assert response["accepted"] == 1
    assert response["bridgeSessionId"] == row.bridge_session_id
    assert len(events) == 1
    assert events[0].event_type == "response.delta"
    assert events[0].normalized_status == "running"
    assert events[0].metadata_["moonmind"]["source"] == "omnigent_stream"


@pytest.mark.asyncio
async def test_embedded_create_session_creates_local_bridge_session(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )

    response = await facade.create_session(
        request=BridgeSessionCreateRequest(
            agent_id="agent-1",
            host_type="external",
            host_id="host-1",
            workspace="/workspace/repo",
            labels={
                "moonmind.workflow_id": "mm:wf-embedded",
                "moonmind.idempotency_key": "idem-create",
            },
        ),
        binding=BridgePrincipalBinding(
            workflow_id="mm:wf-embedded",
            correlation_id="corr-create",
            idempotency_key="idem-create",
            agent_run_id="run-embedded",
        ),
    )

    assert response["id"].startswith("emb_brs_")
    assert response["moonmind"]["bridgeLocal"] is True
    assert response["moonmind"]["reused"] is False
    row = await store.get_session_by_provider_session_id(response["id"])
    assert row is not None
    assert row.moonmind_workflow_id == "mm:wf-embedded"


@pytest.mark.asyncio
async def test_embedded_session_events_preserve_full_payload_and_errors(
    store,
) -> None:
    row = await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="host-1",
        credential_generation=1,
    )

    await facade.ingest_session_event(
        host_id="host-1",
        session_id="sess-embedded",
        request=EmbeddedHostSessionEventRequest(
            type="response.delta",
            data={"text": "hello", "nested": {"answer": 42}},
        ),
        auth=auth,
    )
    events = await store.list_events(row.bridge_session_id)
    assert events[0].metadata_["embeddedRawEvent"]["data"]["nested"] == {"answer": 42}
    assert (
        events[0].metadata_["embeddedNormalizedEvent"]["eventType"]
        == "response.delta"
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.ingest_session_event(
            host_id="host-1",
            session_id="sess-embedded",
            request=EmbeddedHostSessionEventRequest(
                type="response.delta",
                data={"response": {"status": "mystery"}},
            ),
            auth=auth,
        )
    assert excinfo.value.status_code == 502
    assert excinfo.value.failure_class == "integration_error"


@pytest.mark.asyncio
async def test_embedded_session_event_rejects_stale_generation_and_redacts(store) -> None:
    row = await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded", provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1", credential_generation=2,
        host_binding_ref="binding-1", host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_embedded_config())
    stale = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel", protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="host-1", credential_generation=1,
    )
    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.ingest_session_event(
            host_id="host-1", session_id="sess-embedded",
            request=EmbeddedHostSessionEventRequest(type="response.delta", data={"text": "ignored"}),
            auth=stale,
        )
    assert excinfo.value.code == "host_credential_stale"

    current = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel", protocol_profile="omnigent.runner_tunnel.b95e41ec",
        runner_id="host-1", credential_generation=2,
    )
    sentinel = "sentinel-host-credential"
    await facade.ingest_session_event(
        host_id="host-1", session_id="sess-embedded",
        request=EmbeddedHostSessionEventRequest(
            type="response.delta", data={"authorization": f"Bearer {sentinel}", "cookie": sentinel}
        ), auth=current,
    )
    events = await store.list_events(row.bridge_session_id)
    assert sentinel not in repr(events)
