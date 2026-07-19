"""Unit tests for the embedded Omnigent-compatible host facade (MM-1164)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

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
    MAX_EMBEDDED_CAPABILITIES,
    MAX_EMBEDDED_CAPABILITY_BYTES,
    MAX_EMBEDDED_EVENT_BYTES,
    MAX_EMBEDDED_EVENT_ENTRIES,
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.host_auth_adapter import (
    OmnigentHostAuthAdapter,
    UpstreamHostAuthError,
)
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeError,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.embedded_host_channel import EmbeddedHostChannelError
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
    )

    assert context.auth_mode == "upstream_runner_tunnel"
    assert context.protocol_profile == "omnigent.runner_tunnel.7da32637"
    assert context.runner_id.startswith("runner_token_")


def test_embedded_host_auth_rejects_missing_or_wrong_token() -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        verify_embedded_host_auth(
            headers={"X-Omnigent-Runner-Tunnel-Token": "wrong"},
            config=_embedded_config(),
            configured_token="runner-token",
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
        )

    assert excinfo.value.status_code == 401


def test_pinned_source_verifier_executes_without_importable_package() -> None:
    adapter = OmnigentHostAuthAdapter(allowed_tokens=frozenset({"runner-token"}))

    identity = adapter.verify(
        Headers(raw=[(b"x-omnigent-runner-tunnel-token", b"runner-token")])
    )

    assert identity.runner_id.startswith("runner_token_")


def test_embedded_host_auth_rejects_duplicate_tunnel_token_headers() -> None:
    headers = Headers(
        raw=[
            (b"x-omnigent-runner-tunnel-token", b"wrong"),
            (b"x-omnigent-runner-tunnel-token", b"runner-token"),
        ]
    )

    with pytest.raises(UpstreamHostAuthError, match="exactly once"):
        OmnigentHostAuthAdapter(
            allowed_tokens=frozenset({"runner-token"})
        ).verify(headers)


def test_embedded_host_payloads_enforce_protocol_bounds() -> None:
    with pytest.raises(ValidationError, match="entry limit"):
        EmbeddedHostRegisterRequest(
            capabilities={str(index): True for index in range(MAX_EMBEDDED_CAPABILITIES + 1)}
        )

    with pytest.raises(ValidationError, match="byte limit"):
        EmbeddedHostHeartbeatRequest(
            capabilities={"oversized": "x" * MAX_EMBEDDED_CAPABILITY_BYTES}
        )

    with pytest.raises(ValidationError, match="entry limit"):
        EmbeddedHostSessionEventRequest(
            type="response.delta",
            data={str(index): True for index in range(MAX_EMBEDDED_EVENT_ENTRIES + 1)},
        )

    with pytest.raises(ValidationError, match="byte limit"):
        EmbeddedHostSessionEventRequest(
            type="response.delta",
            data={"text": "x" * MAX_EMBEDDED_EVENT_BYTES},
        )


@pytest.mark.asyncio
async def test_registration_rejects_runner_identity_substitution(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
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
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
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
async def test_embedded_session_events_append_to_same_bridge_event_model(store) -> None:
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id="agent-1",
        agent_name="Codex",
        target_metadata={"hostType": "external", "workspace": "/workspace/repo"},
        workflow_id="mm:wf-embedded",
        agent_run_id="run-embedded",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    async with store._session_factory() as session:
        from api_service.db.models import OmnigentBridgeSession

        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        persisted.omnigent_host_id = "host-1"
        await session.commit()
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
            protocol_profile="omnigent.runner_tunnel.7da32637",
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

    await store.bind_profile_authorization(
        request=AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="mm:wf-embedded",
            idempotencyKey="idem-create",
        ),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
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
    assert row.omnigent_host_id == "host-1"


@pytest.mark.asyncio
async def test_embedded_create_rejects_caller_host_bypass(store) -> None:
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-bypass",
        idempotencyKey="idem-bypass",
    )
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-assigned",
    )
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_embedded_config())

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.create_session(
            request=BridgeSessionCreateRequest(
                host_type="external",
                host_id="host-attacker",
                workspace="/workspace/repo",
            ),
            binding=BridgePrincipalBinding(
                workflow_id="corr-bypass",
                correlation_id="corr-bypass",
                idempotency_key="idem-bypass",
            ),
        )

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_embedded_event_rejects_cross_host_binding(store) -> None:
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.attach_session("idem-embedded", "sess-cross-host")
    async with store._session_factory() as session:
        from api_service.db.models import OmnigentBridgeSession

        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        persisted.omnigent_host_id = "host-assigned"
        await session.commit()
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_embedded_config())

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.ingest_session_event(
            host_id="host-attacker",
            session_id="sess-cross-host",
            request=EmbeddedHostSessionEventRequest(type="response.delta"),
            auth=EmbeddedHostAuthContext(
                auth_mode="upstream_runner_tunnel",
                protocol_profile="omnigent.runner_tunnel.b95e41ec",
                runner_id="host-attacker",
                credential_generation=1,
            ),
        )

    assert excinfo.value.status_code == 403
    assert row.moonmind_workflow_id == "mm:wf-embedded"


@pytest.mark.asyncio
async def test_embedded_session_events_preserve_full_payload_and_errors(
    store,
) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id="agent-1",
        agent_name="Codex",
        target_metadata={"hostType": "external", "workspace": "/workspace/repo"},
        workflow_id="mm:wf-embedded",
        agent_run_id="run-embedded",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
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
async def test_dispatch_persists_launch_intent_before_host_side_effect(store) -> None:
    await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id="agent-1",
        agent_name="Codex",
        target_metadata={"hostType": "external", "workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")

    class Channels:
        async def launch_runner(self, **kwargs):
            row = await store.get_existing("idem-embedded")
            assert row.metadata_["embedded_runner_launch"]["state"] == "pending"
            assert kwargs["host_id"] == "host-1"
            return "runner-1"

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels()
    )
    result = await facade.dispatch_runner(idempotency_key="idem-embedded")
    row = await store.get_existing("idem-embedded")

    assert result == {"runnerId": "runner-1", "reused": False}
    assert row.omnigent_runner_id == "runner-1"
    assert row.metadata_["embedded_runner_launch"]["state"] == "launched"


@pytest.mark.asyncio
async def test_dispatch_does_not_repeat_ambiguous_pending_launch(store) -> None:
    await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id=None,
        agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.begin_embedded_runner_launch("idem-embedded", host_id="host-1")

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )
    with pytest.raises(OmnigentBridgeError, match="durable reconciliation") as excinfo:
        await facade.dispatch_runner(idempotency_key="idem-embedded")

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_dispatch_marks_rejected_launch_failed_for_retry(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")

    class Channels:
        async def launch_runner(self, **_kwargs):
            raise EmbeddedHostChannelError("host rejected runner launch")

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels()
    )
    with pytest.raises(OmnigentBridgeError, match="rejected"):
        await facade.dispatch_runner(idempotency_key="idem-embedded")

    row = await store.get_existing("idem-embedded")
    assert row.metadata_["embedded_runner_launch"]["state"] == "failed"
    await store.begin_embedded_runner_launch("idem-embedded", host_id="host-1")
