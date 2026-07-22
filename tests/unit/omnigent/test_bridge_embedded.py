"""Unit tests for the embedded Omnigent-compatible host facade (MM-1164)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
    BridgeSessionEventRequest,
    OmnigentBridgeError,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_POSTED,
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
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
async def test_heartbeat_without_status_preserves_ready_host(store) -> None:
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

    await facade.heartbeat(
        host_id="runner-1",
        request=EmbeddedHostHeartbeatRequest(),
        auth=auth,
    )

    hosts = await store.list_embedded_host_readiness()
    assert hosts[0]["status"] == "ready"
    assert hosts[0]["ready"] is True


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
async def test_heartbeat_rejects_lease_after_provider_profile_rotation(store) -> None:
    """MoonLadderStudios/MoonMind#3423: stale generation cannot keep a lease."""

    await _bind_active_host(store, host_id="stale-host")
    async with store._session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "profile-1")
        profile.credential_generation = 2
        await session.commit()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
        runner_id="stale-host",
        credential_generation=1,
        credential_profile_id="managed-host-auth",
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.heartbeat(
            host_id="stale-host",
            request=EmbeddedHostHeartbeatRequest(status="ready"),
            auth=auth,
        )

    assert excinfo.value.status_code == 403
    assert "credential generation" in str(excinfo.value)


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
async def test_terminal_cleanup_failure_is_redacted_and_idempotent(store) -> None:
    row = await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
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

    for _ in range(2):
        await store.record_terminal_cleanup(
            host_lease_ref="host-lease-1",
            completed=False,
            code="RuntimeError",
            summary="cleanup failed token=secret-value",
        )

    recovered = await store.get_existing("idem-embedded")
    events = await store.list_events(row.bridge_session_id)
    cleanup_events = [
        event for event in events
        if (event.metadata_ or {}).get("eventIdentity", "").startswith(
            "embedded-control:terminal-cleanup:host-lease-1:"
        )
    ]
    assert recovered.terminal_refs["cleanupState"] == "failed"
    assert recovered.terminal_refs["leaseReleaseState"] == "failed"
    assert recovered.terminal_refs["cleanupFailureCode"] == "RuntimeError"
    assert await store.cleanup_required_host_lease_refs() == {"host-lease-1"}
    assert len(cleanup_events) == 2
    assert cleanup_events[-1].metadata_["metadata"]["controlOutcome"] == "failed"
    assert "secret-value" not in str(cleanup_events)


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
    assert response["capabilities"]["sendMessage"] is True
    assert response["capabilities"]["interrupt"] is False
    row = await store.get_session_by_provider_session_id(response["id"])
    assert row is not None
    assert row.omnigent_host_id == "host-1"


@pytest.mark.asyncio
async def test_embedded_control_rejects_unpersistable_idempotency_key(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={},
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.harvest_session(
            "sess-embedded", payload={"idempotencyKey": "x" * 221}
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "omnigent_embedded_control_key_too_long"


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
async def test_embedded_session_events_use_artifact_journals_and_compact_index(
    store, tmp_path,
) -> None:
    """MoonLadderStudios/MoonMind#3424 keeps full events out of DB metadata."""
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
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
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
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
    persisted = await store.get_bridge_session(row.bridge_session_id)
    assert "embeddedRawEvent" not in events[0].metadata_
    assert "embeddedNormalizedEvent" not in events[0].metadata_
    assert events[0].artifact_ref == persisted.normalized_events_ref
    raw_journal = await facade._artifact_gateway.read_text(persisted.raw_events_ref)
    normalized_journal = await facade._artifact_gateway.read_text(
        persisted.normalized_events_ref
    )
    assert '"nested":{"answer":42}' in raw_journal
    assert '"eventType":"response.delta"' in normalized_journal

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
            return OmnigentHostAuthAdapter(
                allowed_tokens=frozenset({kwargs["binding_token"]})
            ).runner_id_for_binding_token(kwargs["binding_token"])

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels(),
        runner_binding_root_secret="root-secret",
    )
    result = await facade.dispatch_runner(idempotency_key="idem-embedded")
    row = await store.get_existing("idem-embedded")

    assert result["reused"] is False
    assert row.omnigent_runner_id == result["runnerId"]
    assert row.metadata_["embedded_runner_launch"]["state"] == "launched"
    lifecycle = row.metadata_["embedded_runner_lifecycle"]
    assert lifecycle["version"] == 1
    assert lifecycle["state"] == "runner_tunnel_waiting"
    assert [item["state"] for item in lifecycle["timeline"]] == [
        "launch_reserved",
        "launch_sent",
        "launch_acknowledged",
        "runner_identity_bound",
        "runner_tunnel_waiting",
    ]
    assert lifecycle["providerLeaseId"] == "provider-lease-1"
    assert "binding_token" not in json.dumps(lifecycle).lower()


@pytest.mark.asyncio
async def test_dispatch_rejects_stale_durable_runner_instead_of_claiming_reuse(
    store,
) -> None:
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
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    class Channels:
        def is_runner_ready(self, runner_id):
            assert runner_id == "runner-1"
            return False

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels()
    )
    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.dispatch_runner(idempotency_key="idem-embedded")

    assert excinfo.value.code == "embedded_runner_stale"
    row = await store.get_existing("idem-embedded")
    assert row.metadata_["embedded_runner_lifecycle"]["state"] == "stale"


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
        calls = 0

        async def stop_runner(self, **kwargs):
            assert kwargs == {"host_id": "host-1", "runner_id": "runner-1"}
            self.calls += 1

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    control_payload = {
        "idempotencyKey": "stop-request-1",
        "expectedWorkflowId": row.moonmind_workflow_id,
        "expectedRunId": row.moonmind_run_id,
        "expectedStepExecutionId": row.step_execution_id,
        "expectedAgentRunId": row.moonmind_agent_run_id,
        "expectedBridgeSessionId": row.bridge_session_id,
        "expectedSessionId": "sess-embedded",
        "expectedHostId": "host-1",
        "expectedRunnerId": "runner-1",
        "expectedTurnState": row.first_message_state,
        "expectedTerminalState": row.status,
    }
    with pytest.raises(OmnigentBridgeError) as stale_error:
        await facade.stop_runner(
            session_id="sess-embedded",
            payload={
                **control_payload,
                "idempotencyKey": "stop-stale-1",
                "expectedRunnerId": "stale-runner",
            },
            actor="user-42",
        )
    assert stale_error.value.status_code == 409
    assert stale_error.value.code == "omnigent_embedded_control_state_mismatch"
    result = await facade.stop_runner(
        session_id="sess-embedded",
        payload=control_payload,
        actor="user-42",
    )
    duplicate = await facade.stop_runner(
        session_id="sess-embedded", payload=control_payload, actor="user-42"
    )
    row = await store.get_existing("idem-embedded")
    cleanup_payload = {
        **control_payload,
        "idempotencyKey": "cleanup-request-1",
        "expectedTurnState": row.first_message_state,
        "expectedTerminalState": row.status,
    }
    cleanup_result = await facade.cleanup_session(
        "sess-embedded", payload=cleanup_payload, actor="user-42"
    )
    cleanup_duplicate = await facade.cleanup_session(
        "sess-embedded", payload=cleanup_payload, actor="user-42"
    )
    row = await store.get_existing("idem-embedded")
    events = await store.list_events(row.bridge_session_id)

    assert result == {"ok": True, "status": "stopped", "runnerId": "runner-1"}
    assert duplicate == {
        "ok": True, "status": "completed", "idempotencyKey": "stop-request-1",
        "reconciled": True, "runnerId": "runner-1",
    }
    assert cleanup_result == {
        "ok": True, "status": "stopped", "runnerId": "runner-1"
    }
    assert cleanup_duplicate == {
        "ok": True, "status": "completed",
        "idempotencyKey": "cleanup-request-1", "reconciled": True,
        "runnerId": "runner-1",
    }
    assert channels.calls == 2
    assert row.status == "canceled"
    assert row.metadata_["embedded_runner_lifecycle"]["state"] == "stopped"
    assert "embedded_runner_exit" not in row.metadata_
    control_metadata = [
        event.metadata_["metadata"]
        for event in events
        if (event.metadata_ or {}).get("eventIdentity", "").startswith(
            "embedded-control:stop-request-1:"
        )
    ]
    assert {item["actor"] for item in control_metadata} == {"user-42"}
    assert {item["controlIdempotencyKey"] for item in control_metadata} == {
        "stop-request-1"
    }
    cleanup_metadata = [
        event.metadata_["metadata"]
        for event in events
        if (event.metadata_ or {}).get("eventIdentity", "").startswith(
            "embedded-control:cleanup-request-1:"
        )
    ]
    assert {item["controlType"] for item in cleanup_metadata} == {
        "terminal_cleanup"
    }


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
    assert row.metadata_["embedded_runner_lifecycle"]["state"] == "running"


@pytest.mark.asyncio
async def test_embedded_lifecycle_rejects_invalid_jump_and_accepts_duplicate(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.begin_embedded_runner_launch("idem-embedded", host_id="host-1")
    await store.mark_embedded_runner_state(
        "idem-embedded", state="launch_reserved", code="launch_reserved"
    )

    with pytest.raises(OmnigentIdempotencyError, match="invalid embedded runner lifecycle transition"):
        await store.mark_embedded_runner_state(
            "idem-embedded", state="running", code="impossible_jump"
        )

    row = await store.get_existing("idem-embedded")
    timeline = row.metadata_["embedded_runner_lifecycle"]["timeline"]
    assert [entry["state"] for entry in timeline] == ["launch_reserved"]


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


def test_runner_binding_auth_is_reconstructable_after_registry_restart() -> None:
    registry = EmbeddedHostChannelRegistry()
    token = "runner-binding-token"
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({token})
    ).runner_id_for_binding_token(token)
    headers = Headers({"X-Omnigent-Runner-Tunnel-Token": token})

    assert registry.authenticate_runner(
        runner_id=runner_id, headers=headers, binding_token=token
    ) == runner_id
    restarted = EmbeddedHostChannelRegistry()
    assert restarted.authenticate_runner(
        runner_id=runner_id, headers=headers, binding_token=token
    ) == runner_id


@pytest.mark.asyncio
async def test_dispatch_replays_pending_launch_with_same_generation_identity(store) -> None:
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
    from moonmind.omnigent.embedded_host_channel import derive_runner_binding_token
    token = derive_runner_binding_token(
        "root-secret", host_id="host-1", session_id="sess-embedded", generation=1000001
    )
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({token})
    ).runner_id_for_binding_token(token)
    await store.begin_embedded_runner_launch(
        "idem-embedded", host_id="host-1", runner_id=runner_id,
        generation=1000001, credential_generation=1, launch_generation=1,
    )

    class Channels:
        async def launch_runner(self, **kwargs):
            assert kwargs["binding_token"] == token
            return runner_id

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels(),
        runner_binding_root_secret="root-secret",
    )
    result = await facade.dispatch_runner(idempotency_key="idem-embedded")
    assert result == {"runnerId": runner_id, "reused": False}


@pytest.mark.asyncio
async def test_reserved_launch_rejects_stale_generation_and_cross_session_identity(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=2, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.begin_embedded_runner_launch(
        "idem-embedded", host_id="host-1", runner_id="runner-current", generation=2
    )

    with pytest.raises(OmnigentIdempotencyError, match="generation"):
        await store.begin_embedded_runner_launch(
            "idem-embedded", host_id="host-1", runner_id="runner-stale", generation=1
        )
    with pytest.raises(OmnigentIdempotencyError, match="reserved launch generation"):
        await store.bind_embedded_runner(
            "idem-embedded", host_id="host-1", runner_id="runner-other-session"
        )


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
        run_store=store, config=_embedded_config(), host_channels=Channels(),
        runner_binding_root_secret="root-secret",
    )
    with pytest.raises(OmnigentBridgeError, match="rejected"):
        await facade.dispatch_runner(idempotency_key="idem-embedded")

    row = await store.get_existing("idem-embedded")
    assert row.metadata_["embedded_runner_launch"]["state"] == "failed"
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

    # MoonLadderStudios/MoonMind#3424: supported controls leave typed,
    # replayable request/outcome evidence without storing the request body.
    events = await store.list_events(
        (await store.get_existing("idem-embedded")).bridge_session_id
    )
    control_events = [event for event in events if event.event_type == "lifecycle.control"]
    assert [event.metadata_["metadata"]["controlOutcome"] for event in control_events] == [
        "requested", "accepted", "completed"
    ]
    assert all(
        event.metadata_["metadata"]["expectedRunnerId"] == "runner-1"
        for event in control_events
    )


@pytest.mark.asyncio
async def test_embedded_duplicate_control_does_not_repeat_live_side_effect(store) -> None:
    """MoonLadderStudios/MoonMind#3424 reconciles ambiguous control retries."""
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
        calls = 0

        async def post_runner_event(self, **_kwargs):
            self.calls += 1
            return {"ok": True}

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    event = BridgeSessionEventRequest(
        type="message", data={"text": "hello"}, idempotencyKey="control-1"
    )

    first = await facade.post_event(session_id="sess-embedded", event=event)
    retry = await facade.post_event(session_id="sess-embedded", event=event)

    assert first == {"ok": True}
    assert retry == {
        "ok": True,
        "status": "completed",
        "idempotencyKey": "control-1",
        "reconciled": True,
    }
    assert channels.calls == 1


@pytest.mark.asyncio
async def test_embedded_control_claim_precedes_live_side_effect(store) -> None:
    """Issue #3424 makes an in-flight duplicate reconcile without redelivery."""
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

    delivery_started = asyncio.Event()
    release_delivery = asyncio.Event()

    class Channels:
        calls = 0

        async def post_runner_event(self, **_kwargs):
            self.calls += 1
            delivery_started.set()
            await release_delivery.wait()
            return {"ok": True}

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    event = BridgeSessionEventRequest(
        type="message", data={"text": "hello"}, idempotencyKey="control-race"
    )

    first_task = asyncio.create_task(
        facade.post_event(session_id="sess-embedded", event=event)
    )
    await delivery_started.wait()
    duplicate = await facade.post_event(session_id="sess-embedded", event=event)
    release_delivery.set()
    first = await first_task

    assert first == {"ok": True}
    assert duplicate == {
        "ok": False,
        "status": "delivery_unknown",
        "idempotencyKey": "control-race",
        "reconciled": True,
    }
    assert channels.calls == 1


@pytest.mark.asyncio
async def test_embedded_control_rejects_expected_turn_state_mismatch(store) -> None:
    """Issue #3424 rejects stale controls before their live side effect."""
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
        calls = 0

        async def post_runner_event(self, **_kwargs):
            self.calls += 1
            return {"ok": True}

    channels = Channels()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=channels
    )
    event = BridgeSessionEventRequest(
        type="message", idempotencyKey="stale-turn",
        expectedTurnState="posted",
    )

    with pytest.raises(OmnigentBridgeError) as exc_info:
        await facade.post_event(session_id="sess-embedded", event=event)

    assert exc_info.value.code == "omnigent_embedded_control_state_mismatch"
    assert channels.calls == 0


@pytest.mark.asyncio
async def test_embedded_harvest_publishes_canonical_manifest(store, tmp_path) -> None:
    """MoonLadderStudios/MoonMind#3424 preserves resources after host cleanup."""
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway

    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    row = await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    class Channels:
        async def request_runner(self, **kwargs):
            path = kwargs["path"]
            if kwargs["expect_json"]:
                if path.endswith("/changes"):
                    return {"items": [{"path": "src/main.py"}]}
                if path.endswith("/filesystem"):
                    return {"items": [{"path": "src/main.py", "type": "file"}]}
                return {"items": [{"id": "report", "filename": "report.txt"}]}
            if "/diff/" in path:
                return b"+changed\n"
            return b"durable content"

    gateway = LocalOmnigentArtifactGateway(root=tmp_path / "artifacts")
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels(),
        artifact_gateway=gateway,
    )

    result = await facade.harvest_session("sess-embedded")
    persisted = await store.get_bridge_session(row.bridge_session_id)
    manifest = await gateway.read_text(result["captureManifestRef"])

    assert result["status"] == "completed_with_diagnostics"
    assert persisted.capture_manifest_ref == result["captureManifestRef"]
    assert '"schemaVersion": "moonmind.omnigent.capture_manifest.v1"' in manifest
    assert "MoonLadderStudios/MoonMind#3424" in manifest
    assert '"finalSnapshotRef"' in manifest
    assert '"runnerLogRef"' in manifest
    assert '"diagnosticsRef"' in manifest
    assert '"optionalNotApplicable"' in manifest
    assert '"evidenceCompleteness": "optional_degradation"' in manifest
    events = await store.list_events(row.bridge_session_id)
    associations = [
        event for event in events
        if (event.metadata_ or {}).get("eventIdentity", "").startswith(
            "embedded-resource-association:"
        )
    ]
    assert len(associations) == 1
    assert associations[0].artifact_ref == result["captureManifestRef"]
    association_metadata = associations[0].metadata_["metadata"]
    assert association_metadata["controlType"] == "harvest"
    assert association_metadata["controlKey"] == "harvest:sess-embedded"
    assert association_metadata["resourceProjectionRef"] == result[
        "resourceProjectionRef"
    ]

    # Association and artifacts remain durable when no live host channel exists.
    replay = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), artifact_gateway=gateway
    )
    snapshot = await replay.get_session("sess-embedded")
    assert snapshot["terminalEvidenceAvailable"] is True


@pytest.mark.asyncio
async def test_embedded_harvest_required_persistence_failure_is_typed(store) -> None:
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    row = await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id=None, agent_name=None,
        target_metadata={"workspace": "/workspace/repo"},
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.bind_embedded_runner(
        "idem-embedded", host_id="host-1", runner_id="runner-1"
    )

    class Channels:
        async def request_runner(self, **kwargs):
            return {"items": []} if kwargs["expect_json"] else b""

    class FailingGateway:
        async def write_json(self, **_kwargs):
            raise OSError("disk unavailable token=must-not-persist")

        async def write_bytes(self, **_kwargs):
            raise OSError("disk unavailable")

        async def read_text(self, _ref):
            return ""

    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config(), host_channels=Channels(),
        artifact_gateway=FailingGateway(),
    )
    with pytest.raises(OmnigentBridgeError) as exc_info:
        await facade.harvest_session("sess-embedded", payload={"idempotencyKey": "h1"})

    assert exc_info.value.code == "omnigent_embedded_required_evidence_unavailable"
    events = await store.list_events(row.bridge_session_id)
    failed = [event for event in events if (event.metadata_ or {}).get(
        "eventIdentity") == "embedded-control:h1:failed"]
    assert len(failed) == 1
    assert "must-not-persist" not in str(failed[0].metadata_)


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


@pytest.mark.asyncio
async def test_embedded_discovery_uses_registered_codex_host_evidence(store) -> None:
    """MoonLadderStudios/MoonMind#3421: discovery is durable and bounded."""
    sentinel = "sentinel-host-secret-never-persisted"
    await _bind_active_host(store, host_id="host-codex")
    await store.record_embedded_host_lifecycle(
        host_id="host-codex",
        credential_generation=1,
        capabilities={
            "harnesses": ["codex-native"],
            "hostCredential": sentinel,
        },
        readiness="ready",
    )
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )

    hosts = await facade.list_hosts()
    agents = await facade.list_agents()

    assert hosts == [
        {
            "id": "host-codex",
            "status": "ready",
            "ready": True,
            "capabilities": {
                "harnesses": ["codex-native"],
                "hostCredential": "[REDACTED]",
            },
            "disconnected": False,
        }
    ]
    assert sentinel not in str(hosts)
    assert agents[0]["id"] == "codex-native"


@pytest.mark.asyncio
async def test_host_auth_profile_is_durably_bound_to_preassigned_lease(store) -> None:
    await _bind_active_host(store, host_id="host-auth-bound")
    await store.record_embedded_host_lifecycle(
        host_id="host-auth-bound",
        credential_generation=8,
        credential_profile_id="host-auth-primary",
        readiness="ready",
    )
    with pytest.raises(OmnigentIdempotencyError, match="profile does not match"):
        await store.record_embedded_host_lifecycle(
            host_id="host-auth-bound",
            credential_generation=9,
            credential_profile_id="unrelated-host-auth",
        )
    with pytest.raises(OmnigentIdempotencyError, match="generation does not match"):
        await store.record_embedded_host_lifecycle(
            host_id="host-auth-bound",
            credential_generation=9,
            credential_profile_id="host-auth-primary",
        )


@pytest.mark.asyncio
async def test_embedded_snapshot_attach_and_stream_survive_disconnect(store) -> None:
    """MoonLadderStudios/MoonMind#3421: history does not require a live socket."""
    await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id="codex-native",
        agent_name="Codex",
        target_metadata={"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
        workflow_id="workflow-1",
        agent_run_id="agent-run-1",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    await store.record_session_created(
        "idem-embedded",
        session_id="sess-embedded",
        agent_id="codex-native",
        endpoint_ref="embedded",
    )
    await store.record_lifecycle_event(
        "idem-embedded",
        event_type="terminal",
        status="completed",
        event_identity="terminal-3421",
        summary="done",
    )
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )
    binding = BridgePrincipalBinding(
        workflow_id="workflow-1",
        correlation_id="mm:wf-embedded",
        idempotency_key="idem-embedded",
        agent_run_id="agent-run-1",
    )

    snapshot = await facade.get_session("sess-embedded")
    attached = await facade.attach_session(
        session_id="sess-embedded", binding=binding
    )
    replay = [event async for event in facade.stream_events("sess-embedded")]

    assert snapshot["status"] == "completed"
    assert (
        attached["moonmind"]["bridgeSessionId"]
        == snapshot["moonmind"]["bridgeSessionId"]
    )
    assert replay[-1]["type"] == "terminal"
    assert replay[-1]["status"] == "completed"

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await facade.delete_session("sess-embedded")
    assert excinfo.value.code == "omnigent_bridge_capability_unavailable"


@pytest.mark.asyncio
async def test_embedded_terminal_stream_drains_all_pages() -> None:
    class _PagedStore:
        def __init__(self) -> None:
            self.calls = 0

        async def get_session_by_provider_session_id(self, session_id: str):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
            self.calls += 1
            sequence = self.calls
            return SimpleNamespace(
                rows=[
                    SimpleNamespace(
                        sequence=sequence,
                        event_id=f"event-{sequence}",
                        event_type="response.delta",
                        timestamp=datetime.now(UTC),
                        normalized_status="active",
                        text_preview=f"page-{sequence}",
                        artifact_ref=None,
                        metadata_={},
                    )
                ],
                has_more=self.calls == 1,
            )

        async def get_bridge_session(self, bridge_session_id: str):
            return SimpleNamespace(
                status="completed",
                terminal_refs={},
                diagnostics_ref=None,
                final_snapshot_ref=None,
            )

    store = _PagedStore()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_embedded_config()
    )

    events = [event async for event in facade.stream_events("sess-embedded")]

    assert [event["sequence"] for event in events] == [1, 2, 2]
    assert events[-1]["type"] == "terminal"
    assert store.calls == 2
