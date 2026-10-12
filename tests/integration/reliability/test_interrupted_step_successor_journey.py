"""MoonMind#4627: an update interrupts a running step and the workflow completes.

MoonMind.Run executes three agent steps through the real MoonMind.AgentRun
child, the production Omnigent execute Activity and the generic host realizer.
Steps one and two complete. While step three runs, its agent host container is
removed and its Activity worker is shut down, as a Compose update recreating
services does. The replacement worker observes the lost host; the realizer
saves the caller-owned workspace through the real publisher, confirms the old
host's removal and returns ``retry_step_execution``. Run starts one successor
Step Execution that launches on the newly installed image and restores the
verified saved bytes, and the workflow completes without repeating steps one
and two.

Only the provider turn is controlled. Temporal, PostgreSQL bindings, Docker
containers, artifact bytes, workspace save/restore and finalization are real.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RawValue
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db import models
from api_service.db.models import (
    Base,
    OmnigentCredentialRuntimeRecord,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.bootstrap import store as bootstrap_store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupAuthority
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.interrupted_attempt import is_recovered_successor
from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.integration.reliability.test_release_routing_journey import connect
from tests.integration.services.temporal.workflows.test_agent_run import (
    MockProviderProfileManager,
)
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent.test_capacity_readmission_turn import install_credential_ledger
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _mock_resilience_policy_envelope,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_STEPS = ("prepare", "implement", "finish")
_PROFILE = "opencode-go-primary"
# Two tags of one repository give the predecessor's image and the image the
# update installs: same repository, different digests, both locally cached.
_PREDECESSOR_TAG = "alpine:3.20"
_INSTALLED_TAG = "alpine:3.21"


@workflow.defn
class InterruptedStepRun:
    """MoonMind.Run's real execution stage over a three-step plan."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        parent = run_module.MoonMindRunWorkflow()
        parent._owner_id = "owner-restartability"
        parent._repo = "example/repository"
        parent._integration = None
        # Visibility is independent of the step lifecycle under test.
        parent._update_memo = lambda: None
        parent._update_search_attributes = lambda: None
        parent._mark_real_work_started = lambda **_kwargs: None
        await parent._run_execution_stage(
            parameters=payload["parameters"], plan_ref=payload["planRef"]
        )
        return {
            "ledger": {
                step: parent._step_ledger_row_for(step).get("status") for step in _STEPS
            }
        }


async def _image_digest(backend: DockerCommandBackend, tag: str) -> str:
    inspect = [
        "docker",
        "image",
        "inspect",
        tag,
        "--format",
        "{{index .RepoDigests 0}}",
    ]
    code, ref, _ = await backend.run(inspect, check=False)
    if code != 0:
        await backend.run(["docker", "pull", tag])
        _, ref, _ = await backend.run(inspect)
    return ref.strip()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    "host_replaced", [True, False], ids=["host-lost", "observer-only"]
)
async def test_update_interrupts_active_step_and_successor_completes_workflow(
    tmp_path, monkeypatch, host_replaced
):
    connected = await connect()
    client = Client(
        connected.service_client,
        namespace=connected.namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    queue = "interrupted-successor-" + uuid4().hex
    backend = DockerCommandBackend()
    cleanup = DockerOmnigentHostCleanupService(backend)
    predecessor_image = await _image_digest(backend, _PREDECESSOR_TAG)
    installed_image = await _image_digest(backend, _INSTALLED_TAG)
    assert predecessor_image != installed_image

    # The deployment's installed runtime, as bootstrap records it. Before the
    # update it is the predecessor's image; the update installs the new one.
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "resolved.json")
    )
    for key in (
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "OMNIGENT_PI_HOST_IMAGE_REF",
    ):
        monkeypatch.delenv(key, raising=False)

    def install(image: str) -> None:
        bootstrap_store.save_resolved_state(
            ResolvedOmnigentDeploymentState.model_validate(
                {
                    "serverImageRef": "ghcr.io/omnigent-ai/omnigent-server@sha256:"
                    + "c" * 64,
                    "sharedHostImageRef": image,
                    "omnigentBuildDigest": "sha256:" + "c" * 64,
                    "architecture": "linux/amd64",
                    "details": {
                        "hostImageProvenance": {
                            image: {
                                "buildDigest": "sha256:" + "2" * 64,
                                "version": "0.11.1",
                            }
                        }
                    },
                }
            )
        )

    install(predecessor_image)
    launcher = DockerOmnigentHostLauncher(
        backend=backend,
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url="http://omnigent:8000",
    )

    workspace_root = tmp_path / "worker"
    # Durable artifact bytes outlive both hosts and the replaced worker.
    blob_root = tmp_path / "durable-blob-volume"
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(blob_root)),
    )
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.check_call(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)]
    )
    subprocess.check_call(["git", "init", "-q", "--initial-branch=main", str(seed)])
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Restartability")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "restartability@example.invalid")
    (seed / "file.txt").write_text("base\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", str(origin), "main")

    containers: dict[str, str] = {}
    saves: dict[str, dict] = {}
    restores: dict[str, str | None] = {}
    launches: list[tuple[str, int, str]] = []
    interrupted = asyncio.Event()
    turns: list[tuple[str, int]] = []

    def workspace_of(request) -> Path:
        locator = request.workspace_spec["workspaceLocator"]
        return (
            workspace_root
            / "temporal_sandbox"
            / locator["workspaceId"]
            / locator.get("relativePath", "repo")
        )

    async with isolated_postgres(
        [
            OmnigentExecutionPlanRecord.__table__,
            OmnigentRuntimeBindingRecord.__table__,
            OmnigentCredentialRuntimeRecord.__table__,
            *(
                table
                for table in Base.metadata.sorted_tables
                if table.name
                in {
                    "omnigent_chat_binding_aliases",
                    "omnigent_cleanup_authority",
                    "omnigent_reconciliation_decisions",
                    "omnigent_commands",
                    "omnigent_observations",
                    "omnigent_turn_attempts",
                    "omnigent_sessions",
                }
            ),
            models.TemporalArtifact.__table__,
            models.TemporalArtifactLink.__table__,
            models.TemporalArtifactPin.__table__,
            models.TemporalArtifactUseClaim.__table__,
            models.TemporalArtifactDeletionIntent.__table__,
        ]
    ) as sessions:
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
        install_credential_ledger(harness, sessions)
        plan = _plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        realizer = harness.realizer
        realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
        canonical_store = OmnigentControlPlaneStore(sessions)
        realizer._turn_commands = CanonicalTurnCommandService(canonical_store)
        realizer._cleanup_authority = CanonicalCleanupAuthority(canonical_store)
        realizer._artifacts = gateway
        realizer._workspace_publisher = OmnigentWorkspacePublicationService(
            workspace_root, artifact_gateway=gateway
        )
        save = realizer._workspace_publisher.save_request_workspace

        async def recorded_save(request):
            saved = await save(request)
            saves[request.step_execution.step_execution_id] = saved
            return saved

        realizer._workspace_publisher.save_request_workspace = recorded_save
        monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: SimpleNamespace(require=lambda _ref: realizer),
        )

        async def no_clone(*_args, **_kwargs):
            raise AssertionError("the sandbox owner already checked out the source")

        materializer = OmnigentWorkspaceMaterializer(
            command_runner=no_clone,
            workspace_root=workspace_root,
            artifact_service=gateway,
        )
        prepare = realizer._host_runtime.prepare

        async def prepare_workspace(**kwargs):
            # The sandbox owner checks out admitted source for each attempt;
            # the production materializer projects a restore checkpoint.
            request = kwargs["request"]
            step = request.step_execution
            restores[step.step_execution_id] = request.workspace_spec.get(
                "workspaceCheckpointRestoreRef"
            )
            workspace = workspace_of(request)
            if not workspace.exists():
                workspace.parent.mkdir(parents=True)
                await asyncio.to_thread(
                    subprocess.check_call,
                    ["git", "clone", "-q", str(origin), str(workspace)],
                )
                SandboxWorkspaceRecordStore(workspace_root).ensure(
                    SandboxWorkspaceRecord(
                        workspace_id=request.workspace_spec["workspaceLocator"][
                            "workspaceId"
                        ],
                        workflow_id=step.workflow_id,
                        step_execution_id=step.step_execution_id,
                        relative_path="repo",
                    )
                )
            if request.workspace_spec.get("workspaceCheckpointRestoreRef"):
                owner = workspace.stat()
                await materializer.materialize(
                    request, runtime_uid=owner.st_uid, runtime_gid=owner.st_gid
                )
            return await prepare(**kwargs)

        realize = realizer._host_runtime.realize

        async def launch_host(**kwargs):
            # Admission selected the predecessor's image. The production
            # launch-image decision runs on the real Docker image cache; the
            # stand-in host is an inert container of the chosen image.
            request, host_class = kwargs["request"], kwargs["host_class"]
            image = await launcher._resolve_launch_image(
                predecessor_image,
                host_class,
                prefer_installed=is_recovered_successor(request),
            )
            step = request.step_execution
            ref, generation = kwargs["host_lease_ref"], kwargs["host_lease_generation"]
            name = "moonmind-test-successor-" + uuid4().hex[:12]
            volume = name + "-state"
            await kwargs["authority_sink"](
                {
                    "kind": "host",
                    "containerName": name,
                    "stateVolumeRef": volume,
                    "launchGeneration": generation,
                }
            )
            labels = [
                "--label",
                "moonmind.host_lease_ref=" + ref,
                "--label",
                "moonmind.host_lease_generation=" + str(generation),
            ]
            await backend.run(["docker", "volume", "create", *labels, volume])
            await backend.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    "none",
                    *labels,
                    "--mount",
                    f"type=volume,src={volume},dst=/owned-state",
                    image,
                    "sleep",
                    "600",
                ]
            )
            containers[step.step_execution_id] = name
            launches.append((step.logical_step_id, step.execution_ordinal, image))
            return {
                **await realize(**kwargs),
                "containerName": name,
                "stateVolumeRef": volume,
            }

        async def remove_host(**kwargs):
            context = kwargs["host_context"]
            return await cleanup.cleanup(
                container_name=context["containerName"],
                state_volume_ref=context["stateVolumeRef"],
                host_lease_ref=kwargs["host_lease_ref"],
                host_lease_generation=kwargs["host_lease_generation"],
            )

        async def container_running(name: str) -> bool:
            code, out, _ = await backend.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    name,
                ],
                check=False,
            )
            return code == 0 and out.strip() == "true"

        async def provider_turn(request, *, session_authority_sink):
            step = request.step_execution
            session_id = "session-" + step.step_execution_id.rsplit(":", 1)[-1]
            await session_authority_sink.session_created(
                f"{step.logical_step_id}-{session_id}"
            )
            turns.append((step.logical_step_id, step.execution_ordinal))
            workspace = workspace_of(request)
            name = containers[step.step_execution_id]
            if step.logical_step_id != "finish":
                (workspace / f"{step.logical_step_id}.txt").write_text("done\n")
                return AgentRunResult(summary=f"{step.logical_step_id} completed")
            if step.execution_ordinal == 1:
                if not interrupted.is_set():
                    # Unsaved progress, then the update replaces this worker.
                    (workspace / "file.txt").write_text("interrupted candidate\n")
                    (workspace / "new.bin").write_bytes(b"\x00\xff\x80")
                    interrupted.set()
                    while True:
                        activity.heartbeat("provider turn running")
                        await asyncio.sleep(0.2)
                if await container_running(name):
                    # Only the observer was replaced: the same healthy turn
                    # finishes on its original host.
                    (workspace / "finish.txt").write_text("same session completed\n")
                    return AgentRunResult(summary="finish completed on its host")
                # The production execution boundary's typed host-loss result.
                return AgentRunResult(
                    summary="The Omnigent session host was lost.",
                    failureClass="integration_error",
                    providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                    retryRecommendation="retry_step_execution",
                )
            # The successor continues from the restored saved work.
            assert (workspace / "file.txt").read_text() == "interrupted candidate\n"
            assert (workspace / "new.bin").read_bytes() == b"\x00\xff\x80"
            (workspace / "finish.txt").write_text("successor completed\n")
            return AgentRunResult(summary="finish completed on the installed runtime")

        async def drain(_session_id):
            return {"stopped": True}

        realizer._host_runtime.prepare = prepare_workspace
        realizer._host_runtime.realize = launch_host
        realizer._host_runtime.cleanup = remove_host
        realizer._session_driver = provider_turn
        realizer._session_cleanup = SimpleNamespace(drain=drain)

        catalog = build_default_activity_catalog()
        selected = TemporalActivityCatalog(
            activities=tuple(
                a
                for a in catalog.activities
                if a.activity_type == "integration.omnigent.profile_bound_execute"
            ),
            fleets=catalog.fleets,
        )
        (binding,) = build_activity_bindings(
            selected, agent_runtime_activities=TemporalAgentRuntimeActivities()
        )
        original_options = MoonMindAgentRun._execute_kwargs_for_route

        def isolated_options(route):
            return {
                **original_options(route),
                "task_queue": queue
                + (
                    "-activity"
                    if route.activity_type
                    == "integration.omnigent.profile_bound_execute"
                    else "-control"
                ),
            }

        monkeypatch.setattr(
            MoonMindAgentRun,
            "_execute_kwargs_for_route",
            staticmethod(isolated_options),
        )
        monkeypatch.setattr(
            MoonMindAgentRun,
            "_manager_workflow_id",
            staticmethod(lambda _runtime: queue + "-manager"),
        )
        monkeypatch.setattr(
            agent_run_module,
            "STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT",
            timedelta(seconds=5),
        )
        run_catalog = run_module.DEFAULT_ACTIVITY_CATALOG
        monkeypatch.setattr(
            run_module,
            "DEFAULT_ACTIVITY_CATALOG",
            SimpleNamespace(
                resolve_activity=lambda name: replace(
                    run_catalog.resolve_activity(name), task_queue=queue + "-control"
                )
            ),
        )
        monkeypatch.setattr(
            run_module.MoonMindRunWorkflow,
            "_workflow_child_task_queue",
            lambda _: queue,
        )

        plan_payload = {
            "plan_version": "1.0",
            "metadata": {
                "title": "Restartability",
                "created_at": "2026-10-12T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:123",
                    "artifact_ref": "art:sha256:456",
                },
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "nodes": [
                {
                    "id": step,
                    "tool": {"type": "agent_runtime", "name": "omnigent"},
                    "inputs": {
                        "instructions": f"Run the {step} step.",
                        "runtime": {
                            "mode": "omnigent",
                            "executionProfileRef": _PROFILE,
                        },
                        "publishMode": "none",
                    },
                }
                for step in _STEPS
            ],
            "edges": [
                {"from": "prepare", "to": "implement"},
                {"from": "implement", "to": "finish"},
            ],
        }
        controls: list[tuple[str, object]] = []
        evidence_request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId=queue,
            idempotencyKey=queue + ":step-checkpoints",
        )

        class SandboxCheckpoints(TemporalSandboxActivities):
            async def _put_checkpoint_bytes(
                self, payload, *, content_type, metadata=None
            ):
                ref = await gateway.write_bytes(
                    request=evidence_request,
                    name=str((metadata or {}).get("artifact_kind") or "checkpoint"),
                    payload=payload,
                    content_type=content_type,
                    link_type="evidence.recovery",
                )
                return "artifact://" + ref.removeprefix("artifact://")

        checkpoints = SandboxCheckpoints(workspace_root=workspace_root)

        @activity.defn(dynamic=True)
        async def control(args: Sequence[RawValue]):
            name = activity.info().activity_type
            payload = (
                activity.payload_converter().from_payload(args[0].payload)
                if args
                else None
            )
            controls.append((name, payload))
            # MoonMind.Run bookkeeping outside the step handoff under test.
            if name == "artifact.read":
                return json.dumps(plan_payload).encode("utf-8")
            if name == "plan.validate":
                return payload["plan_ref"]
            if name == "resilience.compile_policy":
                return _mock_resilience_policy_envelope(payload)
            if name == "workspace.capture_checkpoint":
                # Run's own step-boundary capture through the sandbox owner.
                return await checkpoints.workspace_capture_checkpoint(payload)
            if name in {"step_checkpoint.create", "step_checkpoint.create_v2"}:
                return {
                    "checkpointRef": await gateway.write_json(
                        request=evidence_request,
                        name="step-checkpoint.json",
                        payload=payload,
                        link_type="evidence.recovery",
                    )
                }
            if name == "agent_skill.resolve":
                # The steps select no Skills; the empty snapshot is recorded.
                return {"snapshotId": "restartability-no-skills", "skills": []}
            if name in {"artifact.create", "artifact.write_complete"}:
                artifact_id = (payload or {}).get("artifact_id") or (
                    "art_restartability_%d" % len(controls)
                )
                return {
                    "artifact_ref_v": 1,
                    "artifact_id": artifact_id,
                    "sha256": None,
                    "size_bytes": None,
                    "content_type": "application/json",
                    "encryption": "none",
                }
            if name == "integration.resolve_adapter_metadata":
                return {
                    "agent_id": "omnigent",
                    "execution_style": "streaming_gateway",
                    "supports_callbacks": False,
                }
            if name == "omnigent.evaluate_session_admission":
                return (
                    _admission(profile_ref=_PROFILE)
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
                "Unexpected control Activity: " + name, non_retryable=True
            )

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

        first = Worker(
            client,
            task_queue=queue + "-activity",
            activities=[binding.handler],
            graceful_shutdown_timeout=timedelta(0),
        )
        first_task = asyncio.create_task(first.run())
        try:
            async with Worker(
                client,
                task_queue=queue,
                workflows=[
                    InterruptedStepRun,
                    MoonMindAgentRun,
                    MockProviderProfileManager,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ), Worker(client, task_queue=queue + "-control", activities=[control]):
                handle = await client.start_workflow(
                    InterruptedStepRun.run,
                    {
                        "planRef": "artifact://restartability-plan",
                        "parameters": {
                            "publishMode": "none",
                            "omnigentExecutionPlan": {
                                "planRef": plan.planRef,
                                "planDigest": "sha256:"
                                + plan.planRef.rsplit(":", 1)[-1],
                                "planArtifactRef": "artifact:restartability-plan",
                                "taskInputSnapshotRef": "artifact:restartability-input",
                                "taskInputSnapshotDigest": "sha256:" + "2" * 64,
                            },
                        },
                    },
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=240),
                )
                started = asyncio.create_task(interrupted.wait())
                ended = asyncio.create_task(handle.result())
                await asyncio.wait(
                    {started, ended}, timeout=120, return_when=asyncio.FIRST_COMPLETED
                )
                if not started.done():
                    started.cancel()
                    # Surface why the workflow ended before step three ran.
                    raise AssertionError(
                        f"step three never ran: {await ended if ended.done() else 'timeout'}"
                    )
                ended.cancel()
                # The update: the Activity worker process is replaced, the new
                # image is installed, and the running agent host is removed.
                await first.shutdown()
                await asyncio.gather(first_task)
                install(installed_image)
                if host_replaced:
                    (finish_host,) = (
                        name
                        for step_id, name in containers.items()
                        if ":finish:" in step_id
                    )
                    await backend.run(["docker", "rm", "-f", finish_host])
                # The replacement reads durable binding authority afresh.
                realizer._runtime_bindings = DbRuntimeBindingStore(sessions)
                async with Worker(
                    client, task_queue=queue + "-activity", activities=[binding.handler]
                ):
                    outcome = await handle.result()

                await Replayer(
                    workflows=[
                        InterruptedStepRun,
                        MoonMindAgentRun,
                        MockProviderProfileManager,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(await handle.fetch_history())
                # Every attempt's host is gone before cleanup below: an
                # interrupted one removed by the update, the others by their
                # own finalization.
                for name in containers.values():
                    code, _, _ = await backend.run(
                        ["docker", "container", "inspect", name], check=False
                    )
                    assert code != 0, name
        finally:
            if "manager" in locals():
                await manager.terminate(reason="restartability journey finished")
            if not first_task.done():
                await first.shutdown()
            await asyncio.gather(first_task, return_exceptions=True)
            for name in containers.values():
                await backend.run(["docker", "rm", "-f", name], check=False)
                await backend.run(
                    ["docker", "volume", "rm", name + "-state"], check=False
                )

        # The workflow completed and steps one and two ran exactly once. The
        # replacement worker reattached to the same accepted attempt; only a
        # confirmed host loss produced a successor, on the newly installed image.
        assert set(outcome["ledger"].values()) <= {"completed", "succeeded"}, outcome
        attempts = [("prepare", 1), ("implement", 1), ("finish", 1)]
        images = [predecessor_image] * 3
        if host_replaced:
            attempts.append(("finish", 2))
            images.append(installed_image)
        assert [(step, ordinal) for step, ordinal, _ in launches] == attempts
        assert [image for *_, image in launches] == images
        assert turns == [*attempts[:3], ("finish", 1), *attempts[3:]]
        if not host_replaced:
            assert not any(restores.values())
        else:
            # The successor restored the predecessor's verified saved archive.
            (predecessor_id,) = (k for k in saves if k.endswith(":finish:execution:1"))
            (successor_id,) = (k for k in restores if k.endswith(":finish:execution:2"))
            saved = saves[predecessor_id]
            assert restores[successor_id] == saved["archiveRef"]
            assert (
                "sha256:"
                + hashlib.sha256(
                    await gateway.read_bytes(saved["archiveRef"])
                ).hexdigest()
                == saved["archiveDigest"]
            )
            assert not any(
                ref for step_id, ref in restores.items() if step_id != successor_id
            )
