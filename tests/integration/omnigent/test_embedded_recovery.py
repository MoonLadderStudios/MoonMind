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
    OmnigentBridgeSession,
)
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED, parse_bridge_config
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostHeartbeatRequest,
    EmbeddedHostRegisterRequest,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
)
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.bridge_store import (
    OmnigentDigestMismatchError,
    OmnigentIdempotencyError,
)
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
    assert row.terminal_refs["cleanupState"] == "runner_exited"
    assert [event.event_type for event in events] == ["lifecycle.terminal"]
    assert result["actions"][-1]["action"] == "runner_exit_cleanup"
    assert repository.stopped == ["host-lease-1"]


@pytest.mark.parametrize(
    ("state", "expected_action"),
    [
        ("launch_reserved", "abandoned_launch_cleanup"),
        ("launch_acknowledged", "acknowledgement_without_binding_cleanup"),
        ("runner_identity_bound", "binding_without_tunnel_cleanup"),
        ("runner_tunnel_waiting", "binding_without_tunnel_cleanup"),
        ("stale", "stale_binding_cleanup"),
    ],
)
async def test_restart_janitor_classifies_each_abandoned_lifecycle_boundary(
    store, session_factory, state, expected_action,
) -> None:
    await _seed(store, session_factory)
    row = await store.get_existing("recovery")
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    async with session_factory() as session:
        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        metadata = dict(persisted.metadata_ or {})
        metadata["embedded_runner_lifecycle"] = {
            "version": 1, "state": state, "updatedAt": old, "timeline": [],
        }
        persisted.metadata_ = metadata
        await session.commit()

    refs = await store.embedded_reconciliation_host_lease_refs(
        abandoned_before=datetime.now(UTC) - timedelta(seconds=90)
    )
    assert refs == {"host-lease-1": expected_action}


async def test_restart_janitor_rejects_changed_credential_generation(
    store, session_factory,
) -> None:
    await _seed(store, session_factory)
    async with session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "profile-1")
        profile.credential_generation = 2
        await session.commit()

    refs = await store.embedded_reconciliation_host_lease_refs(
        abandoned_before=datetime.now(UTC)
    )
    assert refs == {"host-lease-1": "credential_generation_cleanup"}


@pytest.mark.parametrize(
    "crash_boundary",
    [
        "reservation_before_command",
        "command_before_acknowledgement",
        "acknowledgement_before_binding",
        "binding_before_tunnel",
        "tunnel_before_readiness_persist",
        "message_response_before_posted_persist",
        "runner_exit_before_terminal_bridge_persist",
    ],
)
async def test_seven_boundary_restart_matrix_preserves_single_side_effects(
    store, session_factory, crash_boundary,
) -> None:
    """Crash once at every issue-listed production ownership boundary."""

    await _seed(store, session_factory)
    class ObservedHost:
        launch_command_count = 0
        post_count = 0
        created_runner_ids: list[str] = []
        runner_ready = False

        async def launch_runner(self, **kwargs):
            self.launch_command_count += 1
            runner_id = OmnigentHostAuthAdapter(
                allowed_tokens=frozenset({kwargs["binding_token"]})
            ).runner_id_for_binding_token(kwargs["binding_token"])
            if runner_id not in self.created_runner_ids:
                self.created_runner_ids.append(runner_id)
            self.runner_ready = True
            return runner_id

        def is_runner_ready(self, _runner_id):
            return self.runner_ready

        async def wait_runner_ready(self, _runner_id):
            return self.runner_ready

        async def post_runner_event(self, **_kwargs):
            self.post_count += 1
            return {"pending_id": "pending-1", "item_id": "item-1"}

        async def request_runner(self, **_kwargs):
            return {
                "events": [{"text": "marker-1"}],
                "firstMessageResponse": {
                    "pending_id": "pending-1", "item_id": "item-1",
                },
            }

    class OneShotCrashStore:
        """Inject a process death immediately before/after one durable write."""

        def __init__(self, inner, method, *, after=False, state=None):
            self.inner = inner
            self.method = method
            self.after = after
            self.state = state
            self.injected = False

        def __getattr__(self, name):
            target = getattr(self.inner, name)
            if name != self.method:
                return target

            async def call(*args, **kwargs):
                matches = self.state is None or kwargs.get("state") == self.state
                if matches and not self.injected and not self.after:
                    self.injected = True
                    raise RuntimeError(f"injected crash: {crash_boundary}")
                result = await target(*args, **kwargs)
                if matches and not self.injected:
                    self.injected = True
                    raise RuntimeError(f"injected crash: {crash_boundary}")
                return result
            return call

    host = ObservedHost()
    fault_specs = {
        "reservation_before_command": ("begin_embedded_runner_launch", True, None),
        "command_before_acknowledgement": (
            "mark_embedded_runner_state", False, "launch_acknowledged",
        ),
        "acknowledgement_before_binding": ("bind_embedded_runner", False, None),
        "binding_before_tunnel": ("bind_embedded_runner", True, None),
        "tunnel_before_readiness_persist": (
            "mark_embedded_runner_state", True, "runner_tunnel_ready",
        ),
        "message_response_before_posted_persist": ("mark_posted", False, None),
        "runner_exit_before_terminal_bridge_persist": (
            "record_embedded_runner_exit", False, None,
        ),
    }
    method, after, state = fault_specs[crash_boundary]
    crashing_store = OneShotCrashStore(store, method, after=after, state=state)
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=crashing_store, config=_config(), host_channels=host,
        runner_binding_root_secret="recovery-root-secret",
    )

    if crash_boundary in {
        "reservation_before_command", "command_before_acknowledgement",
        "acknowledgement_before_binding", "binding_before_tunnel",
    }:
        with pytest.raises(RuntimeError, match="injected crash"):
            await facade.dispatch_runner(idempotency_key="recovery")
    else:
        dispatch = await facade.dispatch_runner(idempotency_key="recovery")
        runner_id = dispatch["runnerId"]
        if crash_boundary == "tunnel_before_readiness_persist":
            with pytest.raises(RuntimeError, match="injected crash"):
                await facade.record_runner_tunnel_ready(runner_id=runner_id)

    # Reconstruct both production owners, then retry the interrupted operation.
    restarted_store = OmnigentBridgeSessionStore(session_factory)
    restarted = OmnigentEmbeddedHostProtocolFacade(
        run_store=restarted_store, config=_config(), host_channels=host,
        runner_binding_root_secret="recovery-root-secret",
    )
    reused = await restarted.dispatch_runner(idempotency_key="recovery")
    runner_id = reused["runnerId"]
    assert reused["reused"] is (crash_boundary != "reservation_before_command")
    await restarted.record_runner_tunnel_ready(runner_id=runner_id)

    await store.mark_prepared("recovery", digest="digest-1", marker="marker-1")
    await store.mark_posting("recovery")
    response = await restarted.post_event(
        session_id="session-1",
        event=EmbeddedHostSessionEventRequest(type="message", data={"text": "hello"}),
    )
    if crash_boundary == "message_response_before_posted_persist":
        with pytest.raises(RuntimeError, match="injected crash"):
            await crashing_store.mark_posted("recovery", response=response)
        await restarted.reconcile_first_message(session_id="session-1")
    else:
        await restarted_store.mark_posted("recovery", response=response)

    # Retrying execute after either posting boundary must only revalidate the
    # digest; it must not regress the durable embedded lifecycle.
    lifecycle_before_retry = (
        (await restarted_store.get_existing("recovery")).metadata_[
            "embedded_runner_lifecycle"
        ]["state"]
    )
    await restarted_store.mark_prepared(
        "recovery", digest="digest-1", marker="marker-1"
    )
    assert (
        (await restarted_store.get_existing("recovery")).metadata_[
            "embedded_runner_lifecycle"
        ]["state"]
        == lifecycle_before_retry
    )

    if crash_boundary == "runner_exit_before_terminal_bridge_persist":
        with pytest.raises(RuntimeError, match="injected crash"):
            await facade.record_runner_exit(runner_id=runner_id, error="exit 1")
        restarted_store = OmnigentBridgeSessionStore(session_factory)
    await restarted_store.record_embedded_runner_exit(runner_id=runner_id, error="exit 1")

    row = await restarted_store.get_existing("recovery")
    lifecycle = row.metadata_["embedded_runner_lifecycle"]
    events = await restarted_store.list_events(row.bridge_session_id)
    assert host.launch_command_count == 1
    assert host.post_count == 1
    assert host.created_runner_ids == [runner_id]
    assert row.omnigent_host_id == "host-1"
    assert row.omnigent_runner_id == runner_id
    assert row.omnigent_session_id == "session-1"
    assert row.first_message_item_id == "item-1"
    assert lifecycle["state"] == "failed"
    assert [event.event_type for event in events] == ["lifecycle.terminal"]
    refs = await restarted_store.cleanup_required_host_lease_refs()
    assert refs == {"host-lease-1"}


async def test_embedded_response_before_persist_reconciles_and_digest_change_fails_closed(
    store, session_factory,
) -> None:
    await _seed(store, session_factory)
    await store.bind_embedded_runner(
        "recovery", host_id="host-1", runner_id="runner-1"
    )
    await store.mark_embedded_runner_state(
        "recovery", state="runner_tunnel_ready", code="authenticated_runner_handshake"
    )
    await store.mark_prepared("recovery", digest="digest-1", marker="marker-1")
    await store.mark_posting("recovery")

    class ObservedRunner:
        post_count = 0

        async def post_runner_event(self, **_kwargs):
            self.post_count += 1
            return {"pending_id": "pending-1", "item_id": "item-1"}

        async def request_runner(self, **_kwargs):
            return {
                "events": [{"text": "marker-1"}],
                "firstMessageResponse": {
                    "pending_id": "pending-1", "item_id": "item-1",
                },
            }

    runner = ObservedRunner()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_config(), host_channels=runner
    )
    await facade.post_event(
        session_id="session-1",
        event=EmbeddedHostSessionEventRequest(type="message", data={"text": "hello"}),
    )

    # The runner accepted marker-1, then MoonMind restarted before mark_posted.
    restarted_store = OmnigentBridgeSessionStore(session_factory)
    row = await restarted_store.get_existing("recovery")
    assert row.first_message_state == "posting"
    assert row.first_message_marker == "marker-1"
    restarted = OmnigentEmbeddedHostProtocolFacade(
        run_store=restarted_store, config=_config(), host_channels=runner
    )
    assert await restarted.reconcile_first_message(session_id="session-1") == {
        "reconciled": True, "pending_id": "pending-1", "item_id": "item-1",
    }
    assert runner.post_count == 1

    with pytest.raises(OmnigentDigestMismatchError, match="different first-message"):
        await restarted_store.mark_prepared(
            "recovery", digest="digest-changed", marker="marker-changed"
        )
    final = await restarted_store.get_existing("recovery")
    assert final.first_message_digest == "digest-1"
    assert final.first_message_item_id == "item-1"
