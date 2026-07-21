"""Durable embedded-host recovery matrix for MoonMind#3370."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED, parse_bridge_config
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostHeartbeatRequest,
    EmbeddedHostRegisterRequest,
    OmnigentEmbeddedHostProtocolFacade,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.bridge_store import OmnigentIdempotencyError
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/recovery.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
def store(session_factory):
    return OmnigentBridgeSessionStore(session_factory)


def _config():
    return parse_bridge_config({
        "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
        "hostConnection": {"embedded": {
            "proxyConformanceEvidenceRef": "artifact://proxy",
            "liveSmokeEvidenceRef": "artifact://live",
            "hostAuthConformanceEvidenceRef": "artifact://auth",
        }},
    })


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external", agentId="omnigent",
        correlationId="mm:wf-recovery", idempotencyKey="recovery",
    )


async def _seed(store: OmnigentBridgeSessionStore, session_factory) -> EmbeddedHostAuthContext:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(ManagedAgentProviderProfile(
            profile_id="profile-1", runtime_id="codex_cli", provider_id="openai",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            max_parallel_runs=1, credential_generation=1,
        ))
        session.add(OmnigentOAuthHostBindingRecord(
            binding_ref="binding-1", provider_profile_id="profile-1",
            endpoint_ref="embedded", harness="codex-native",
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
        ))
        await session.flush()
        session.add(OmnigentOAuthHostLeaseRecord(
            lease_id="host-lease-1", provider_profile_id="profile-1",
            provider_lease_id="provider-lease-1", binding_ref="binding-1",
            credential_generation=1, holder_workflow_id="mm:wf-recovery",
            idempotency_key="host-recovery", lease_purpose="execution_omnigent",
            omnigent_host_id="host-1", container_name="host-1", status="ready",
            acquired_at=now, last_heartbeat_at=now, expires_at=now + timedelta(hours=1),
        ))
        await session.commit()
    await store.get_or_create(
        request=_request(), endpoint_ref="embedded", agent_id="agent-1",
        agent_name="Codex", target_metadata={"workspace": "/workspace/repo"},
    )
    await store.bind_profile_authorization(
        request=_request(), endpoint_ref="embedded", provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1", credential_generation=1,
        host_binding_ref="binding-1", host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
    )
    await store.attach_session("recovery", "session-1")
    return EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
        runner_id="host-1", credential_generation=1,
    )


async def test_disconnect_restart_reconnect_and_retry_matrix(
    store, session_factory,
) -> None:
    auth = await _seed(store, session_factory)
    first = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_config())
    registration = EmbeddedHostRegisterRequest(
        hostId="host-1", capabilities={"harnesses": ["codex-native"]}
    )

    # Duplicate hello/heartbeat delivery is idempotent, including disconnect before launch.
    assert await first.register_host(request=registration, auth=auth) == await first.register_host(
        request=registration, auth=auth
    )
    await first.heartbeat(
        host_id="host-1", request=EmbeddedHostHeartbeatRequest(status="ready"), auth=auth
    )
    await first.disconnect_host(host_id="host-1", auth=auth)

    # Reconstructing the facade models a MoonMind restart; durable assignment survives.
    restarted = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_config())
    await restarted.register_host(request=registration, auth=auth)
    await store.bind_embedded_runner("recovery", host_id="host-1", runner_id="runner-1")
    await restarted.disconnect_host(host_id="host-1", auth=auth)  # after launch
    await restarted.heartbeat(
        host_id="host-1", request=EmbeddedHostHeartbeatRequest(status="ready"), auth=auth
    )

    # Activity retry cannot redirect the persisted runner or duplicate first-message state.
    await store.bind_embedded_runner("recovery", host_id="host-1", runner_id="runner-1")
    await store.mark_prepared("recovery", digest="digest-1", marker="marker-1")
    await store.mark_prepared("recovery", digest="digest-1", marker="marker-1")
    row = await store.get_existing("recovery")
    assert row.omnigent_runner_id == "runner-1"
    assert row.first_message_digest == "digest-1"

    stale = replace(auth, credential_generation=2)
    with pytest.raises(OmnigentBridgeError, match="active profile lease"):
        await restarted.heartbeat(
            host_id="host-1", request=EmbeddedHostHeartbeatRequest(), auth=stale
        )
    with pytest.raises(OmnigentIdempotencyError, match="another runner"):
        await store.bind_embedded_runner(
            "recovery", host_id="host-1", runner_id="stale-runner"
        )


async def test_runner_crash_disconnected_cleanup_survives_restart_and_drives_janitor(
    store, session_factory,
) -> None:
    auth = await _seed(store, session_factory)
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_config())
    await store.bind_embedded_runner("recovery", host_id="host-1", runner_id="runner-1")
    await facade.disconnect_host(host_id="host-1", auth=auth)
    await store.record_embedded_runner_exit(runner_id="runner-1", error="exit 1")

    class Repository:
        stopped: list[str] = []

        async def list_active_host_leases(self):
            now = datetime.now(UTC)
            return [SimpleNamespace(
                lease_id="host-lease-1", provider_profile_id="profile-1",
                binding_ref="binding-1", container_name="host-1",
                omnigent_session_id=None, last_heartbeat_at=now,
                expires_at=now + timedelta(hours=1),
            )]

        async def validate_binding(self, _binding_ref):
            return SimpleNamespace()

        async def mark_host_lease_stopped(self, lease_id):
            self.stopped.append(lease_id)

    class Runtime:
        async def container_exists(self, _name): return True
        async def stop_host(self, **_kwargs): return None
        async def list_managed_containers(self): return []

    repository = Repository()
    result = await OmnigentOAuthHostJanitor(
        repository=repository, runtime=Runtime(), client=SimpleNamespace(),
        run_store=store,
    ).run()
    row = await store.get_existing("recovery")
    events = await store.list_events(row.bridge_session_id)

    assert row.status == "failed"
    assert row.terminal_refs["cleanupState"] == "completed"
    assert row.terminal_refs["leaseReleaseState"] == "released"
    assert [event.event_type for event in events] == [
        "lifecycle.terminal", "lifecycle.control", "lifecycle.control",
    ]
    assert events[-1].metadata_["metadata"]["controlOutcome"] == "completed"
    assert result["actions"][-1]["action"] == "runner_exit_cleanup"
    assert repository.stopped == ["host-lease-1"]
