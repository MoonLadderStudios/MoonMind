"""An update that replaces the agent host restarts only the interrupted Step.

MoonLadderStudios/MoonMind#4627 (AC1/AC2). A three-step workflow runs on a real
Temporal server: two agent steps complete, then an update stops the agent
worker mid-turn and removes the third step's actual host container. The
production Run execution stage, AgentRun child, profile-bound execute Activity,
generic realizer, PostgreSQL runtime bindings and host leases, canonical turn
commands, workspace materializer, checkpoint capture, artifact storage and
saved-work finalization stay real. A fresh worker composition then shares
only that durable state.

The provider is controlled: it writes and reads workspace bytes with
``docker exec`` in the real host container and reports the host loss it
observes. The same journey runs on two host fleets:

* The reliability shard has no Omnigent server or host image, so each host is
  an inert container of a Compose dependency image carrying the production
  lease labels and workspace bind.
* The exact-artifact job's credential-free recovery row launches every host
  from the pinned Omnigent host image through the production host runtime,
  launcher, registration authority and fenced cleanup. Sessions are opened
  on the real Omnigent server, host loss is the production predicate over
  that server's session projection, and the lost session is drained through
  the production cleanup client.

The successor admits on the same image; admission on a newly installed image
is #4503's selection.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import tarfile
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RawValue
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from moonmind.omnigent import execute as execute_module
from moonmind.omnigent.activity_ownership import current_delivery_owner
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupAuthority
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
from moonmind.omnigent.credential_materializers import (
    CredentialCleanupResult,
    CredentialRuntimeHandle,
    credential_runtime_identity,
)
from moonmind.omnigent.execute import _snapshot_projects_lost_host
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
from moonmind.omnigent.host_runtime import (
    GenericOmnigentHostRuntime,
    PreparedHostInputs,
)
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.turn_delivery import admission_epoch
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
    stable_binding_id,
)
from moonmind.omnigent.session_cleanup import OmnigentSessionCleanupService
from moonmind.omnigent.settings import (
    resolved_api_token,
    resolved_host_runner_token,
    resolved_server_url,
)
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    _ACTIVITY_HANDLER_ATTRS,
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.integration.omnigent.test_exact_docker_n_way_concurrency import (
    _credential_recovery_host_class,
    _exact_docker_environment_reason,
    _expected_host_owner,
    _host_image,
    _host_server_url,
    _network_ref,
)
from tests.integration.reliability.test_release_routing_journey import connect
from tests.integration.services.temporal.workflows.test_agent_run import (
    MockProviderProfileManager,
)
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import _plan
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

STEPS = ("prepare", "analyze", "implement")
# Already pulled by the reliability Compose stack; it runs no agent software.
HOST_IMAGE = "postgres:17"
SAVED_BEFORE_UPDATE = "saved before the update\n"
COMPLETED_AFTER_UPDATE = "completed after the update\n"
PROFILE_BOUND_EXECUTE = "integration.omnigent.profile_bound_execute"
CREDENTIALLESS = "none@1"
#: The receipt the exact-artifact job validates for the exact-image journey.
EXACT_RECEIPT = "interrupted-step-exact-docker.json"


def _journey_plan(*, host_class=None, agent_id=None):
    payload = _plan("opencode-go/model").payload.model_dump(mode="json", by_alias=True)
    payload["workspaceMutation"] = "allowed"
    if host_class is not None:
        # The exact host runs the stock OpenCode agent and observed harness
        # on the credentialless route, so registration can admit it.
        payload["agentSource"]["upstreamId"] = agent_id
        payload["harnessImplementationRef"] = host_class.declaredHarnessImplementations[
            0
        ].implementationRef
        payload["credentialBindings"]["primary-model"][
            "materializerRef"
        ] = CREDENTIALLESS
    return create_execution_plan_envelope(payload)


def _plan_document() -> bytes:
    return json.dumps(
        {
            "plan_version": "1.0",
            "metadata": {
                "title": "Interrupted step journey",
                "created_at": "2026-10-13T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:" + "a" * 64,
                    "artifact_ref": "art:sha256:" + "b" * 64,
                },
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "nodes": [
                {
                    "id": step,
                    "tool": {"type": "agent_runtime", "name": "omnigent"},
                    "inputs": {
                        "instructions": f"Do the {step} work.",
                        "runtime": {"mode": "omnigent"},
                    },
                }
                for step in STEPS
            ],
            "edges": [
                {"from": "prepare", "to": "analyze"},
                {"from": "analyze", "to": "implement"},
            ],
        }
    ).encode()


def _host_class() -> HostClass:
    return HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            # The bind-mounted workspace stays writable by the test process.
            "runtime": {"uid": os.getuid(), "gid": os.getgid(), "home": "/home/app"},
        }
    )


@workflow.defn(failure_exception_types=[Exception])
class InterruptedStepUpdateJourney:
    """Host the production Run execution stage on a real Temporal server."""

    @workflow.run
    async def run(self, payload: dict) -> list[dict]:
        run = MoonMindRunWorkflow()
        run._owner_id = payload["ownerId"]
        run._repo = None
        run._integration = None
        await run._run_execution_stage(
            parameters=payload["parameters"], plan_ref=payload["planRef"]
        )
        return [
            {"logicalStepId": row.get("logicalStepId"), "status": row.get("status")}
            for row in run._step_ledger_rows
        ]


class _ArtifactServiceProxy:
    """One database session per artifact operation, as the worker runtime does."""

    def __init__(self, sessions) -> None:
        self._sessions = sessions

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            async with self._sessions() as session:
                service = TemporalArtifactService(TemporalArtifactRepository(session))
                return await getattr(service, name)(*args, **kwargs)

        return call


class _InertDockerHosts:
    """Production workspace and cleanup owners around an inert agent container.

    ``prepare`` is the production workspace materializer, including checkpoint
    restore, and ``cleanup`` the production fenced Docker cleanup. Only the
    launch is a plain ``docker run``: the shard has no Omnigent host image.
    """

    def __init__(self, *, docker, materializer, launches: list[dict]) -> None:
        self._docker = docker
        self._materializer = materializer
        self._cleanup = DockerOmnigentHostCleanupService(docker)
        self._launches = launches

    async def validate_repository_intent(self, *, request, plan):
        await OmnigentGithubCredentialService(None).validate_repository_intent(
            request=request, plan=plan
        )

    async def prepare(
        self,
        *,
        request,
        plan,
        host_class,
        repository_owner_ref,
        authority_sink=None,
        **_kwargs,
    ):
        workspace = await self._materializer.materialize(
            request,
            mutation=plan.payload.workspaceMutation,
            runtime_uid=host_class.runtime["uid"],
            runtime_gid=host_class.runtime["gid"],
            plan=plan,
            repository_owner_ref=repository_owner_ref,
            authority_sink=authority_sink,
        )
        if authority_sink is not None:
            await authority_sink({"kind": "workspace", **workspace})
        return PreparedHostInputs(
            workspace_attachment=workspace,
            skill_attachment={},
            tool_attachments=(),
            egress_attestation={"networkRef": "none"},
        )

    async def realize(
        self,
        *,
        request,
        host_lease_ref,
        host_lease_generation,
        host_class,
        launch_policy,
        prepared,
        authority_sink,
        **_kwargs,
    ):
        name = "moonmind-test-step-host-" + uuid4().hex[:12]
        volume = name + "-state"
        await authority_sink(
            {
                "kind": "host",
                "containerName": name,
                "stateVolumeRef": volume,
                "launchGeneration": host_lease_generation,
            }
        )
        labels = [
            "--label",
            "moonmind.host_lease_ref=" + host_lease_ref,
            "--label",
            "moonmind.host_lease_generation=" + str(host_lease_generation),
        ]
        await self._docker.run(["docker", "volume", "create", *labels, volume])
        workspace = prepared.workspace_attachment["sourceRef"]
        await self._docker.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--user",
                "{uid}:{gid}".format(**host_class.runtime),
                "--network",
                "none",
                *labels,
                "--mount",
                f"type=bind,src={workspace},dst=/workspaces/run",
                "--mount",
                f"type=volume,src={volume},dst=/owned-state",
                "--entrypoint",
                "/bin/sh",
                HOST_IMAGE,
                "-c",
                "sleep 600",
            ]
        )
        step = request.step_execution
        self._launches.append(
            {
                "container": name,
                "hostId": name,
                "step": step.logical_step_id,
                "ordinal": step.execution_ordinal,
                "workspace": workspace,
            }
        )
        return {
            "omnigentHostId": name,
            "hostId": name,
            "containerName": name,
            "stateVolumeRef": volume,
            "hostClassRef": host_class.ref,
            "launchPolicyRef": launch_policy.ref,
            "workspacePath": "/workspaces/run",
        }

    async def cleanup(self, *, host_context, host_lease_ref, host_lease_generation):
        return await self._cleanup.cleanup(
            container_name=host_context["containerName"],
            state_volume_ref=host_context["stateVolumeRef"],
            host_lease_ref=host_lease_ref,
            host_lease_generation=host_lease_generation,
        )

    async def cleanup_prepared(self, _prepared):
        return None

    async def cleanup_authorities(self, _authorities):
        return None


class _ProviderCapacity:
    """AgentRun admitted Provider Profile capacity; the provider is controlled."""

    async def acquire_all(self, **_kwargs):
        return ()

    async def release_all(self, _leases):
        return None


class _NoCredentials:
    async def materialize_all(self, **_kwargs):
        return ()

    async def load_cleanup_handles(self, *_args):
        return ()

    async def cleanup_all(self, _handles):
        return ()


class _ControlledSessions:
    async def drain(self, session_id):
        return {"sessionId": session_id, "stopped": True}


class _InertFleet:
    """Reliability-shard hosts: inert containers and controlled session ids."""

    execution_timeout = timedelta(seconds=120)
    turn_start_seconds = 60
    recovery_seconds = 90
    heartbeat_timeout = timedelta(seconds=2)
    activity_heartbeat_seconds = None

    def __init__(self, docker) -> None:
        self.docker = docker
        self.host_class = _host_class()
        self.plan = _journey_plan()

    def host_runtime(self, *, materializer, launches):
        return _InertDockerHosts(
            docker=self.docker, materializer=materializer, launches=launches
        )

    def route(self):
        return _ProviderCapacity(), _NoCredentials()

    def session_cleanup(self):
        return _ControlledSessions()

    async def open_session(self, request):
        step = request.step_execution
        return f"session-{step.logical_step_id}-{step.execution_ordinal}"

    async def host_lost(self, _session, container) -> bool:
        return not await _running(self.docker, container)

    async def discard(self, launches) -> None:
        for launch in launches:
            await self.docker.run(
                ["docker", "rm", "-f", launch["container"]], check=False
            )
            await self.docker.run(
                ["docker", "volume", "rm", launch["container"] + "-state"],
                check=False,
            )


class _CredentiallessRoute:
    """Provider Profile capacity and credentials for the plan's ``none@1`` route.

    AgentRun admitted the capacity and the route carries no secret, so each
    handle has no attachments. A credentialless handle is what lets the
    production registration admit the stock OpenCode harness, which reports
    ``needs-auth`` without host credentials.
    """

    async def acquire_all(self, *, workflow_id, idempotency_key, **_kwargs):
        lease_ref = "provider-lease:" + idempotency_key
        return (
            AcquiredProviderLease(
                slot="primary-model",
                provider_profile_ref="opencode-go-primary",
                capacity_scope_ref="moonmind-test",
                provider_lease_ref=lease_ref,
                credential_generation=1,
                lease=CredentialLease(
                    profile_id="opencode-go-primary",
                    runtime_id="omnigent",
                    lease_id=lease_ref,
                    owner_id=workflow_id,
                    purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                ),
                owned_by_workflow=True,
            ),
        )

    async def release_all(self, _leases):
        return None

    async def materialize_all(self, *, acquired_leases, **_kwargs):
        handles = []
        for item in acquired_leases:
            ref, digest = credential_runtime_identity(item, CREDENTIALLESS)
            handles.append(
                CredentialRuntimeHandle(
                    credentialRuntimeRef=ref,
                    providerProfileRef=item.provider_profile_ref,
                    providerLeaseRef=item.provider_lease_ref,
                    credentialGeneration=item.credential_generation,
                    materializerRef=CREDENTIALLESS,
                    cleanupRef="credential-cleanup:" + digest,
                )
            )
        return tuple(handles)

    async def load_cleanup_handles(self, _provider_leases, handles):
        return tuple(
            CredentialRuntimeHandle.model_validate(item)
            for item in (handles or {}).values()
        )

    async def cleanup_all(self, handles):
        return tuple(
            CredentialCleanupResult(cleanupRef=item.cleanupRef, removed=True)
            for item in handles
        )


class _NoSkills:
    """No Skill is selected; the host mounts an empty read-only projection."""

    def __init__(self, path: Path) -> None:
        self._attachment = {
            "kind": "bind",
            "sourceRef": str(path),
            "targetPath": "/opt/moonmind-skills",
            "accessMode": "read-only",
        }

    async def anticipated_attachment(self, _resolved, *, owner_ref):
        return dict(self._attachment)

    async def materialize(self, _resolved, *, owner_ref):
        return dict(self._attachment)

    async def cleanup(self, _attachment):
        return None


class _NoTools:
    async def materialize(self, _resolved, *, image_ref):
        return ()


class _IsolatedNetwork:
    """The recovery row's internal network and proxy replace deployment egress.

    The deployment egress gateway is not part of that row, and its network has
    no route to any provider.
    """

    def __init__(self, network: str) -> None:
        self._network = network

    async def attest(self, *, request, launch_policy):
        return {
            "networkRef": self._network,
            "profileRef": "moonmind-test-internal-network",
            "profileDigest": "sha256:" + "0" * 64,
            "appliedRuleDigest": "sha256:" + "0" * 64,
        }


class _NoCapabilities:
    """The controlled provider calls no MoonMind capability from the host."""

    def build(self, **_kwargs):
        return {}


class _RuntimeOwnedWorkspaces:
    """Hand each materialized workspace to the exact host's runtime identity.

    A deployed worker runs as the same unprivileged identity as the exact
    host image, so every workspace it creates is already writable there. The
    recovery row's driver runs as root for the Docker socket, so its scratch
    workspaces need the ownership the production worker would have given them.
    """

    def __init__(self, materializer) -> None:
        self._materializer = materializer

    async def materialize(self, request, **kwargs):
        workspace = await self._materializer.materialize(request, **kwargs)
        uid, gid = kwargs["runtime_uid"], kwargs["runtime_gid"]
        if os.geteuid() == 0 and workspace.get("kind") == "bind":
            root = Path(workspace["sourceRef"])
            for path in (root, *root.rglob("*")):
                os.chown(path, uid, gid, follow_symlinks=False)
        return workspace

    def __getattr__(self, name):
        return getattr(self._materializer, name)


class _ObservedLaunches:
    """Record each registered exact host with the image Docker actually ran.

    The exact image's build and harness identity is attested by the recovery
    row before this journey runs; here every launch is bound to that image.
    """

    def __init__(self, docker, launches: list[dict]) -> None:
        self._docker = docker
        self._launches = launches

    async def attest(self, *, request, spec, launch_result, registration, **_kwargs):
        container = launch_result["containerName"]
        _code, image_id, _err = await self._docker.run(
            ["docker", "container", "inspect", "--format", "{{.Image}}", container]
        )
        step = request.step_execution
        self._launches.append(
            {
                "container": container,
                "hostId": registration["omnigentHostId"],
                "step": step.logical_step_id,
                "ordinal": step.execution_ordinal,
                "workspace": spec.workspaceAttachment["sourceRef"],
                "imageId": image_id.strip(),
                "stateVolume": launch_result["stateVolumeRef"],
                "controlVolume": launch_result.get("controlVolumeRef"),
            }
        )
        return {}


class _ExactFleet:
    """Hosts launched from the pinned Omnigent images on the real server."""

    execution_timeout = timedelta(minutes=15)
    turn_start_seconds = 600
    recovery_seconds = 600
    # A real launch outlasts the inert fleet's timeout, so the Activity
    # heartbeats more often than its production 30 seconds.
    heartbeat_timeout = timedelta(seconds=10)
    activity_heartbeat_seconds = 1.0

    def __init__(self, *, docker, client, host_class, agent_id, skills) -> None:
        self.docker = docker
        self.client = client
        self.host_class = host_class
        self.plan = _journey_plan(host_class=host_class, agent_id=agent_id)
        self._skills = skills
        self.sessions: list[str] = []
        self.lost_snapshot: dict | None = None

    @classmethod
    async def open(cls, docker, tmp_path: Path) -> "_ExactFleet":
        image_ref = _host_image()
        await docker.run(["docker", "pull", image_ref])
        client = OmnigentHttpClient(
            base_url=resolved_server_url(), api_token=resolved_api_token()
        )
        agent = next(
            (
                item
                for item in await client.list_agents()
                if item.get("name") == "opencode-native-ui"
            ),
            None,
        )
        assert agent is not None, "credential-free OpenCode stock agent is unavailable"
        skills = tmp_path / "skills"
        skills.mkdir()
        return cls(
            docker=docker,
            client=client,
            host_class=await _credential_recovery_host_class(docker, image_ref),
            agent_id=agent["id"],
            skills=skills,
        )

    def host_runtime(self, *, materializer, launches):
        return GenericOmnigentHostRuntime(
            launcher=DockerOmnigentHostLauncher(
                backend=self.docker,
                runtime_scripts=OmnigentRuntimeScriptService(),
                server_url=_host_server_url(),
                host_api_token=resolved_host_runner_token(),
            ),
            workspace_service=_RuntimeOwnedWorkspaces(materializer),
            skill_service=_NoSkills(self._skills),
            tool_service=_NoTools(),
            github_credential_service=OmnigentGithubCredentialService(None),
            egress_service=_IsolatedNetwork(_network_ref()),
            runtime_environment_service=_NoCapabilities(),
            registration_waiter=OmnigentHostRegistrationService(
                client=self.client,
                expected_owner=_expected_host_owner(),
                backend=self.docker,
            ),
            host_attestor=_ObservedLaunches(self.docker, launches),
            cleanup_service=DockerOmnigentHostCleanupService(self.docker),
        )

    def route(self):
        route = _CredentiallessRoute()
        return route, route

    def session_cleanup(self):
        return OmnigentSessionCleanupService(self.client)

    async def open_session(self, request):
        omnigent = request.parameters["omnigent"]
        session = omnigent["session"]
        created = await self.client.create_session(
            {
                "agent_id": omnigent["agent"]["agentId"],
                "host_type": session["hostType"],
                "host_id": session["hostId"],
                "workspace": session["workspace"],
                "harness_override": omnigent["agent"]["harnessOverride"],
                "initial_items": [],
                "title": "MoonMind interrupted step journey",
            }
        )
        self.sessions.append(created["id"])
        return created["id"]

    async def host_lost(self, session, _container) -> bool:
        snapshot = await self.client.get_session(session)
        if not _snapshot_projects_lost_host(snapshot):
            return False
        self.lost_snapshot = {
            "runnerOnline": snapshot.get("runner_online"),
            "hostOnline": snapshot.get("host_online"),
            "hostResumable": snapshot.get("host_resumable"),
        }
        return True

    async def discard(self, launches) -> None:
        for session in self.sessions:
            try:
                await self.client.delete_session(session, delete_branch=False)
            except Exception:  # noqa: BLE001 - best-effort fixture teardown
                pass
        for launch in launches:
            await self.docker.run(
                ["docker", "rm", "-f", launch["container"]], check=False
            )
            for volume in (launch["stateVolume"], launch["controlVolume"]):
                if volume:
                    await self.docker.run(
                        ["docker", "volume", "rm", volume], check=False
                    )


async def _running(docker, name: str) -> bool:
    code, out, _err = await docker.run(
        ["docker", "container", "inspect", "--format", "{{.State.Running}}", name],
        check=False,
    )
    return code == 0 and out.strip() == "true"


async def _interrupted_step_journey(tmp_path, monkeypatch, fleet) -> dict:
    """Run the update journey on ``fleet`` and assert its shared outcome."""

    connected = await connect()
    client = Client(
        connected.service_client,
        namespace=connected.namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    queue = "interrupted-step-" + uuid4().hex
    agent_queue, control_queue = queue + "-agent", queue + "-control"
    workspace_root = tmp_path / "agent_jobs"
    workspace_root.mkdir()
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "blobs")),
    )
    # Visibility attributes are not registered in the isolated namespace.
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda *_a, **_k: None)
    # A stopped worker's in-flight delivery is retried promptly.
    monkeypatch.setattr(
        agent_run_module,
        "STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT",
        fleet.heartbeat_timeout,
    )
    if fleet.activity_heartbeat_seconds is not None:
        monkeypatch.setattr(
            execute_module,
            "_ACTIVITY_HEARTBEAT_INTERVAL_SECONDS",
            fleet.activity_heartbeat_seconds,
        )
    docker = fleet.docker
    plan = fleet.plan
    plan_ref = "artifact:journey-run-plan"
    host_class = fleet.host_class
    launches: list[dict] = []
    deliveries: list[tuple[str, int, str, str, str]] = []
    execution_requests = []
    turn_started = asyncio.Event()
    restored: dict[str, str] = {}

    async def host_running(name: str) -> bool:
        return await _running(docker, name)

    def container_of(host_id: str) -> str:
        return next(item["container"] for item in launches if item["hostId"] == host_id)

    async def in_host(host: str, *command: str) -> str:
        _code, out, _err = await docker.run(
            ["docker", "exec", "-w", "/workspaces/run", host, *command]
        )
        return out

    async def write(host: str, script: str) -> None:
        await in_host(host, "sh", "-c", script)

    async def controlled_provider(request, *, session_authority_sink, **_continuation):
        step = request.step_execution
        key = (step.logical_step_id, step.execution_ordinal)
        host = container_of(request.parameters["omnigent"]["session"]["hostId"])
        session = session_authority_sink.binding.omnigentSessionId
        if session:
            # A replacement worker re-observes the accepted turn; it never
            # starts the provider again.
            deliveries.append((*key, host, "observed", session))
        else:
            session = await fleet.open_session(request)
            await session_authority_sink.session_created(session)
            deliveries.append((*key, host, "started", session))
            if key == ("implement", 1):
                await write(host, "printf 'saved before the update\\n' > progress.txt")
                turn_started.set()
            elif key == ("implement", 2):
                restored["bytes"] = await in_host(host, "cat", "progress.txt")
                await write(
                    host, "printf 'completed after the update\\n' >> progress.txt"
                )
            else:
                await write(host, f"printf '{key[0]} done\\n' > {key[0]}.txt")
        if key == ("implement", 1):
            while not await fleet.host_lost(session, host):
                await asyncio.sleep(0.2)
            return AgentRunResult(
                summary="The agent host was replaced by an update.",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                retryRecommendation="retry_step_execution",
                metadata={"omnigentSessionId": session},
            )
        return AgentRunResult(
            summary=f"{key[0]} completed", metadata={"omnigentSessionId": session}
        )

    async def resolve_host(_plan):
        return host_class, get_launch_policy("omnigent-on-demand@1")

    async with isolated_postgres([]) as sessions:
        # One create_all so shared PostgreSQL enum types are created once.
        async with sessions.kw["bind"].begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
        await DbExecutionPlanStore(sessions).persist(plan)
        artifacts = TemporalOmnigentArtifactGateway(sessions)

        def build_realizer():
            """One worker generation's composition, sharing only durable state."""

            async def daemon_command(argv, input_bytes=None):
                return await docker.run(argv, input_bytes=input_bytes, check=False)

            control_plane = OmnigentControlPlaneStore(sessions)
            capacity, credentials = fleet.route()
            realizer = GenericOmnigentHostRealizer(
                execution_owner=current_delivery_owner,
                runtime_binding_store=DbRuntimeBindingStore(sessions),
                provider_lease_coordinator=capacity,
                credential_provisioning_service=credentials,
                host_lease_repository=DbOmnigentHostLeaseRepository(sessions),
                host_runtime=fleet.host_runtime(
                    materializer=OmnigentWorkspaceMaterializer(
                        command_runner=daemon_command,
                        workspace_root=workspace_root,
                        artifact_service=artifacts,
                    ),
                    launches=launches,
                ),
                planned_host_resolver=resolve_host,
                session_driver=controlled_provider,
                session_cleanup_service=fleet.session_cleanup(),
                workspace_publisher=OmnigentWorkspacePublicationService(
                    workspace_root,
                    artifact_gateway=artifacts,
                    execution_plan_store=DbExecutionPlanStore(sessions),
                ),
                artifact_gateway=artifacts,
                turn_command_service=CanonicalTurnCommandService(control_plane),
                cleanup_authority=CanonicalCleanupAuthority(control_plane),
                # Installed-image admission is #4503's; this update keeps it.
                deployment_validator=AsyncMock(return_value=None),
                heartbeat_interval_seconds=0.5,
                heartbeat_ttl_seconds=60,
            )
            execute = realizer.execute

            async def record_execution(request, selected_plan):
                execution_requests.append(request)
                return await execute(request, selected_plan)

            realizer.execute = record_execution
            return realizer

        current = {"realizer": build_realizer()}
        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: SimpleNamespace(require=lambda _ref: current["realizer"]),
        )

        catalog = build_default_activity_catalog()

        def bind(family: str, keyword: str, implementation, *, exclude=()):
            unique = {}
            for item in catalog.activities:
                owner = _ACTIVITY_HANDLER_ATTRS.get(item.activity_type, ("",))[0]
                if owner == family and item.activity_type not in exclude:
                    unique.setdefault(item.activity_type, item)
            return build_activity_bindings(
                TemporalActivityCatalog(
                    activities=tuple(unique.values()), fleets=catalog.fleets
                ),
                **{keyword: implementation},
            )

        proxy = _ArtifactServiceProxy(sessions)
        artifact_activities = TemporalArtifactActivities(proxy)
        (agent_binding,) = bind(
            "agent_runtime",
            "agent_runtime_activities",
            TemporalAgentRuntimeActivities(),
            exclude={
                item.activity_type
                for item in catalog.activities
                if item.activity_type != PROFILE_BOUND_EXECUTE
            },
        )
        # Checkpoint capture, step checkpoints and artifacts are the real
        # Activities over PostgreSQL metadata and blob storage.
        storage_handlers = [
            binding.handler
            for binding in bind(
                "artifacts",
                "artifact_activities",
                artifact_activities,
                exclude={
                    "artifact.read",
                    "provider_profile.list",
                    "provider_profile.manager_state",
                },
            )
        ] + [
            binding.handler
            for binding in bind(
                "sandbox",
                "sandbox_activities",
                TemporalSandboxActivities(
                    artifact_service=proxy, workspace_root=workspace_root
                ),
            )
            if binding.activity_type == "workspace.capture_checkpoint"
        ]
        (read_artifact,) = (
            binding.handler
            for binding in bind("artifacts", "artifact_activities", artifact_activities)
            if binding.activity_type == "artifact.read"
        )

        @activity.defn(name="artifact.read")
        async def read_plan_or_artifact(request):
            # The authored plan is a fixture; every other read is real.
            if isinstance(request, dict) and request.get("artifact_ref") == plan_ref:
                return _plan_document()
            return await read_artifact(request)

        @activity.defn(dynamic=True)
        async def control(args: Sequence[RawValue]):
            """Planning, Skill and Provider Profile decisions outside this journey."""

            name = activity.info().activity_type
            payload = (
                activity.payload_converter().from_payload(args[0].payload)
                if args
                else None
            )
            if name in {"plan.validate", "agent_skill.resolve"}:
                return {}
            if name == "integration.resolve_adapter_metadata":
                return {
                    "agent_id": "omnigent",
                    "execution_style": "streaming_gateway",
                    "supports_callbacks": False,
                }
            if name == "omnigent.evaluate_session_admission":
                return (
                    _admission(profile_ref="opencode-go-primary")
                    .model_copy(update={"admitted": False})
                    .model_dump(mode="json", by_alias=True)
                )
            if name == "omnigent.admit_generic_host_capacity":
                return {"admitted": True}
            if name == "provider_profile.list":
                return {"profiles": []}
            if name == "provider_profile.manager_state":
                return {
                    "running": True,
                    "inspection_succeeded": True,
                    "requester_pending": True,
                }
            if name == "agent_runtime.publish_artifacts":
                return payload
            raise ApplicationError(
                f"Unexpected journey Activity: {name}", non_retryable=True
            )

        class RoutedCatalog:
            def __init__(self, base):
                self._base = base

            def resolve_activity(self, activity_type):
                return replace(
                    self._base.resolve_activity(activity_type),
                    task_queue=(
                        agent_queue
                        if activity_type == PROFILE_BOUND_EXECUTE
                        else control_queue
                    ),
                )

            def __getattr__(self, name):
                return getattr(self._base, name)

        for module in (run_module, agent_run_module):
            monkeypatch.setattr(
                module, "DEFAULT_ACTIVITY_CATALOG", RoutedCatalog(catalog)
            )
        monkeypatch.setattr(
            MoonMindRunWorkflow, "_workflow_child_task_queue", lambda _self: queue
        )
        original_options = MoonMindAgentRun._execute_kwargs_for_route
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_execute_kwargs_for_route",
            staticmethod(
                lambda route: {
                    **original_options(route),
                    "task_queue": (
                        agent_queue
                        if route.activity_type == PROFILE_BOUND_EXECUTE
                        else control_queue
                    ),
                }
            ),
        )
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_manager_workflow_id",
            staticmethod(lambda _runtime: queue + "-manager"),
        )

        # Namespace creation can precede propagation to the history service.
        for attempt in range(30):
            try:
                manager = await client.start_workflow(
                    MockProviderProfileManager.run,
                    {},
                    id=queue + "-manager",
                    task_queue=queue,
                )
                break
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND or attempt == 29:
                    raise
                await asyncio.sleep(1)

        old_worker = Worker(
            client,
            task_queue=agent_queue,
            activities=[agent_binding.handler],
            graceful_shutdown_timeout=timedelta(0),
        )
        old_worker_task = asyncio.create_task(old_worker.run())
        try:
            async with Worker(
                client,
                task_queue=queue,
                workflows=[
                    InterruptedStepUpdateJourney,
                    MoonMindAgentRun,
                    MockProviderProfileManager,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ), Worker(
                client,
                task_queue=control_queue,
                activities=[control, read_plan_or_artifact, *storage_handlers],
            ):
                handle = await client.start_workflow(
                    InterruptedStepUpdateJourney.run,
                    {
                        "ownerId": "journey-owner",
                        "planRef": plan_ref,
                        "parameters": {
                            "publishMode": "none",
                            "executionProfileRef": "opencode-go-primary",
                            "omnigentExecutionPlan": {
                                "planRef": plan.planRef,
                                "planDigest": "sha256:"
                                + plan.planRef.rsplit(":", 1)[-1],
                                "planArtifactRef": "artifact:journey-plan",
                                "taskInputSnapshotRef": "artifact:journey-input",
                                "taskInputSnapshotDigest": "sha256:" + "2" * 64,
                            },
                        },
                    },
                    id=queue,
                    task_queue=queue,
                    execution_timeout=fleet.execution_timeout,
                )
                result_task = asyncio.ensure_future(handle.result())
                started_task = asyncio.ensure_future(turn_started.wait())
                await asyncio.wait(
                    {result_task, started_task},
                    timeout=fleet.turn_start_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if result_task.done():
                    result_task.result()
                    raise AssertionError("the workflow finished before the update")
                assert turn_started.is_set(), "the implement turn never started"
                lost_host = deliveries[-1][2]
                # The update: the worker stops mid-turn, the affected agent
                # host container is removed, and a new worker composition
                # starts with only the durable state.
                await old_worker.shutdown()
                await asyncio.gather(old_worker_task)
                assert await host_running(lost_host)
                await docker.run(["docker", "rm", "-f", lost_host])
                current["realizer"] = build_realizer()
                async with Worker(
                    client, task_queue=agent_queue, activities=[agent_binding.handler]
                ):
                    rows = await asyncio.wait_for(result_task, fleet.recovery_seconds)
                history = await handle.fetch_history()
        finally:
            if "manager" in locals():
                await manager.terminate(reason="interrupted step journey finished")
            if not old_worker_task.done():
                await old_worker.shutdown()
            await asyncio.gather(old_worker_task, return_exceptions=True)
            await fleet.discard(launches)

        assert rows == [
            {"logicalStepId": step, "status": "completed"} for step in STEPS
        ]
        # Completed steps ran once. The replacement worker only re-observed the
        # interrupted turn, then the step got one successor on a new host.
        assert [
            (step, ordinal, kind) for step, ordinal, _host, kind, _s in deliveries
        ] == [
            ("prepare", 1, "started"),
            ("analyze", 1, "started"),
            ("implement", 1, "started"),
            ("implement", 1, "observed"),
            ("implement", 2, "started"),
        ]
        assert [(item["step"], item["ordinal"]) for item in launches] == [
            ("prepare", 1),
            ("analyze", 1),
            ("implement", 1),
            ("implement", 2),
        ]
        assert launches[2]["container"] == lost_host != launches[3]["container"]
        assert launches[2]["workspace"] != launches[3]["workspace"]
        for launch in launches:
            assert not await host_running(launch["container"])

        # The successor is the same workflow and logical step, and it restored
        # the bytes captured into artifact storage before the old host's
        # workspace stopped mattering.
        predecessor, successor = execution_requests[-2], execution_requests[-1]
        assert predecessor.step_execution.execution_ordinal == 1
        assert successor.step_execution.execution_ordinal == 2
        assert successor.step_execution.workflow_id == queue
        assert successor.step_execution.logical_step_id == "implement"
        assert "workspaceCheckpointRestoreRef" not in predecessor.workspace_spec
        restore_ref = successor.workspace_spec["workspaceCheckpointRestoreRef"]
        with tarfile.open(
            fileobj=io.BytesIO(await artifacts.read_bytes(restore_ref)), mode="r:gz"
        ) as archive:
            saved = {
                Path(member.name).name: archive.extractfile(member).read().decode()
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "progress.txt"
            }
        assert saved == {"progress.txt": SAVED_BEFORE_UPDATE}
        assert restored == {"bytes": SAVED_BEFORE_UPDATE}
        assert (Path(launches[3]["workspace"]) / "progress.txt").read_text() == (
            SAVED_BEFORE_UPDATE + COMPLETED_AFTER_UPDATE
        )

        # The lost attempt licensed its successor only after a confirmed stop,
        # and its host lease capacity was released through its owner.
        lost = await DbRuntimeBindingStore(sessions).get(
            stable_binding_id(
                execution_plan_ref=plan.planRef,
                idempotency_key=predecessor.idempotency_key,
                admission_epoch=admission_epoch(predecessor),
            )
        )
        assert lost.state is RuntimeBindingState.cleaned
        assert lost.terminalResult["retryRecommendation"] == "retry_step_execution"
        assert "successorBlocked" not in lost.phaseResults
        lease = await DbOmnigentHostLeaseRepository(sessions).get(lost.hostLeaseRef)
        assert lease.status == "cleaned"
        await Replayer(
            workflows=[InterruptedStepUpdateJourney],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history)
    return {
        "workflowId": queue,
        "rows": rows,
        "launches": launches,
        "deliveries": deliveries,
        "restoredBytes": restored["bytes"],
        "completedBytes": (Path(launches[3]["workspace"]) / "progress.txt").read_text(),
    }


async def test_update_restarts_only_the_interrupted_step_from_saved_bytes(
    tmp_path, monkeypatch
):
    await _interrupted_step_journey(
        tmp_path, monkeypatch, _InertFleet(DockerCommandBackend())
    )


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _exact_journey_receipt(*, source_commit, image_ref, journey, lost_snapshot):
    """Project the exact-image journey into the receipt its CI row validates."""

    sessions = {
        (step, ordinal): session
        for step, ordinal, _host, kind, session in journey["deliveries"]
        if kind == "started"
    }

    def attempt(launch):
        return {
            "executionOrdinal": launch["ordinal"],
            "containerName": launch["container"],
            "hostId": launch["hostId"],
            "hostImageId": launch["imageId"],
            "sessionId": sessions[(launch["step"], launch["ordinal"])],
        }

    return {
        "schemaVersion": 1,
        "sourceCommit": source_commit,
        "hostImageRef": image_ref,
        "workflowId": journey["workflowId"],
        "logicalStepId": "implement",
        "steps": journey["rows"],
        "predecessor": attempt(journey["launches"][2]),
        "successor": attempt(journey["launches"][3]),
        "hostLoss": lost_snapshot,
        "savedWorkspaceDigest": _digest(SAVED_BEFORE_UPDATE),
        "restoredWorkspaceDigest": _digest(journey["restoredBytes"]),
        "completedWorkspaceDigest": _digest(journey["completedBytes"]),
    }


async def test_exact_omnigent_host_update_restarts_only_the_interrupted_step(
    tmp_path, monkeypatch
):
    """Replace a real Omnigent host mid-step; only that step gets a successor.

    The exact-artifact job's credential-free recovery row runs this on the
    pinned server and host images with a real Temporal server and PostgreSQL.
    A missing real environment is a skip the row's validator rejects.
    """

    reason = _exact_docker_environment_reason()
    if reason:
        pytest.skip(f"exact-image interrupted step journey unavailable: {reason}")
    docker = DockerCommandBackend()
    fleet = await _ExactFleet.open(docker, tmp_path)
    journey = await _interrupted_step_journey(tmp_path, monkeypatch, fleet)
    image_id = (
        await docker.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", _host_image()]
        )
    )[1].strip()
    assert [launch["imageId"] for launch in journey["launches"]] == [image_id] * 4
    assert fleet.lost_snapshot == {
        "runnerOnline": False,
        "hostOnline": False,
        "hostResumable": False,
    }
    source_commit = os.getenv("MOONMIND_CREDENTIAL_RECOVERY_SOURCE_COMMIT", "").strip()
    if not source_commit:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    output = Path(
        os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR", str(tmp_path))
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / EXACT_RECEIPT).write_text(
        json.dumps(
            _exact_journey_receipt(
                source_commit=source_commit,
                image_ref=_host_image(),
                journey=journey,
                lost_snapshot=fleet.lost_snapshot,
            ),
            sort_keys=True,
        )
        + "\n"
    )
