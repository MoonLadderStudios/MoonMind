"""An update replaces a live agent host; only the interrupted step continues.

MoonLadderStudios/MoonMind#4627. A Run has two completed steps and one active
Omnigent agent step. An update installs a new host image and replaces the
Omnigent server that observed the step, so the active host is projected lost
while its container process is still alive. The journey crosses that
replacement through the production owners:

- the Run workflow's real step loop retries only the interrupted step, as a
  ``runtime_recovered`` Step Execution restoring the lost execution's archive;
- the real generic realizer saves the workspace before releasing the host,
  resumes a stop whose acknowledgement was lost, and grants the successor only
  once the old container is confirmed removed;
- the real ``GenericOmnigentHostRuntime`` and ``DockerOmnigentHostLauncher``
  launch actual containers, the successor on the newly installed image;
- the real workspace materializer restores the saved bytes from durable
  artifact storage after the lost workspace is gone;
- the real fenced Docker cleanup refuses a late old-owner cleanup of the live
  successor, and a late redelivery of the old attempt changes nothing.

Only the Omnigent server transport (host registration, attestation and the
agent turn projection) is a controlled provider, and the AgentRun child is the
realizer call it dispatches. The agent itself is a real process inside each
host container, reading and writing the bind-mounted workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.db.models import (
    Base,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.realizers import generic_host
from moonmind.omnigent.realizers.turn_delivery import admission_epoch
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
    stable_binding_id,
)
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRuntimeStepExecutionLaunch,
    OmnigentExecutionPlanBinding,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)
from tests.unit.omnigent.test_image_owned_tool_delivery import _record_deployment_host
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _HOST_LOSS_RETRY_PATCHES,
    _drive_omnigent_step_attempts,
    _terminal_manifests,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

# Two releases of one small image repository stand in for the host image
# before and after the update. Their digests are read from the daemon, never
# hard-coded, so the journey launches exactly what the update installed.
_PLANNED_TAG = "busybox:1.36.1"
_INSTALLED_TAG = "busybox:1.37.0"
_SERVER_URL = "http://omnigent:8000"
_SAVED_LINE = "saved before the update"
_FINISHED_LINE = "finished on the installed image"


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *args], text=True
    ).strip()


def _workspace_id(workflow_id: str, step_execution_id: str) -> str:
    return hashlib.sha256(f"{workflow_id}:{step_execution_id}".encode()).hexdigest()[
        :24
    ]


async def _pulled_digest(backend: DockerCommandBackend, tag: str) -> str:
    await backend.run(["docker", "pull", "--quiet", tag], timeout_seconds=300)
    _code, out, _err = await backend.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag]
    )
    digests = [item for item in json.loads(out) or [] if "@sha256:" in item]
    assert digests, f"{tag} has no registry digest"
    return digests[0]


async def _running(backend: DockerCommandBackend, container: str) -> bool | None:
    """True/False for a present container, None once it no longer exists."""

    code, out, _err = await backend.run(
        ["docker", "container", "inspect", "--format", "{{.State.Running}}", container],
        check=False,
    )
    return None if code != 0 else out.strip() == "true"


async def _volume_exists(backend: DockerCommandBackend, volume: str) -> bool:
    code, _out, _err = await backend.run(
        ["docker", "volume", "inspect", volume], check=False
    )
    return code == 0


def _binding_ref(env, request: AgentExecutionRequest) -> str:
    return stable_binding_id(
        execution_plan_ref=env.plan.planRef,
        idempotency_key=request.idempotency_key,
        admission_epoch=admission_epoch(request),
    )


def _written(path: Path) -> bool:
    """Whether the agent finished writing one line to ``path``."""

    return path.exists() and path.read_text().endswith("\n")


async def _wait_for(predicate, *, timeout: float = 60.0, message: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.2)


class _AgentScripts:
    """The agent process inside each host container.

    Step Execution 1 edits the repository and keeps running. Its successor
    proves, from inside the new container, that it started from the saved
    bytes, then records its own result.
    """

    def build_entrypoint(self, *, step_execution_id: str, **_kwargs):
        workspace = "/workspaces/run"
        if step_execution_id.endswith(":execution:1"):
            script = (
                f"cd {workspace}; "
                "printf '%s\\n' \"print('interrupted edit')\" > app.py; "
                f"printf '%s\\n' '{_SAVED_LINE}' > progress.txt; "
                "exec sleep 600"
            )
        else:
            script = (
                f"cd {workspace}; "
                f"if [ \"$(cat progress.txt)\" = '{_SAVED_LINE}' ] "
                "&& grep -q 'interrupted edit' app.py; then "
                f"printf '%s\\n' '{_FINISHED_LINE}' > result.txt; "
                "else printf 'restored bytes missing\\n' > result.txt; fi; "
                "exec sleep 600"
            )
        return script, {}


class _StopAcknowledgements(DockerCommandBackend):
    """Real Docker; optionally lose or refuse the next host removals."""

    def __init__(self) -> None:
        self.lost_acknowledgements = 0
        self.refused_removals = 0
        self.removals: list[str] = []

    async def run(self, argv, **kwargs):
        removal = list(argv[:3]) == ["docker", "rm", "-f"]
        if removal and self.refused_removals:
            # The daemon cannot be reached: the container keeps running.
            self.refused_removals -= 1
            raise TimeoutError("docker daemon did not answer the stop")
        result = await super().run(argv, **kwargs)
        if removal:
            self.removals.append(str(argv[3]))
            if self.lost_acknowledgements:
                # The container is gone but its owner never learns it.
                self.lost_acknowledgements -= 1
                raise TimeoutError("docker stop acknowledgement was lost")
        return result


class _ControlledOmnigentServer:
    """Host registration and attestation answered by a controlled server."""

    def __init__(self, backend: DockerCommandBackend) -> None:
        self._backend = backend
        self.launches: list[dict[str, object]] = []

    async def wait_for_registration(
        self, *, correlation_name: str, expected_host_id=None, **_kwargs
    ):
        async def registered() -> bool:
            return bool(await _running(self._backend, correlation_name))

        await _wait_for(registered, message=f"{correlation_name} never started")
        return {"omnigentHostId": expected_host_id or correlation_name}

    async def attest(self, *, spec, launch_result, **_kwargs):
        self.launches.append(
            {
                "stepExecutionId": spec.stepExecutionId,
                "containerName": launch_result["containerName"],
                "stateVolumeRef": launch_result["stateVolumeRef"],
                "launchImageRef": launch_result["launchImageRef"],
                "preferInstalledImage": spec.preferInstalledImage,
                "hostLeaseRef": spec.hostLeaseRef,
                "hostLeaseGeneration": spec.hostLeaseGeneration,
            }
        )
        return {
            "hostHarnessAttestationRef": "artifact://host",
            "modelOptionAttestationRef": "artifact://models",
            "launchImageRef": launch_result["launchImageRef"],
        }


class _DeploymentInputs:
    """Run-owned skills, tools, egress and GitHub inputs outside this journey."""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir

    async def anticipated_attachment(self, _resolved, *, owner_ref):
        return {
            "kind": "bind",
            "sourceRef": str(self._skills_dir),
            "targetPath": "/opt/moonmind-skills",
            "accessMode": "read-only",
            "cleanupRef": f"skill-cleanup:{owner_ref}",
        }

    async def materialize(self, resolved, *, owner_ref):
        return await self.anticipated_attachment(resolved, owner_ref=owner_ref)

    async def cleanup(self, _attachment):
        return None

    async def validate_repository_intent(self, **_kwargs):
        return None

    async def anticipated_attachment_for_request(self, *_args, **_kwargs):
        return None

    async def attest(self, **_kwargs):
        return {
            "networkRef": "none",
            "profileRef": "egress:none",
            "profileDigest": "sha256:" + "a" * 64,
            "appliedRuleDigest": "sha256:" + "b" * 64,
            "attestationRef": "artifact://egress",
        }

    def build(self, **_kwargs):
        return {}


class _GithubCredentials(_DeploymentInputs):
    async def materialize(self, **_kwargs):
        return None


class _Tools(_DeploymentInputs):
    async def materialize(self, *_args, **_kwargs):
        return []


class _ProviderCapacity:
    """Provider leases counted per delivery, so a release is attributable."""

    def __init__(self, acquired) -> None:
        self._acquired = acquired
        self.held = 0

    async def acquire_all(self, **_kwargs):
        self.held += 1
        return (self._acquired,)

    async def release_all(self, leases):
        self.held -= len(tuple(leases))

    async def release_from_binding(self, _leases):
        self.held -= 1


def _host_class(image_ref: str, harness_host_class: HostClass) -> HostClass:
    payload = harness_host_class.model_dump(by_alias=True, mode="json")
    payload["imageRef"] = image_ref
    payload["omnigentVersion"] = "0.14.0"
    # The agent writes the runner-owned workspace as the runner's identity.
    payload["runtime"] = {"uid": os.getuid(), "gid": os.getgid(), "home": "/home/app"}
    return HostClass.model_validate(payload)


def _writable_plan():
    payload = _plan("opencode-go/model").payload.model_dump(mode="json", by_alias=True)
    payload["workspaceMutation"] = "allowed"
    return create_execution_plan_envelope(payload)


async def _journey(tmp_path: Path, monkeypatch, sessions):
    """Wire the production owners around one Docker daemon and storage."""

    # The worker shares the daemon's filesystem, as in a local deployment.
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)
    backend = DockerCommandBackend()
    planned = await _pulled_digest(backend, _PLANNED_TAG)
    installed = await _pulled_digest(backend, _INSTALLED_TAG)
    assert planned != installed
    # Before the update the deployment's installed host image is the planned one.
    _record_deployment_host(monkeypatch, tmp_path, planned)

    blob_root = tmp_path / "durable-artifacts"
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(blob_root)),
    )
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    artifact_sessions = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    gateway = TemporalOmnigentArtifactGateway(session_factory=artifact_sessions)
    root = tmp_path / "worker"
    root.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()

    plan = _writable_plan()
    await DbExecutionPlanStore(sessions).persist(plan)
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer
    realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
    realizer._heartbeat_interval = 0.5
    realizer._workspace_publisher = OmnigentWorkspacePublicationService(
        root, artifact_gateway=gateway
    )
    capacity = _ProviderCapacity(harness.acquired)
    realizer._provider_leases = capacity
    handle = harness.credential_handle.model_copy(update={"attachments": ()})

    class Credentials:
        async def materialize_all(self, **_kwargs):
            return (handle,)

        async def load_cleanup_handles(self, *_args):
            return (handle,)

        async def cleanup_all(self, _handles):
            return ()

    realizer._credentials = Credentials()
    drained: list[str] = []

    async def drain(session_id):
        drained.append(session_id)
        return {"sessionId": session_id, "stopped": True}

    realizer._session_cleanup = SimpleNamespace(drain=drain)

    class TurnCommands:
        async def claim(self, **_kwargs):
            return SimpleNamespace(
                owns_delivery=True, session_id="oms_generic", fencing_generation=1
            )

        async def attach_provider_session(self, **_kwargs):
            return None

        async def settle(self, **_kwargs):
            return None

    realizer._turn_commands = TurnCommands()
    host_class = _host_class(planned, (await realizer._resolve_host(plan))[0])

    async def resolve_host(_plan):
        return host_class, get_launch_policy("omnigent-on-demand@1")

    realizer._resolve_host = resolve_host
    stops = _StopAcknowledgements()
    cleanup = DockerOmnigentHostCleanupService(stops)
    server = _ControlledOmnigentServer(backend)
    inputs = _DeploymentInputs(skills)
    realizer._host_runtime = GenericOmnigentHostRuntime(
        launcher=DockerOmnigentHostLauncher(
            backend=backend, runtime_scripts=_AgentScripts(), server_url=_SERVER_URL
        ),
        workspace_service=OmnigentWorkspaceMaterializer(
            command_runner=None, workspace_root=root, artifact_service=gateway
        ),
        skill_service=inputs,
        tool_service=_Tools(skills),
        github_credential_service=_GithubCredentials(skills),
        egress_service=inputs,
        runtime_environment_service=inputs,
        registration_waiter=server,
        host_attestor=server,
        cleanup_service=cleanup,
    )
    realizer._deployment_validator = AsyncMock(return_value=None)
    monkeypatch.setattr(
        generic_host, "_STOP_RECONCILIATION_RETRY_DELAYS_SECONDS", (0, 0)
    )
    return SimpleNamespace(
        backend=backend,
        stops=stops,
        cleanup=cleanup,
        server=server,
        realizer=realizer,
        plan=plan,
        gateway=gateway,
        root=root,
        planned=planned,
        installed=installed,
        capacity=capacity,
        host_leases=harness.host_leases,
        drained=drained,
        engine=engine,
    )


async def _admitted(env, step, *, restore_ref: str | None, idempotency_key: str):
    """The AgentRun boundary's admitted request for one Step Execution."""

    task_input = b'{"objective":"finish the interrupted change"}'
    plan_bytes = b'{"steps":["prepare","analyze","implement"]}'
    locator = {
        "kind": "sandbox",
        "workspaceId": _workspace_id(step.workflow_id, step.step_execution_id),
        "relativePath": "repo",
    }
    spec = {"repository": "MoonLadderStudios/MoonMind", "workspaceLocator": locator}
    if restore_ref is not None:
        spec["workspaceCheckpointRestoreRef"] = restore_ref
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "opencode-primary",
            "correlationId": step.workflow_id,
            "idempotencyKey": idempotency_key,
            "workspaceSpec": spec,
            "parameters": {"publishMode": "none"},
            "stepExecution": step.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        }
    )
    input_ref = await env.gateway.write_bytes(
        request=request,
        name="input",
        payload=task_input,
        content_type="application/json",
        link_type="input",
    )
    plan_ref = await env.gateway.write_bytes(
        request=request,
        name="plan",
        payload=plan_bytes,
        content_type="application/json",
        link_type="input",
    )
    digest = hashlib.sha256(plan_bytes).hexdigest()
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + digest,
        planDigest="sha256:" + digest,
        planArtifactRef=plan_ref,
        taskInputSnapshotRef=input_ref,
        taskInputSnapshotDigest="sha256:" + hashlib.sha256(task_input).hexdigest(),
    )
    return request.model_copy(
        update={
            "step_execution": request.step_execution.model_copy(
                update={"omnigent_execution_plan": binding}
            )
        }
    )


def _checkout(env, step) -> Path:
    """The repository checkout the run gave Step Execution 1."""

    workspace_id = _workspace_id(step.workflow_id, step.step_execution_id)
    workspace = env.root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.name", "Journey")
    _git(workspace, "config", "user.email", "journey@example.invalid")
    (workspace / "app.py").write_text("print('base')\n")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "base")
    SandboxWorkspaceRecordStore(env.root).ensure(
        SandboxWorkspaceRecord(
            workspace_id, step.workflow_id, step.step_execution_id, "repo"
        )
    )
    return workspace


async def _cleanup_docker(env) -> None:
    for launch in env.server.launches:
        await env.backend.run(
            ["docker", "rm", "-f", str(launch["containerName"])], check=False
        )
        await env.backend.run(
            ["docker", "volume", "rm", str(launch["stateVolumeRef"])], check=False
        )


_PLAN_NODES = [
    {
        "id": node_id,
        "tool": {"type": "agent_runtime", "name": "omnigent"},
        "inputs": {"instructions": instructions, "runtime": {"mode": "omnigent"}},
    }
    for node_id, instructions in (
        ("prepare", "Prepare the change."),
        ("analyze", "Analyze the repository."),
        ("implement", "Implement the change."),
    )
]
_PLAN_EDGES = [
    {"from": "prepare", "to": "analyze"},
    {"from": "analyze", "to": "implement"},
]


async def test_update_replacing_live_agent_host_continues_only_interrupted_step(
    tmp_path, monkeypatch
):
    async with isolated_postgres(
        [OmnigentExecutionPlanRecord.__table__, OmnigentRuntimeBindingRecord.__table__]
    ) as sessions:
        env = await _journey(tmp_path, monkeypatch, sessions)
        children: list[tuple[str, int, str | None]] = []
        admitted: list[AgentExecutionRequest] = []
        observed: dict[str, object] = {}

        async def interrupted_turn(request, *, session_authority_sink):
            await session_authority_sink.session_created("session-1")
            launch = env.server.launches[-1]
            container = str(launch["containerName"])
            workspace = (
                env.root
                / "temporal_sandbox"
                / request.workspace_spec["workspaceLocator"]["workspaceId"]
                / "repo"
            )

            async def progressed() -> bool:
                return _written(workspace / "progress.txt")

            await _wait_for(progressed, message="the agent never wrote its work")
            # The update installs the new host image and replaces the Omnigent
            # server; the new server projects the old host lost although its
            # container process keeps running.
            _record_deployment_host(monkeypatch, tmp_path, env.installed)
            env.stops.lost_acknowledgements = 1
            observed["oldHostRunningWhenLost"] = await _running(env.backend, container)
            return AgentRunResult(
                summary="Omnigent session host was lost before the turn finished.",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                retryRecommendation="retry_step_execution",
                metadata={"omnigentSessionId": "session-1"},
            )

        async def successor_turn(request, *, session_authority_sink):
            await session_authority_sink.session_created("session-2")
            old, new = env.server.launches
            observed["oldHostAtSuccessorLaunch"] = observed.pop("oldHostProbe")
            workspace = (
                env.root
                / "temporal_sandbox"
                / request.workspace_spec["workspaceLocator"]["workspaceId"]
                / "repo"
            )

            async def finished() -> bool:
                return _written(workspace / "result.txt")

            await _wait_for(finished, message="the successor agent never finished")
            observed["successorResult"] = (workspace / "result.txt").read_text()

            # A late cleanup from the old owner, holding a stale handle that
            # names the live successor, is fenced by the container labels.
            with pytest.raises(HarnessPlatformError) as stale:
                await env.cleanup.cleanup(
                    container_name=str(new["containerName"]),
                    state_volume_ref=str(new["stateVolumeRef"]),
                    host_lease_ref=str(old["hostLeaseRef"]),
                    host_lease_generation=int(old["hostLeaseGeneration"]),
                )
            observed["staleCleanupCode"] = stale.value.code
            # A late redelivery of the old attempt returns its recorded result
            # and neither launches, stops, nor releases the successor.
            late = await env.realizer._execute_lifecycle(admitted[0], env.plan)
            observed["lateRedelivery"] = late.provider_error_code
            observed["successorRunningAfterLateOwner"] = await _running(
                env.backend, str(new["containerName"])
            )
            observed["successorVolumeAfterLateOwner"] = await _volume_exists(
                env.backend, str(new["stateVolumeRef"])
            )
            observed["successorLeaseAfterLateOwner"] = (
                await env.host_leases.get(str(new["hostLeaseRef"]))
            ).status
            observed["capacityHeldAfterLateOwner"] = env.capacity.held
            observed["launchesAfterLateOwner"] = len(env.server.launches)
            return AgentRunResult(
                summary="The interrupted change finished on the installed image.",
                metadata={"omnigentSessionId": "session-2"},
            )

        original_realize = env.realizer._host_runtime.realize

        async def realize(**kwargs):
            if env.server.launches:
                # Probe the lost host at the moment the successor launches.
                observed["oldHostProbe"] = await _running(
                    env.backend, str(env.server.launches[0]["containerName"])
                )
            return await original_realize(**kwargs)

        env.realizer._host_runtime.realize = realize

        async def agent_run(run_request: AgentExecutionRequest) -> AgentRunResult:
            step = run_request.step_execution
            children.append((step.logical_step_id, step.execution_ordinal, step.reason))
            if step.logical_step_id != "implement":
                return AgentRunResult(summary=f"{step.logical_step_id} completed.")
            request = await _admitted(
                env,
                step,
                restore_ref=(run_request.workspace_spec or {}).get(
                    "workspaceCheckpointRestoreRef"
                ),
                idempotency_key=run_request.idempotency_key,
            )
            admitted.append(request)
            if step.execution_ordinal == 1:
                _checkout(env, step)
                env.realizer._session_driver = interrupted_turn
            else:
                env.realizer._session_driver = successor_turn
            return await env.realizer._execute_lifecycle(request, env.plan)

        try:
            workflow, run_requests, manifests = await _drive_omnigent_step_attempts(
                monkeypatch,
                agent_run,
                enabled_patches=set(_HOST_LOSS_RETRY_PATCHES),
                nodes=_PLAN_NODES,
                edges=_PLAN_EDGES,
            )

            # Completed steps ran once; only the interrupted step continued, as
            # a recovered Step Execution of the same logical step.
            assert [(name, ordinal) for name, ordinal, _ in children] == [
                ("prepare", 1),
                ("analyze", 1),
                ("implement", 1),
                ("implement", 2),
            ]
            assert children[-1][2] == "runtime_recovered"
            assert workflow._step_execution_for("prepare") == 1
            assert workflow._step_execution_for("analyze") == 1
            assert workflow._step_execution_for("implement") == 2

            # The old host was still running when it was reported lost. Its
            # removal acknowledgement was lost, the realizer resumed the stop,
            # and the successor launched only after the container was gone.
            assert observed["oldHostRunningWhenLost"] is True
            assert env.stops.lost_acknowledgements == 0
            old, new = env.server.launches
            assert env.stops.removals[0] == old["containerName"]
            assert observed["oldHostAtSuccessorLaunch"] is None

            # The successor is a fresh launch on the newly installed image; the
            # lost attempt keeps the image it actually ran on.
            assert (old["launchImageRef"], old["preferInstalledImage"]) == (
                env.planned,
                False,
            )
            assert (new["launchImageRef"], new["preferInstalledImage"]) == (
                env.installed,
                True,
            )
            assert old["containerName"] != new["containerName"]

            # Verified saved bytes crossed the host removal: the successor's
            # own process read them before finishing.
            lost_result = await DbRuntimeBindingStore(sessions).get(
                _binding_ref(env, admitted[0])
            )
            saved = lost_result.phaseResults["saved"]
            archive = await env.gateway.read_bytes(saved["archiveRef"])
            assert "sha256:" + hashlib.sha256(archive).hexdigest() == (
                saved["archiveDigest"]
            )
            restore_ref = admitted[1].workspace_spec["workspaceCheckpointRestoreRef"]
            assert restore_ref == saved["archiveRef"]
            assert observed["successorResult"] == _FINISHED_LINE + "\n"
            successor_workspace = (
                env.root
                / "temporal_sandbox"
                / admitted[1].workspace_spec["workspaceLocator"]["workspaceId"]
                / "repo"
            )
            assert (successor_workspace / "progress.txt").read_text() == (
                _SAVED_LINE + "\n"
            )
            (recovery,) = {
                json.dumps(manifest["workspace"]["runtimeLossRecovery"], sort_keys=True)
                for manifest in _terminal_manifests(manifests, 2)
                if manifest["logicalStepId"] == "implement"
            }
            recovery = json.loads(recovery)
            assert recovery["mode"] == "restore_saved_checkpoint"
            assert recovery["checkpointRef"] == saved["archiveRef"]
            assert recovery["checkpointDigest"] == saved["archiveDigest"]

            # Late old-owner effects could not touch the live successor.
            assert observed["staleCleanupCode"] == "OMNIGENT_RUNTIME_BINDING_CONFLICT"
            assert observed["lateRedelivery"] == "OMNIGENT_SESSION_HOST_LOST"
            assert observed["successorRunningAfterLateOwner"] is True
            assert observed["successorVolumeAfterLateOwner"] is True
            assert observed["successorLeaseAfterLateOwner"] == "ready"
            assert observed["capacityHeldAfterLateOwner"] == 1
            assert observed["launchesAfterLateOwner"] == 2

            # Both attempts ended cleaned, with nothing left on the daemon.
            # The resumed stop drains the lost session again; draining is
            # idempotent and no other session was ever stopped.
            assert list(dict.fromkeys(env.drained)) == ["session-1", "session-2"]
            assert env.capacity.held == 0
            for launch in (old, new):
                lease = await env.host_leases.get(str(launch["hostLeaseRef"]))
                assert lease.status == "cleaned"
                assert await _running(env.backend, str(launch["containerName"])) is None
                assert not await _volume_exists(
                    env.backend, str(launch["stateVolumeRef"])
                )
            for request in admitted:
                binding = await DbRuntimeBindingStore(sessions).get(
                    _binding_ref(env, request)
                )
                assert binding.state is RuntimeBindingState.cleaned
            assert len(run_requests) == 4
        finally:
            await _cleanup_docker(env)
            await env.engine.dispose()


async def test_unconfirmed_stop_of_live_host_withholds_successor_until_removed(
    tmp_path, monkeypatch
):
    """A stop that cannot be confirmed never yields parallel compute.

    The Docker daemon does not answer the removal of the still-running lost
    host. The realizer resumes the stop within its bound, then withholds the
    successor and leaves the binding to the janitor with the workspace saved.
    A replacement worker later finishes that same stop and only then returns
    the recorded retry authority, without starting another turn.
    """

    async with isolated_postgres(
        [OmnigentExecutionPlanRecord.__table__, OmnigentRuntimeBindingRecord.__table__]
    ) as sessions:
        env = await _journey(tmp_path, monkeypatch, sessions)
        step_execution_id = "wf-unconfirmed-stop:run-1:implement:execution:1"
        identity = AgentRuntimeStepExecutionLaunch.model_validate(
            {
                "workflowId": "wf-unconfirmed-stop",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": step_execution_id,
                "runtimeContextPolicy": "fresh_agent_run",
            }
        )
        request = await _admitted(
            env,
            identity,
            restore_ref=None,
            idempotency_key=f"{step_execution_id}:agent_execute",
        )
        workspace = _checkout(env, identity)
        turns: list[str] = []

        async def lost_turn(_request, *, session_authority_sink):
            await session_authority_sink.session_created("session-1")
            turns.append("session-1")

            async def progressed() -> bool:
                return _written(workspace / "progress.txt")

            await _wait_for(progressed, message="the agent never wrote its work")
            # Every removal attempt of this lost attempt goes unanswered.
            env.stops.refused_removals = 3
            return AgentRunResult(
                summary="Omnigent session host was lost before the turn finished.",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                retryRecommendation="retry_step_execution",
                metadata={"omnigentSessionId": "session-1"},
            )

        env.realizer._session_driver = lost_turn
        try:
            withheld = await env.realizer._execute_lifecycle(request, env.plan)

            (launch,) = env.server.launches
            container = str(launch["containerName"])
            assert withheld.retry_recommendation == "delegate_to_janitor"
            assert withheld.provider_error_code == "OMNIGENT_CLEANUP_DEFERRED"
            assert withheld.metadata["successorAuthority"] == "withheld"
            assert withheld.metadata["workPreserved"] is True
            assert env.stops.refused_removals == 0
            # The lost host is genuinely still running; nothing was released.
            assert await _running(env.backend, container) is True
            lease = await env.host_leases.get(str(launch["hostLeaseRef"]))
            assert lease.status == "cleanup_pending"
            binding = await DbRuntimeBindingStore(sessions).get(
                _binding_ref(env, request)
            )
            assert binding.state is RuntimeBindingState.cleanup_pending

            async def no_new_turn(*_args, **_kwargs):
                raise AssertionError("a finished attempt must not start a turn")

            env.realizer._session_driver = no_new_turn
            resumed = await env.realizer._execute_lifecycle(request, env.plan)

            assert resumed.retry_recommendation == "retry_step_execution"
            assert resumed.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
            assert resumed.metadata["savedWorkspaceCheckpoint"] == (
                withheld.metadata["savedWorkspaceCheckpoint"]
            )
            assert await _running(env.backend, container) is None
            assert not await _volume_exists(env.backend, str(launch["stateVolumeRef"]))
            lease = await env.host_leases.get(str(launch["hostLeaseRef"]))
            assert lease.status == "cleaned"
            assert turns == ["session-1"]
            assert len(env.server.launches) == 1
        finally:
            await _cleanup_docker(env)
            await env.engine.dispose()
