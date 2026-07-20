"""Unit tests for the embedded Omnigent-compatible host facade (MM-1164)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
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
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_POSTED,
    OmnigentBridgeSessionStore,
)
from moonmind.omnigent.embedded_host_channel import (
    EmbeddedHostChannelError,
    EmbeddedHostChannelRegistry,
    EmbeddedRunnerChannel,
    MAX_EMBEDDED_FRAME_BYTES,
    MAX_PENDING_HOST_REQUESTS,
    MAX_PENDING_RUNNER_REQUESTS,
)
from moonmind.omnigent.runner_protocol_adapter import runner_frames
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


async def _bind_active_host(store, *, host_id: str = "runner-1") -> None:
    now = datetime.now(UTC)
    async with store._session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "profile-1")
        if profile is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="profile-1",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    max_parallel_runs=1,
                    credential_generation=1,
                )
            )
            session.add(
                OmnigentOAuthHostBindingRecord(
                    binding_ref="binding-1",
                    provider_profile_id="profile-1",
                    endpoint_ref="embedded",
                    harness="codex-native",
                    credential_mount_template_json={
                        "authVolumeRef": {
                            "providerProfileId": "profile-1",
                            "runtimeId": "codex_cli",
                            "providerId": "openai",
                            "volumeRef": "profile-1-volume",
                            "credentialGeneration": 1,
                            "ownerUserId": "user-1",
                        },
                        "targetPath": "/home/app/.codex",
                        "accessMode": "read_write",
                        "runtimeUid": 1000,
                        "runtimeGid": 1000,
                    },
                )
            )
            await session.flush()
        session.add(
            OmnigentOAuthHostLeaseRecord(
                lease_id=f"lease-{host_id}",
                provider_profile_id="profile-1",
                provider_lease_id=f"provider-{host_id}",
                binding_ref="binding-1",
                credential_generation=1,
                holder_workflow_id="workflow-1",
                idempotency_key=f"host-{host_id}",
                lease_purpose="execution_omnigent",
                omnigent_host_id=host_id,
                status="ready",
                acquired_at=now,
                last_heartbeat_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()


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
async def test_live_tunnels_bound_concurrent_requests() -> None:
    registry = EmbeddedHostChannelRegistry()

    async def send_text(_text: str) -> None:
        return None

    host = registry.connect(host_id="host-1", send_text=send_text)
    host.hello = object()
    loop = asyncio.get_running_loop()
    host._pending = {
        str(index): loop.create_future() for index in range(MAX_PENDING_HOST_REQUESTS)
    }
    frame = type("Frame", (), {"request_id": "next"})()
    with pytest.raises(EmbeddedHostChannelError, match="command limit"):
        await host.request(frame)

    frames = runner_frames()
    # Exercise the production channel without depending on an upstream hello shape.
    runner = EmbeddedRunnerChannel(
        runner_id="runner-1", send_text=send_text, frames=frames, hello=object()
    )
    runner._pending = {
        str(index): {"future": loop.create_future(), "status": None, "body": []}
        for index in range(MAX_PENDING_RUNNER_REQUESTS)
    }
    with pytest.raises(EmbeddedHostChannelError, match="request limit"):
        await runner.request("GET", "/resource")


@pytest.mark.asyncio
async def test_live_tunnels_reject_oversized_frames_before_decoding() -> None:
    registry = EmbeddedHostChannelRegistry()

    async def send_text(_text: str) -> None:
        return None

    oversized = "x" * (MAX_EMBEDDED_FRAME_BYTES + 1)
    host = registry.connect(host_id="host-1", send_text=send_text)
    with pytest.raises(EmbeddedHostChannelError, match="frame exceeds size limit"):
        host.accept_host_frame(oversized)

    runner = EmbeddedRunnerChannel(
        runner_id="runner-1", send_text=send_text, frames=object(), hello=object()
    )
    with pytest.raises(EmbeddedHostChannelError, match="frame exceeds size limit"):
        runner.accept_frame(oversized)


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
    await _bind_active_host(store)
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
    await facade.disconnect_host(host_id="runner-1", auth=auth)
    async with store._session_factory() as session:
        lease = await session.get(OmnigentOAuthHostLeaseRecord, "lease-runner-1")
        assert lease.host_readiness == "disconnected"
        assert lease.disconnected_at is not None


@pytest.mark.asyncio
async def test_registration_rejects_host_without_profile_bound_lease(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
        runner_id="unbound-host",
        credential_generation=1,
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.register_host(
            request=EmbeddedHostRegisterRequest(hostId="unbound-host"), auth=auth
        )

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_registration_rejects_expired_profile_bound_lease(store) -> None:
    await _bind_active_host(store, host_id="expired-host")
    async with store._session_factory() as session:
        lease = await session.get(OmnigentOAuthHostLeaseRecord, "lease-expired-host")
        now = datetime.now(UTC)
        lease.acquired_at = now - timedelta(hours=2)
        lease.expires_at = now - timedelta(seconds=1)
        await session.commit()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
        runner_id="expired-host",
        credential_generation=1,
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.register_host(
            request=EmbeddedHostRegisterRequest(hostId="expired-host"), auth=auth
        )

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_runner_exit_without_terminal_event_creates_terminal_evidence(store) -> None:
    row = await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_profile_authorization(
        request=_request(),
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id=None,
    )
    async with store._session_factory() as session:
        from api_service.db.models import OmnigentBridgeSession

        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        persisted.omnigent_runner_id = "runner-exited"
        await session.commit()

    await store.record_embedded_runner_exit(
        runner_id="runner-exited", error="process failed token=secret-value"
    )
    recovered = await store.get_existing("idem-embedded")
    events = await store.list_events(row.bridge_session_id)

    assert recovered.status == "failed"
    assert recovered.first_message_state == "terminal"
    assert recovered.terminal_refs["cleanupState"] == "runner_exited"
    assert recovered.terminal_refs["failureClass"] == "execution_error"
    assert "secret-value" not in str(recovered.metadata_)
    assert "secret-value" not in str(recovered.terminal_refs)
    assert [event.event_type for event in events] == ["lifecycle.terminal"]
    assert events[0].metadata_["metadata"]["janitorRequired"] is True
    assert await store.cleanup_required_host_lease_refs() == {"host-lease-1"}


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
            assert row.metadata_["embedded_runner_launch"]["state"] == "launch_sent"
            assert kwargs["host_id"] == "host-1"
            assert kwargs["binding_token"]
            return "runner-1"

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels()
    )
    result = await facade.dispatch_runner(idempotency_key="idem-embedded")
    row = await store.get_existing("idem-embedded")

    assert result == {"runnerId": "runner-1", "reused": False}
    assert row.omnigent_runner_id == "runner-1"
    assert row.metadata_["embedded_runner_launch"]["state"] == "runner_tunnel_waiting"


@pytest.mark.asyncio
async def test_stop_runner_uses_durable_exact_host_binding(store) -> None:
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
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    class Channels:
        async def stop_runner(self, **kwargs):
            assert kwargs == {"host_id": "host-1", "runner_id": "runner-1"}

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels()
    )
    result = await facade.stop_runner(session_id="sess-embedded")
    row = await store.get_existing("idem-embedded")

    assert result == {"ok": True, "status": "stopped", "runnerId": "runner-1"}
    assert row.status == "canceled"
    assert "embedded_runner_exit" not in row.metadata_


@pytest.mark.asyncio
async def test_runner_exit_diagnostics_are_redacted_before_persistence(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    await store.record_embedded_runner_exit(
        runner_id="runner-1", error="launch failed token=supersecret"
    )

    row = await store.get_existing("idem-embedded")
    assert row.metadata_["embedded_runner_exit"]["error"] == (
        "launch failed token=[REDACTED]"
    )


@pytest.mark.asyncio
async def test_first_message_uses_durable_runner_and_canonical_posting_state(store) -> None:
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
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )
    await store.mark_prepared("idem-embedded", digest="digest", marker="marker")
    await store.mark_posting("idem-embedded")

    class Channels:
        calls = []

        async def post_runner_event(self, **kwargs):
            self.calls.append(kwargs)
            return {"item_id": "item-1"}

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    event = EmbeddedHostSessionEventRequest(type="message", data={"text": "hello"})
    response = await facade.post_event(session_id="sess-embedded", event=event)
    await store.mark_posted("idem-embedded", response=response)

    row = await store.get_existing("idem-embedded")
    assert channels.calls == [{
        "runner_id": "runner-1",
        "session_id": "sess-embedded",
        "payload": {"type": "message", "data": {"text": "hello"}},
    }]
    assert row.first_message_state == FIRST_MESSAGE_POSTED
    assert row.first_message_item_id == "item-1"


@pytest.mark.asyncio
async def test_runner_tunnel_reconnect_aborts_ambiguous_post_and_newest_wins() -> None:
    frames = runner_frames()
    registry = EmbeddedHostChannelRegistry()
    sent = []

    async def send_text(value):
        sent.append(value)

    hello = frames.encode_frame(frames.HelloFrame(
        runner_version="1.0.0", frame_protocol_version=1,
        harnesses=["codex-native"], envs=["local"],
    ))
    old = registry.connect_runner(
        runner_id="runner-1", send_text=send_text, hello_text=hello
    )
    task = asyncio.create_task(
        old.request("POST", "/v1/sessions/sess-1/events", {"type": "message"})
    )
    await asyncio.sleep(0)

    new = registry.connect_runner(
        runner_id="runner-1", send_text=send_text, hello_text=hello
    )
    with pytest.raises(EmbeddedHostChannelError, match="disconnected"):
        await task

    registry.disconnect_runner(old)
    assert registry._runners["runner-1"] is new


def test_runner_binding_auth_uses_boundary_resolved_secret() -> None:
    registry = EmbeddedHostChannelRegistry()
    token = "runner-binding-token"
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({token})
    ).runner_id_for_binding_token(token)
    headers = Headers({"X-Omnigent-Runner-Tunnel-Token": token})

    assert registry.authenticate_runner(
        runner_id=runner_id, headers=headers, binding_token=token
    ) == runner_id
    registry.revoke_runner_binding(runner_id)


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
async def test_embedded_lifecycle_rejects_invalid_and_terminal_transitions(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.begin_embedded_runner_launch("idem-embedded", host_id="host-1")

    with pytest.raises(OmnigentIdempotencyError, match="invalid.*launch_reserved -> running"):
        await store.transition_embedded_runner("idem-embedded", state="running")
    first = await store.transition_embedded_runner(
        "idem-embedded", state="launch_sent"
    )
    duplicate = await store.transition_embedded_runner(
        "idem-embedded", state="launch_sent"
    )
    assert len(first.metadata_["embedded_runner_launch"]["transitions"]) == len(
        duplicate.metadata_["embedded_runner_launch"]["transitions"]
    )
    await store.transition_embedded_runner("idem-embedded", state="stale")
    with pytest.raises(OmnigentIdempotencyError, match="terminal"):
        await store.transition_embedded_runner("idem-embedded", state="running")


@pytest.mark.asyncio
async def test_first_message_retry_fails_closed_before_duplicate_side_effect(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )
    await store.mark_prepared("idem-embedded", digest="digest", marker="marker")
    await store.mark_posting("idem-embedded")

    class Channels:
        calls = 0

        async def post_runner_event(self, **_kwargs):
            self.calls += 1
            raise TimeoutError("response lost")

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    event = EmbeddedHostSessionEventRequest(type="message", data={"text": "hello"})
    with pytest.raises(OmnigentBridgeError, match="response lost"):
        await facade.post_event(session_id="sess-embedded", event=event)
    with pytest.raises(OmnigentBridgeError, match="outcome is ambiguous"):
        await facade.post_event(session_id="sess-embedded", event=event)
    assert channels.calls == 1


@pytest.mark.asyncio
async def test_dispatch_marks_uncertain_launch_stale_without_retry(store) -> None:
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
    assert row.metadata_["embedded_runner_launch"]["state"] == "stale"
    with pytest.raises(OmnigentIdempotencyError, match="durable reconciliation"):
        await store.begin_embedded_runner_launch("idem-embedded", host_id="host-1")


@pytest.mark.asyncio
async def test_embedded_resources_and_elicitation_use_exact_bound_runner(store) -> None:
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
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    class Channels:
        calls = []

        async def request_runner(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["expect_json"]:
                return {"items": [{"path": "src/main.py"}]}
            return b"content"

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )

    index = await facade.get_resource("changed_files", "sess-embedded")
    content = await facade.get_resource(
        "workspace_file", "sess-embedded", "src/main.py"
    )
    resolved = await facade.resolve_elicitation(
        session_id="sess-embedded", elicitation_id="approval-1",
        payload={"decision": "approve"},
    )

    assert index == {"items": [{"path": "src/main.py"}]}
    assert content == b"content"
    assert resolved == {"items": [{"path": "src/main.py"}]}
    assert {call["runner_id"] for call in channels.calls} == {"runner-1"}
    assert channels.calls[1]["path"].endswith("/filesystem/src/main.py")
    assert channels.calls[2]["path"].endswith("/approval-1/resolve")


@pytest.mark.asyncio
async def test_embedded_resource_rejects_traversal_before_runner_request(store) -> None:
    class Store:
        async def get_session_by_provider_session_id(self, _session_id):
            return type("Row", (), {
                "omnigent_host_id": "host-1", "omnigent_runner_id": "runner-1"
            })()

    class Channels:
        async def request_runner(self, **_kwargs):
            raise AssertionError("unsafe path reached runner")

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=Store(), config=_embedded_config(), host_channels=Channels()
    )
    with pytest.raises(OmnigentBridgeError, match="traversal-unsafe"):
        await facade.get_resource("workspace_file", "sess-embedded", "../secret")
