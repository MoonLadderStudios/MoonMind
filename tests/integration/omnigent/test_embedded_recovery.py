"""Durable embedded-host recovery matrix for MoonMind#3370."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
    OmnigentBridgeSession,
)
from api_service.services.omnigent_policies import seed_bootstrap_policies
from moonmind.config.settings import settings
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
from moonmind.omnigent.control_plane import TurnSourceKind
from moonmind.omnigent.execute import OmnigentSessionStillRunningError
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.remediation_workspace import (
    RemediationLiveWorkspace,
    RemediationLoopHead,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
)
from moonmind.security.egress import (
    CONTROL_PLANE_NETWORK_REF,
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_PROFILE_SET_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_PROFILE,
)
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
)

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
    with pytest.raises(OmnigentBridgeError, match="generation does not match"):
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
                status="ready",
            )]

        async def claim_host_lease_cleanup(
            self, lease_id, *, expected_status, expected_last_heartbeat_at,
            ttl_seconds,
        ):
            assert lease_id == "host-lease-1"
            assert expected_status == "ready"
            assert ttl_seconds == 90
            return SimpleNamespace(
                lease_id=lease_id,
                provider_profile_id="profile-1",
                binding_ref="binding-1",
                container_name="host-1",
                omnigent_session_id=None,
                last_heartbeat_at=expected_last_heartbeat_at,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                status="draining",
            )

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
    assert row.terminal_refs["leaseReleaseState"] == "held"
    assert [event.event_type for event in events] == [
        "lifecycle.terminal", "lifecycle.control", "lifecycle.control",
    ]
    assert events[-1].metadata_["metadata"]["controlOutcome"] == "completed"
    assert result["actions"][-1]["action"] == "runner_exit_cleanup"
    assert repository.stopped == ["host-lease-1"]


@pytest.mark.parametrize("cleanup_succeeds", [True, False])
async def test_remediation_continuation_janitor_uses_real_authority_chain(
    session_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_succeeds: bool,
) -> None:
    """Cross remediation admission, prepare, continuation, janitor, and artifacts.

    Docker and Temporal transports are deterministic hermetic boundaries, while
    every authority owner under test is the production implementation: the
    coordinator, remediation workspace owner, SQL host/bridge repositories,
    OAuth host runtime, Temporal artifact service, janitor action owner, and
    Provider Profile lease client.
    """

    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "temporal-artifacts"),
    )
    immutable_server = (
        "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "8" * 64
    )
    immutable_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "9" * 64

    async def resolve_image(image_ref: str) -> str:
        return immutable_server if "server" in image_ref else immutable_host

    async with session_factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                max_parallel_runs=1,
                credential_generation=3,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
            )
        )
        await session.commit()
        await seed_bootstrap_policies(
            session,
            image_resolver=resolve_image,
        )

    workspace_root = tmp_path / "workspaces"
    workspace_owner = SandboxRemediationWorkspaceOwner(workspace_root)
    loop_id = "issue-3625-remediation"
    branch_ref = "refs/heads/remediation/issue-3625"
    checkpoint_ref = "artifact://checkpoint-3625"
    workspace_digest = "sha256:" + "a" * 64
    prior_workspace_id = "prior-remediation-workspace"
    prior_workflow_id = "mm:issue-3625:verification"
    prior_step_id = f"{prior_workflow_id}:run:verify:execution:1"
    workspace_owner.records.ensure(
        SandboxWorkspaceRecord(
            workspace_id=prior_workspace_id,
            workflow_id=prior_workflow_id,
            step_execution_id=prior_step_id,
            relative_path="repo",
        )
    )
    prior_workspace = (
        workspace_root / "temporal_sandbox" / prior_workspace_id / "repo"
    )
    prior_workspace.mkdir(parents=True)
    (prior_workspace / "candidate.txt").write_text(
        "verified cumulative remediation head\n",
        encoding="utf-8",
    )
    workspace_owner.record_loop_head(
        RemediationLoopHead(
            loop_id=loop_id,
            branch_ref=branch_ref,
            checkpoint_ref=checkpoint_ref,
            workspace_digest=workspace_digest,
            head_version=2,
            base_commit="b" * 40,
            manifest_ref="artifact://manifest-3625",
        )
    )
    workspace_owner.record_live_workspace(
        RemediationLiveWorkspace(
            loop_id=loop_id,
            branch_ref=branch_ref,
            checkpoint_ref=checkpoint_ref,
            workspace_digest=workspace_digest,
            head_version=2,
            workspace_id=prior_workspace_id,
            workflow_id=prior_workflow_id,
            step_execution_id=prior_step_id,
        )
    )

    workflow_id = "mm:issue-3625:remediation"
    step_execution_id = f"{workflow_id}:run:remediate:execution:1"
    destination_workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode()
    ).hexdigest()[:24]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId=workflow_id,
        idempotencyKey="issue-3625-remediation-attempt",
        stepExecution=AgentRuntimeStepExecutionLaunch(
            workflowId=workflow_id,
            runId="run",
            logicalStepId="remediate",
            executionOrdinal=1,
            stepExecutionId=step_execution_id,
            runtimeContextPolicy="fresh_agent_run",
        ),
        remediationWorkspace={
            "schemaVersion": "v1",
            "loopId": loop_id,
            "branchRef": branch_ref,
            "attemptOrdinal": 1,
            "workflowId": workflow_id,
            "runId": "run",
            "logicalStepId": "remediate",
            "stepExecutionId": step_execution_id,
            "baseCheckpointRef": checkpoint_ref,
            "baseWorkspaceDigest": workspace_digest,
            "expectedHeadVersion": 2,
            "headAuthorityRef": "artifact://loop-head-3625",
            "destinationWorkspaceLocator": {
                "kind": "sandbox",
                "workspaceId": destination_workspace_id,
                "relativePath": "repo",
            },
            "workspacePolicy": "continue_from_loop_head",
            "executionProfileRef": "codex",
            "hostProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-static@1",
            "workspaceCapabilitySnapshot": {
                "locatorKind": "sandbox",
                "restore": True,
            },
        },
        parameters={
            "publishMode": "none",
            "omnigent": {
                "executionTargetRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-static@1",
                "session": {},
            },
        },
    )

    store = OmnigentBridgeSessionStore(session_factory)
    repository = OmnigentOAuthHostRepository(session_factory)
    artifact_service = _OnDemandTemporalArtifactService(session_factory)
    release_signals: list[dict[str, object]] = []
    release_times: list[datetime] = []
    command_order: list[str] = []

    class TemporalClient:
        async def start_workflow(self, *_args, **_kwargs):
            return None

    class TemporalAdapter:
        async def get_client(self):
            return TemporalClient()

        async def update_workflow(self, _workflow_id, _name, payload):
            return {
                "profile_id": payload["execution_profile_ref"],
                "lease_id": (
                    f"provider-lease:{payload['execution_profile_ref']}:"
                    f"{payload['requester_workflow_id']}"
                ),
                "already_held": False,
            }

        async def signal_workflow(self, _workflow_id, name, payload):
            assert name == "release_slot"
            command_order.append("provider_released")
            release_times.append(datetime.now(UTC))
            release_signals.append(dict(payload))

    lease_client = ProviderProfileLeaseClient(TemporalAdapter())
    registered_host_id = (
        "host-" + hashlib.sha256(workflow_id.encode()).hexdigest()[:16]
    )

    class HostClient:
        async def list_hosts(self):
            return [
                {
                    "id": registered_host_id,
                    "name": "omnigent-host-codex",
                    "status": "online",
                    "harnesses": ["codex-native"],
                }
            ]

    runtime = OmnigentOAuthHostRuntime(
        client=HostClient(),
        workspace_root=workspace_root,
    )
    skill_projection = tmp_path / "resolved-skills"
    skill_projection.mkdir()

    async def prepare_skill_projection(**_kwargs):
        return skill_projection

    runtime._prepare_skill_projection = prepare_skill_projection  # type: ignore[method-assign]
    runtime._align_workspace_ownership = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    current_host_lease_ref: list[str] = []
    container_running = True
    server_container_id = hashlib.sha256(
        f"server:{workflow_id}".encode()
    ).hexdigest()
    host_container_id = hashlib.sha256(
        f"host:{workflow_id}".encode()
    ).hexdigest()
    gateway_image_digest = "sha256:" + "d" * 64
    host_image_digest = immutable_host.rsplit("@", 1)[-1]
    gateway_networks = {
        DEFAULT_EGRESS_PROFILE.network_ref: {},
        "moonmind_sandbox-egress-network": {},
        OMNIGENT_EGRESS_PROFILE.network_ref: {},
        CONTROL_PLANE_NETWORK_REF: {},
    }
    applied_rule_payload = {
        "profileDigest": OMNIGENT_EGRESS_PROFILE.digest,
        "configDigest": EGRESS_CONFIG_DIGEST,
        "gatewayImageDigest": gateway_image_digest,
        "internal": True,
        "ipv6": False,
        "idleSeconds": OMNIGENT_EGRESS_PROFILE.idle_seconds,
        "gatewayNetworks": sorted(gateway_networks),
        "enforcer": ENFORCER_IMPLEMENTATION,
    }
    applied_rule_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            applied_rule_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    client_address = "172.31.0.19"
    observed_architecture = {
        "aarch64": "arm64",
        "x86_64": "amd64",
    }.get(platform.machine().lower(), platform.machine().lower())

    async def run_host_command(*args, **_kwargs):
        nonlocal container_running
        if args[:3] == ("docker", "network", "inspect"):
            return (0, json.dumps({"Internal": True, "EnableIPv6": False}), "")
        if args[:2] == ("docker", "compose"):
            if " up " in f" {' '.join(args)} ":
                container_running = True
                command_order.append("host_started")
            elif " stop " in f" {' '.join(args)} ":
                command_order.append("host_stop_requested")
                if cleanup_succeeds:
                    container_running = False
            elif " ps " in f" {' '.join(args)} ":
                identity = (
                    server_container_id if args[-1] == "omnigent" else host_container_id
                )
                return (0, f"{identity}\n", "")
            return (0, "", "")
        if args[:2] == ("docker", "inspect"):
            format_value = args[3]
            identity = args[4]
            if identity == OMNIGENT_EGRESS_PROFILE.gateway_ref:
                return (
                    0,
                    json.dumps(
                        {
                            "labels": {
                                "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                                "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                                "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                            },
                            "networks": gateway_networks,
                            "image": gateway_image_digest,
                            "health": "healthy",
                        }
                    ),
                    "",
                )
            if identity == server_container_id:
                if format_value == "{{json .Config.Image}}":
                    return (0, json.dumps(immutable_server), "")
                if format_value == "{{json .Image}}":
                    return (0, json.dumps("sha256:" + "8" * 64), "")
            if identity == host_container_id and format_value.startswith(
                '{"labels"'
            ):
                return (
                    0,
                    json.dumps(
                        {
                            "labels": {
                                "moonmind.egress.profile": OMNIGENT_EGRESS_PROFILE.ref,
                                "moonmind.egress.profile_digest": OMNIGENT_EGRESS_PROFILE.digest,
                                "moonmind.egress.applied_rule_digest": applied_rule_digest,
                            },
                            "networks": {
                                OMNIGENT_EGRESS_PROFILE.network_ref: {
                                    "NetworkID": "network-" + host_container_id[:16],
                                    "EndpointID": "endpoint-" + host_container_id[:16],
                                    "IPAddress": client_address,
                                }
                            },
                            "imageRef": immutable_host,
                            "image": host_image_digest,
                        }
                    ),
                    "",
                )
            if identity == host_container_id and format_value == "{{.State.Running}}":
                return (0, "true\n" if container_running else "false\n", "")
            raise AssertionError(f"unexpected Docker inspect: {args}")
        if args[:2] == ("docker", "image") and args[2] == "inspect":
            if args[4].startswith('{"repoDigests"'):
                return (
                    0,
                    json.dumps(
                        {
                            "repoDigests": [immutable_server],
                            "architecture": observed_architecture,
                        }
                    ),
                    "",
                )
            return (0, json.dumps(observed_architecture), "")
        if args[:3] == (
            "docker",
            "exec",
            OMNIGENT_EGRESS_PROFILE.gateway_ref,
        ):
            if args[3] == "sha256sum":
                return (
                    0,
                    EGRESS_CONFIG_DIGEST.removeprefix("sha256:")
                    + "  /etc/squid/squid.conf\n",
                    "",
                )
            if args[3] == "cat":
                denial_time = datetime.now(UTC).timestamp()
                return (
                    0,
                    f"{denial_time} 2 {client_address} TCP_DENIED/403 0 CONNECT "
                    "metadata.invalid:443/ - HIER_NONE/- text/html\n",
                    "",
                )
        raise AssertionError(f"unexpected host command: {args}")

    runtime._run = run_host_command  # type: ignore[method-assign]

    async def execute_with_continuation(bound_request, **_kwargs):
        authorization = bound_request.parameters["omnigent"][
            "_moonmindProfileAuthorization"
        ]
        current_host_lease_ref.append(authorization["hostLeaseRef"])
        raise OmnigentSessionStillRunningError(
            "defer this exact host to the production janitor"
        )

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=session_factory,
        lease_client=lease_client,
        host_repository=repository,
        host_runtime=runtime,
        run_store=store,
        execution_runner=execute_with_continuation,
        artifact_gateway=artifact_service,
        artifact_service=artifact_service,
        workspace_owner=workspace_owner,
    )

    with pytest.raises(OmnigentSessionStillRunningError):
        await coordinator.execute(request)

    assert current_host_lease_ref
    host_lease_ref = current_host_lease_ref[0]
    persisted_host_lease = await repository.get_host_lease(host_lease_ref)
    assert persisted_host_lease is not None
    continuation_key = f"{request.idempotency_key}:repository-continuation:1"
    continuation = request.model_copy(
        update={"idempotency_key": continuation_key}
    )
    initial_bridge = await store.get_existing(request.idempotency_key)
    assert initial_bridge is not None
    await store.submit_canonical_turn(
        request=continuation,
        bridge_session_id=initial_bridge.bridge_session_id,
        source_kind=TurnSourceKind.REPOSITORY_CONTINUATION,
        effective_launch_snapshot=persisted_host_lease.effective_launch_snapshot,
        caller_id="repository_publication_controller",
    )
    await store.record_lifecycle_event(
        continuation_key,
        event_type="repository_continuation_1",
        status="failed",
    )
    authority = await store.get_egress_cleanup_authority(
        host_lease_ref=host_lease_ref
    )
    assert authority is not None
    assert authority["evidenceRequest"]["remediation"] is True
    launch_ref = authority["launchEvidenceRef"]
    launch_artifact, launch_bytes = await artifact_service.read(
        artifact_id=launch_ref,
        principal="system",
    )
    assert launch_artifact.sha256 == hashlib.sha256(launch_bytes).hexdigest()
    launch_payload = parse_and_verify_conformance_evidence(
        launch_bytes,
        location="integration-remediation-launch",
    )
    assert launch_payload["conformanceRow"] == "remediation_static_compose"

    host_lease = await repository.get_host_lease(host_lease_ref)
    assert host_lease is not None
    janitor = OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=HostClient(),
        run_store=store,
        lease_client=lease_client,
        artifact_gateway=artifact_service,
    )
    if cleanup_succeeds:
        action = await janitor.run_action(
            action_kind="host.stop",
            profile_id="codex",
            host_lease_ref=host_lease_ref,
            expected_host_state=host_lease.status,
            request_id="issue-3625-janitor-success",
        )
        terminal_ref = action["afterEvidenceRefs"][-1]
        assert release_signals
        assert command_order.index("host_stop_requested") < command_order.index(
            "provider_released"
        )
    else:
        with pytest.raises(OmnigentOAuthHostError) as cleanup_error:
            await janitor.run_action(
                action_kind="host.stop",
                profile_id="codex",
                host_lease_ref=host_lease_ref,
                expected_host_state=host_lease.status,
                request_id="issue-3625-janitor-failure",
            )
        terminal_ref = cleanup_error.value.egress_evidence_ref
        assert terminal_ref
        assert release_signals == []

    terminal_artifact, terminal_bytes = await artifact_service.read(
        artifact_id=terminal_ref,
        principal="system",
    )
    assert terminal_artifact.sha256 == hashlib.sha256(terminal_bytes).hexdigest()
    if cleanup_succeeds:
        terminal_created_at = terminal_artifact.created_at
        if terminal_created_at.tzinfo is None:
            terminal_created_at = terminal_created_at.replace(tzinfo=UTC)
        assert terminal_created_at <= release_times[0]
    terminal_payload = parse_and_verify_conformance_evidence(
        terminal_bytes,
        location="integration-remediation-terminal",
    )
    assert terminal_payload["launchEvidenceRef"] == launch_ref
    assert terminal_payload["cleanupResult"] == (
        "drained_owned_static_host" if cleanup_succeeds else "failed"
    )
    continuation_row = await store.get_existing(
        continuation_key
    )
    assert continuation_row is not None
    assert continuation_row.terminal_refs["egressLaunchEvidenceRef"] == launch_ref
    assert continuation_row.terminal_refs["egressEvidenceRef"] == terminal_ref
    assert continuation_row.terminal_refs["leaseReleaseState"] == (
        "released" if cleanup_succeeds else "held"
    )


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
    assert events[-1].event_type == "lifecycle.terminal"
    assert all(
        event.event_type in {"lifecycle.control", "lifecycle.terminal"}
        for event in events
    )
    refs = await restarted_store.cleanup_required_host_lease_refs()
    assert refs == {"host-lease-1"}

    # Restart/retry must preserve each durable authority and the enrolled OAuth
    # materialization instead of allocating replacement runtime state.
    async with session_factory() as session:
        profile_count = await session.scalar(
            select(func.count()).select_from(ManagedAgentProviderProfile)
        )
        binding_count = await session.scalar(
            select(func.count()).select_from(OmnigentOAuthHostBindingRecord)
        )
        lease_count = await session.scalar(
            select(func.count()).select_from(OmnigentOAuthHostLeaseRecord)
        )
        durable_profile = await session.get(ManagedAgentProviderProfile, "profile-1")
        durable_binding = await session.get(
            OmnigentOAuthHostBindingRecord, "binding-1"
        )
    assert profile_count == binding_count == lease_count == 1
    assert durable_profile is not None
    assert durable_profile.credential_generation == 1
    assert durable_binding is not None
    assert durable_binding.credential_mount_template_json["authVolumeRef"] == {
        "providerProfileId": "profile-1",
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "volumeRef": "profile-1-volume",
        "credentialGeneration": 1,
        "ownerUserId": "user-1",
    }
    assert [event.sequence for event in events] == list(
        range(1, len(events) + 1)
    )


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
