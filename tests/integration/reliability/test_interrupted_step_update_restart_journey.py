"""An update replaces the worker and agent host mid-step; the step restarts (#4627).

The real ``MoonMindRunWorkflow`` execution stage runs a three-step plan in a
worker OS process. The workflow's admitted execution plan pins the host image
installed when it started. After two steps complete, the third step's agent
host process is running and has written work into its workspace. The update
then records a newer installed host image in the deployment's resolved-images
store, kills that worker process and the host process outright, and starts a
replacement worker process. The pre-update image stays in the local image
cache, as it does after a Compose pull.

Temporal redelivers the interrupted attempt to the replacement worker. That
observer reattaches through the real generic realizer, finds the host gone,
and its real cleanup saves the surviving workspace through the production
workspace publication service and artifact store before releasing the host.
The Run workflow starts one successor Step Execution under the same logical
step and admitted plan, which restores those verified bytes into its own fresh
workspace after the old workspace was removed. Production selection launches
it on the newly installed image although the plan's image is still cached, and
it completes the original workflow. The two completed steps never run again.

Real: separate worker processes and agent host processes (killed with SIGKILL),
Temporal, the Run workflow's retry loop and restart handoff, the generic
realizer lifecycle/cleanup/reconciliation and janitor confirmation, host image
selection (the planned-host resolver's Host Class selection over the
deployment's resolved-images store and the host launcher's image resolution),
the runtime-binding and host-lease stores, git workspaces, workspace
save/restore and the artifact store. Controlled stand-ins: the agent provider
(a small process that writes or checks files), started in place of a host
container from the image the launcher resolved, the Omnigent session transport
that observes it, the local image cache that the launcher queries, the
``MoonMind.AgentRun`` child (one heartbeating Activity), and provider/credential
leases.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

CONFIG_ENV = "MOONMIND_INTERRUPTED_STEP_JOURNEY_CONFIG"
IMAGE_A = "ghcr.io/example/opencode@sha256:" + "a" * 64
IMAGE_B = "ghcr.io/example/opencode@sha256:" + "b" * 64
BUILD_A = "sha256:" + "1" * 64
BUILD_B = "sha256:" + "2" * 64
STEP_IDS = ("prepare", "analyze", "implement")
INTERRUPTED_STEP = "implement"
SAVED_FILE = "interrupted-work.txt"
COMPLETED_FILE = "completed.txt"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


# ---------------------------------------------------------------------------
# Shared durable evidence
# ---------------------------------------------------------------------------


def _record(ledger: Path, **event: Any) -> None:
    event.setdefault("pid", os.getpid())
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _events(ledger: Path, kind: str | None = None) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    events = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [event for event in events if kind is None or event["event"] == kind]


def _process_alive(pid: int) -> bool:
    """True while ``pid`` runs; a zombie has already stopped."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def _tables() -> list[Any]:
    from api_service.db import models

    return [
        models.OmnigentExecutionPlanRecord.__table__,
        models.OmnigentRuntimeBindingRecord.__table__,
        models.OmnigentHostBindingRecordV2.__table__,
        models.OmnigentHostLeaseRecordV2.__table__,
        models.TemporalArtifact.__table__,
        models.TemporalArtifactLink.__table__,
        models.TemporalArtifactPin.__table__,
        models.TemporalArtifactUseClaim.__table__,
        models.TemporalArtifactDeletionIntent.__table__,
    ]


def _session_factory(config: dict[str, Any]):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    schema = config.get("databaseSchema")
    engine = create_async_engine(
        config["databaseUrl"],
        **(
            {"connect_args": {"server_settings": {"search_path": schema}}}
            if schema
            else {}
        ),
    )
    return async_sessionmaker(engine, expire_on_commit=False)


def _use_artifact_blobs(blob_root: Path) -> None:
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore,
        TemporalArtifactService,
    )

    TemporalArtifactService._build_store_from_settings = staticmethod(  # type: ignore[method-assign]
        lambda: LocalTemporalArtifactStore(blob_root)
    )


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *args], text=True
    ).strip()


# ---------------------------------------------------------------------------
# Controlled agent provider: the process a launched host runs
# ---------------------------------------------------------------------------

_HOST_SCRIPT = r"""
import os, sys, time
from pathlib import Path
workspace, mode, image, ordinal, ready = sys.argv[1:6]
workspace = Path(workspace)
if mode == "hold":
    (workspace / "interrupted-work.txt").write_text(
        f"saved by attempt {ordinal} on {image}\n")
    Path(ready).write_text(str(os.getpid()))
    while True:
        time.sleep(1)
if mode == "die":
    (workspace / f"attempt-{ordinal}.txt").write_text(f"attempt {ordinal}\n")
    Path(ready).write_text(str(os.getpid()))
    os._exit(137)
restored = (workspace / "interrupted-work.txt").read_text()
(workspace / "completed.txt").write_text(f"completed on {image} from: {restored}")
Path(ready).write_text(str(os.getpid()))
"""


def _host_mode(config: dict[str, Any], ordinal: int) -> str:
    if config["scenario"] == "exhausted":
        return "die"
    return "hold" if ordinal == 1 else "complete"


# ---------------------------------------------------------------------------
# Workflows hosted by the worker process
# ---------------------------------------------------------------------------

_WORKER_QUEUE: dict[str, str] = {}


@workflow.defn(name="MoonMind.AgentRun", sandboxed=False)
class AgentRunStandIn:
    """One heartbeating agent-attempt Activity in place of the AgentRun child."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "reliability.interrupted_step.agent_attempt",
            request,
            task_queue=_WORKER_QUEUE["name"],
            start_to_close_timeout=timedelta(minutes=3),
            heartbeat_timeout=timedelta(seconds=3),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=200),
                maximum_attempts=4,
            ),
        )


@workflow.defn(name="ReliabilityInterruptedStepRun", sandboxed=False)
class InterruptedStepRun:
    """Run the real Run workflow execution stage over a three-step plan."""

    def __init__(self) -> None:
        from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

        self._run = MoonMindRunWorkflow()

    @workflow.signal
    def cancel(self) -> None:
        self._run._cancel_requested = True

    @workflow.run
    async def run(self) -> dict[str, Any]:
        wf = self._run
        wf._owner_id = "owner-1"
        wf._repo = "MoonLadderStudios/MoonMind"
        wf._integration = None

        async def passthrough(request):
            return request

        async def no_manifest(*_args, **_kwargs):
            return None

        wf._maybe_bind_workflow_scoped_session = passthrough
        wf._record_step_execution_manifest = no_manifest
        try:
            await wf._run_execution_stage(
                parameters={"publishMode": "none"}, plan_ref="plan-ref"
            )
        except ApplicationError:
            raise
        except Exception as exc:
            # The Run workflow's terminal step failure fails this run.
            raise ApplicationError(str(exc), non_retryable=True) from exc
        return {
            "cancelRequested": wf._cancel_requested,
            "stepExecutions": {
                step_id: wf._step_execution_for(step_id) for step_id in STEP_IDS
            },
        }


# ---------------------------------------------------------------------------
# Worker process: real Run workflow, realizer, stores and workspaces
# ---------------------------------------------------------------------------


def _worker_main() -> None:
    config = json.loads(os.environ[CONFIG_ENV])
    asyncio.run(_run_worker(config))


async def _run_worker(config: dict[str, Any]) -> None:  # noqa: C901
    from temporalio import activity
    from temporalio.client import Client
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from moonmind.config.settings import settings
    from moonmind.omnigent.activity_ownership import current_delivery_owner
    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.execute import (
        OmnigentSessionHostLostError,
        omnigent_activity_heartbeat,
    )
    from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
    from moonmind.omnigent.host_runtime import PreparedHostInputs
    from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunResult,
    )
    from moonmind.workflows.temporal.activities.omnigent_activities import (
        _try_generic_realizer_dispatch,
    )
    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )
    from moonmind.workflows.temporal.workflows import run as run_module
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _CachedImages,
        _generic_publication_harness,
        exact_launch_resolver,
    )

    queue = config["taskQueue"]
    root = Path(config["workspaceRoot"])
    ledger = Path(config["ledger"])
    markers = Path(config["markers"])
    _use_artifact_blobs(Path(config["blobRoot"]))
    sessions = _session_factory(config)
    gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
    publisher = OmnigentWorkspacePublicationService(root, artifact_gateway=gateway)
    # The workflow's admitted plan and the production resolver that turns it
    # into a Host Class from this worker's installed deployment state.
    plan, planned_host_resolver = exact_launch_resolver(IMAGE_A, build_digest=BUILD_A)
    # A Compose pull leaves the pre-update image in the local cache.
    launcher = DockerOmnigentHostLauncher(
        backend=_CachedImages(IMAGE_A, IMAGE_B),
        runtime_scripts=object(),
        server_url="http://omnigent:8000",
    )
    launched: dict[int, subprocess.Popen] = {}

    # The workflow's own routes name deployment queues; this isolated worker
    # serves every Activity and child workflow on one test queue instead.
    settings.temporal.address = config["temporalAddress"]
    settings.temporal.user_workflow_v2_task_queue = queue
    run_module.WORKFLOW_TASK_QUEUE = queue
    original_execute_typed_activity = run_module.execute_typed_activity

    async def execute_on_test_queue(activity_type, arg, **kwargs):
        kwargs["task_queue"] = queue
        return await original_execute_typed_activity(activity_type, arg, **kwargs)

    run_module.execute_typed_activity = execute_on_test_queue
    # The disposable Temporal server registers no MoonMind search attributes.
    run_module.workflow.upsert_search_attributes = lambda *_args, **_kwargs: None
    # Record real markers for the restart path's patches, as new histories do.
    # Unrelated later patches (profile compilation, publication, reporting)
    # need deployment Activities this journey does not cross.
    enabled_patches = {
        run_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        run_module.RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
        run_module.RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
        run_module.RUN_STEP_EXECUTION_MANIFEST_PATCH,
        run_module.RUN_INTERRUPTED_STEP_SAVED_WORK_RESTORE_PATCH,
    }
    real_patched = run_module.workflow.patched
    run_module.workflow.patched = lambda patch_id: (
        patch_id in enabled_patches and real_patched(patch_id)
    )

    class Artifacts:
        """Host attestation and cleanup evidence for this stand-in launcher."""

        async def read_bytes(self, ref: str) -> bytes:
            return (markers / ref.rsplit("/", 1)[-1]).read_bytes()

        async def write_json(self, **_kwargs):
            return "artifact://cleanup-evidence"

        async def write_text(self, **_kwargs):
            return "artifact://host-logs"

    class HostRuntime:
        """Launch the controlled provider process in place of a container."""

        async def prepare(self, *, request, **kwargs):
            await kwargs["authority_sink"](
                {
                    "kind": "skills",
                    "cleanupRef": "skill-cleanup:" + request.idempotency_key,
                }
            )
            workspace = publisher.resolve_request_workspace(request)
            restore_ref = (request.workspace_spec or {}).get(
                "workspaceCheckpointRestoreRef"
            )
            if restore_ref:
                # The update removed the old host's workspace volume: only the
                # verified saved archive can carry its work forward.
                for other in (root / "temporal_sandbox").iterdir():
                    if other.name != workspace.parent.name:
                        shutil.rmtree(other)
                await OmnigentOAuthHostRuntime(
                    client=object(),
                    workspace_root=root,
                    repository_source_root=root,
                )._apply_workspace_checkpoint_restore(
                    workspace, artifact_ref=restore_ref, artifact_gateway=gateway
                )
                _record(
                    ledger,
                    event="workspace-restored",
                    ordinal=request.step_execution.execution_ordinal,
                    restoreRef=restore_ref,
                    files=sorted(path.name for path in workspace.iterdir()),
                )
            return PreparedHostInputs(
                workspace_attachment={
                    "kind": "bind",
                    "sourceRef": str(workspace),
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                skill_attachment={
                    "kind": "bind",
                    "sourceRef": "/tmp/skills",
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                    "deliveryRef": "skill-delivery:one",
                },
                tool_attachments=(),
                egress_attestation={
                    "networkRef": "egress",
                    "attestationRef": "artifact://egress",
                },
            )

        async def realize(self, *, request, plan, prepared, host_class, **_kwargs):
            ordinal = request.step_execution.execution_ordinal
            # The launcher's own image resolution picks what to start.
            image = await launcher._resolve_launch_image(
                host_class.imageRef, host_class
            )
            workspace = Path(prepared.workspace_attachment["sourceRef"])
            ready = markers / f"host-ready-{uuid4().hex}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _HOST_SCRIPT,
                    str(workspace),
                    _host_mode(config, ordinal),
                    image,
                    str(ordinal),
                    str(ready),
                ],
                start_new_session=True,
            )
            launched[process.pid] = process
            host_id = f"host-pid-{process.pid}"
            (markers / f"attestation-{process.pid}").write_text(
                json.dumps(
                    {
                        "imageRef": image,
                        "architecture": plan.payload.hostArchitecture,
                        "omnigentBuildDigest": host_class.omnigentBuildDigest,
                        "harnessId": plan.payload.harnessId,
                        "harnessImplementationRef": (
                            plan.payload.harnessImplementationRef
                        ),
                        "omnigentHostId": host_id,
                    }
                )
            )
            _record(
                ledger,
                event="host-launched",
                hostPid=process.pid,
                image=image,
                planImage=plan.payload.hostImageRef,
                executionPlanRef=plan.planRef,
                step=request.step_execution.logical_step_id,
                ordinal=ordinal,
                ready=str(ready),
            )
            return {
                "omnigentHostId": host_id,
                "hostId": host_id,
                "containerName": host_id,
                "stateVolumeRef": f"state-{process.pid}",
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": "/workspaces/run",
                "hostHarnessAttestationRef": (
                    f"artifact://host-attestation/attestation-{process.pid}"
                ),
                "modelOptionAttestationRef": "artifact://models",
                "hostCleanupRef": f"host-cleanup:{process.pid}",
            }

        async def cleanup(self, *, host_context, host_lease_ref, **_kwargs):
            pid = int(str(host_context["containerName"]).rsplit("-", 1)[-1])
            lost_ack = markers / (
                "stop-ack-lost-"
                + hashlib.sha256(host_lease_ref.encode()).hexdigest()[:16]
            )
            if (
                config["scenario"] == "stop_ack_lost"
                and pid not in launched
                and not lost_ack.exists()
            ):
                # The stop acknowledgement for the replaced worker's host is
                # lost once: this worker cannot yet confirm that it stopped.
                lost_ack.write_text(host_lease_ref)
                _record(ledger, event="stop-ack-lost", hostPid=pid)
                raise ConnectionError("host stop acknowledgement lost")
            if _process_alive(pid):
                os.kill(pid, signal.SIGKILL)
            if pid in launched:
                launched[pid].wait()
            for _ in range(100):
                if not _process_alive(pid):
                    break
                await asyncio.sleep(0.05)
            assert not _process_alive(pid), "host stop must be confirmed"
            _record(ledger, event="host-stopped", hostPid=pid)
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
            return None

        async def cleanup_authorities(self, _authorities):
            return None

    async def session_driver(request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        host_id = request.parameters["omnigent"]["session"]["hostId"]
        pid = int(str(host_id).rsplit("-", 1)[-1])
        ordinal = request.step_execution.execution_ordinal
        _record(
            ledger,
            event="observer-attached",
            hostPid=pid,
            ordinal=ordinal,
            activityAttempt=activity.info().attempt,
        )
        announced = False
        while True:
            process = launched.get(pid)
            exit_code = process.poll() if process is not None else None
            alive = _process_alive(pid) if process is None else exit_code is None
            if exit_code == 0:
                return AgentRunResult(
                    summary="implemented",
                    metadata={"omnigentSessionId": "session-1"},
                )
            if not alive:
                raise OmnigentSessionHostLostError(
                    f"Omnigent session host {host_id} is gone with the marked "
                    "turn unfinished; retry as a new step execution",
                    offline_seconds=0.0,
                )
            if not announced:
                launches = [
                    event
                    for event in _events(ledger, "host-launched")
                    if event["hostPid"] == pid
                ]
                if launches and Path(launches[0]["ready"]).exists():
                    _record(ledger, event="host-running", hostPid=pid, ordinal=ordinal)
                    announced = True
            activity.heartbeat({"hostPid": pid})
            await asyncio.sleep(0.1)

    async def realizer():
        harness = await _generic_publication_harness({})
        built = harness.realizer
        built._runtime_bindings = DbRuntimeBindingStore(sessions)
        built._host_leases = DbOmnigentHostLeaseRepository(sessions)
        built._host_runtime = HostRuntime()
        built._resolve_host = planned_host_resolver
        built._session_driver = session_driver
        built._workspace_publisher = publisher
        built._artifacts = Artifacts()
        built._turn_commands = None
        built._execution_owner = current_delivery_owner

        async def release_from_binding(_leases):
            return None

        built._provider_leases.release_from_binding = release_from_binding
        return built

    class Registry:
        def __init__(self, realizer):
            self._realizer = realizer

        def require(self, _ref):
            return self._realizer

    def attempt_request(request: AgentExecutionRequest) -> AgentExecutionRequest:
        identity = request.step_execution
        workspace_id = hashlib.sha256(
            f"{identity.workflow_id}:{identity.step_execution_id}".encode()
        ).hexdigest()[:24]
        binding = {
            "planRef": plan.planRef,
            "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
            "planArtifactRef": config["planArtifactRef"],
            "taskInputSnapshotRef": config["taskInputRef"],
            "taskInputSnapshotDigest": config["taskInputDigest"],
        }
        workspace_spec = {
            "repository": "MoonLadderStudios/MoonMind",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
        }
        restore_ref = (request.workspace_spec or {}).get(
            "workspaceCheckpointRestoreRef"
        )
        if restore_ref:
            workspace_spec["workspaceCheckpointRestoreRef"] = restore_ref
        return AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": "opencode-primary",
                "correlationId": identity.workflow_id,
                "idempotencyKey": request.idempotency_key,
                "parameters": {"publishMode": "none"},
                "workspaceSpec": workspace_spec,
                "omnigentExecutionPlan": binding,
                "stepExecution": {
                    **identity.model_dump(by_alias=True, mode="json"),
                    "omnigentExecutionPlan": binding,
                },
            }
        )

    def materialize_checkout(request: AgentExecutionRequest) -> None:
        identity = request.step_execution
        locator = request.workspace_spec["workspaceLocator"]
        workspace = root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        if workspace.exists():
            return
        workspace.mkdir(parents=True)
        _git(workspace, "init", "-q")
        _git(workspace, "config", "user.name", "Journey")
        _git(workspace, "config", "user.email", "journey@example.invalid")
        (workspace / "README.md").write_text("base\n")
        _git(workspace, "add", ".")
        _git(workspace, "commit", "-qm", "base")
        SandboxWorkspaceRecordStore(root).ensure(
            SandboxWorkspaceRecord(
                workspace_id=locator["workspaceId"],
                workflow_id=identity.workflow_id,
                step_execution_id=identity.step_execution_id,
                relative_path="repo",
            )
        )

    @activity.defn(name="artifact.read")
    async def read_plan(_payload) -> bytes:
        nodes = [
            {
                "id": step_id,
                "tool": {"type": "agent_runtime", "name": "omnigent"},
                "inputs": {
                    "instructions": f"Run {step_id}.",
                    "runtime": {"mode": "omnigent"},
                },
            }
            for step_id in STEP_IDS
        ]
        edges = [
            {"from": STEP_IDS[index], "to": STEP_IDS[index + 1]}
            for index in range(len(STEP_IDS) - 1)
        ]
        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Interrupted update journey",
                    "created_at": "2026-10-08T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:123",
                        "artifact_ref": "art:sha256:456",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": nodes,
                "edges": edges,
            }
        ).encode()

    @activity.defn(name="reliability.interrupted_step.agent_attempt")
    async def agent_attempt(payload: dict[str, Any]) -> dict[str, Any]:
        request = AgentExecutionRequest.model_validate(payload)
        identity = request.step_execution
        _record(
            ledger,
            event="delivery",
            step=identity.logical_step_id,
            ordinal=identity.execution_ordinal,
            activityAttempt=activity.info().attempt,
            restoreRef=(request.workspace_spec or {}).get(
                "workspaceCheckpointRestoreRef"
            ),
        )
        if identity.logical_step_id != INTERRUPTED_STEP:
            return AgentRunResult(
                summary=f"{identity.logical_step_id} done"
            ).model_dump(by_alias=True, mode="json", exclude_none=True)
        bound = attempt_request(request)
        materialize_checkout(bound)
        # Like the production Activity, heartbeat the whole delivery,
        # including cleanup and the workspace save.
        async with omnigent_activity_heartbeat(interval_seconds=0.5):
            result = await _try_generic_realizer_dispatch(
                bound,
                plan_store=DbExecutionPlanStore(sessions),
                realizer_registry=Registry(await realizer()),
            )
        _record(
            ledger,
            event="attempt-result",
            ordinal=identity.execution_ordinal,
            providerErrorCode=result.provider_error_code,
            retryRecommendation=result.retry_recommendation,
            metadataKeys=sorted((result.metadata or {}).keys()),
        )
        return result.model_dump(by_alias=True, mode="json", exclude_none=True)

    @activity.defn(name="integration.omnigent.oauth_host_janitor")
    async def confirm_stop(payload: dict[str, Any]) -> dict[str, Any]:
        confirm = payload["confirmAttemptStop"]
        built = await realizer()
        async with omnigent_activity_heartbeat(interval_seconds=0.5):
            confirmation = await GenericOmnigentHostJanitor(
                host_leases=built._host_leases,
                runtime_bindings=built._runtime_bindings,
                realizer=built,
            ).confirm_attempt_stopped(
                execution_plan_ref=confirm["executionPlanRef"],
                runtime_binding_ref=confirm["runtimeBindingRef"],
            )
        _record(ledger, event="stop-confirmed", **confirmation)
        return confirmation

    _WORKER_QUEUE["name"] = queue

    client = await Client.connect(
        config["temporalAddress"], data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    )
    _record(ledger, event="worker-started")
    async with Worker(
        client,
        task_queue=queue,
        workflows=[InterruptedStepRun, AgentRunStandIn],
        activities=[read_plan, agent_attempt, confirm_stop],
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_cached_workflows=0,
    ):
        await asyncio.Event().wait()


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------


async def _database_identity(sessions) -> tuple[str, str | None]:
    from sqlalchemy import text

    engine = sessions.kw["bind"]
    url = engine.url.render_as_string(hide_password=False)
    if engine.dialect.name != "postgresql":
        return url, None
    async with sessions() as session:
        schema = (await session.execute(text("SELECT current_schema()"))).scalar()
    return url, schema


def _install(image: str, build_digest: str) -> None:
    """Record ``image`` as the deployment's installed host, as an update does."""

    from moonmind.omnigent.bootstrap.store import save_resolved_state
    from tests.unit.omnigent.test_generic_platform_production_services import (
        installed_deployment_state,
    )

    save_resolved_state(installed_deployment_state(image, build_digest=build_digest))


def _start_worker(config: dict[str, Any]) -> subprocess.Popen:
    env = dict(os.environ)
    env[CONFIG_ENV] = json.dumps(config)
    # Workers read the installed host image from deployment state only.
    env["MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH"] = config["resolvedImagesPath"]
    for name in ("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "OMNIGENT_IMAGE_REF"):
        env.pop(name, None)
    repository = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repository), *filter(None, [env.get("PYTHONPATH")])]
    )
    # Each worker's output stays with the test's evidence for diagnosis.
    log = Path(config["markers"]).parent / f"worker-{uuid4().hex[:8]}.log"
    with log.open("w") as output:
        return subprocess.Popen(
            [sys.executable, "-m", __name__],
            cwd=repository,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)


async def _wait_for(description, predicate, *, seconds: float = 120.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {description}")


@pytest.mark.parametrize(
    "scenario", ["update", "stop_ack_lost", "cancelled", "exhausted"]
)
async def test_update_restarts_only_the_interrupted_step_from_saved_bytes(
    tmp_path, monkeypatch, scenario
):
    from temporalio.client import WorkflowFailureError

    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
    from moonmind.omnigent.runtime_bindings import (
        DbRuntimeBindingStore,
        RuntimeBindingState,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from tests.integration.reliability.test_release_routing_journey import connect
    from tests.support.isolated_postgres import isolated_postgres
    from tests.unit.omnigent.test_generic_platform_production_services import (
        exact_launch_resolver,
    )

    client = await connect()
    queue = "interrupted-update-" + uuid4().hex
    ledger = tmp_path / "ledger.jsonl"
    markers = tmp_path / "markers"
    markers.mkdir()
    blob_root = tmp_path / "durable-artifacts"
    _use_artifact_blobs(blob_root)
    async with isolated_postgres(_tables()) as sessions:
        # Admitted before the update: the plan pins the then-installed image.
        plan, _resolver = exact_launch_resolver(IMAGE_A, build_digest=BUILD_A)
        await DbExecutionPlanStore(sessions).persist(plan)
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        anchor = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "correlationId": queue,
                "idempotencyKey": queue + ":inputs",
            }
        )
        task_input = b'{"objective":"finish the interrupted step"}'
        input_ref = await gateway.write_bytes(
            request=anchor,
            name="input",
            payload=task_input,
            content_type="application/json",
            link_type="input",
        )
        plan_ref = await gateway.write_bytes(
            request=anchor,
            name="plan",
            payload=b'{"steps":["implement"]}',
            content_type="application/json",
            link_type="input",
        )
        database_url, schema = await _database_identity(sessions)
        config = {
            "scenario": scenario,
            "taskQueue": queue,
            "temporalAddress": client.service_client.config.target_host,
            "databaseUrl": database_url,
            "databaseSchema": schema,
            "workspaceRoot": str(tmp_path / "worker"),
            "blobRoot": str(blob_root),
            "ledger": str(ledger),
            "markers": str(markers),
            "resolvedImagesPath": str(tmp_path / "deployment" / "resolved-images.json"),
            "planArtifactRef": plan_ref,
            "taskInputRef": input_ref,
            "taskInputDigest": "sha256:" + hashlib.sha256(task_input).hexdigest(),
        }
        monkeypatch.setenv(
            "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", config["resolvedImagesPath"]
        )
        _install(IMAGE_A, BUILD_A)
        workers = [_start_worker(config)]
        try:
            handle = await client.start_workflow(
                "ReliabilityInterruptedStepRun",
                id=queue,
                task_queue=queue,
                execution_timeout=timedelta(minutes=8),
            )
            if scenario == "exhausted":
                # Every attempt's host dies under it; the worker keeps running.
                with pytest.raises(WorkflowFailureError) as failure:
                    await asyncio.wait_for(handle.result(), 360)
                assert (
                    "OMNIGENT_SESSION_HOST_LOST" in str(failure.value.cause)
                    or "host" in str(failure.value.cause).lower()
                )
                outcome = None
            else:
                running = await _wait_for(
                    "the interrupted attempt's host to hold unsaved work",
                    lambda: [
                        event
                        for event in _events(ledger, "host-running")
                        if event["ordinal"] == 1
                    ],
                )
                old_worker = workers[0]
                old_host = running[0]["hostPid"]
                # The update: the worker and the agent host stop abruptly.
                os.killpg(old_worker.pid, signal.SIGKILL)
                old_worker.wait(timeout=30)
                os.killpg(os.getpgid(old_host), signal.SIGKILL)
                await _wait_for(
                    "the old host to stop", lambda: not _process_alive(old_host)
                )
                if scenario == "cancelled":
                    await handle.signal("cancel")
                _install(IMAGE_B, BUILD_B)
                workers.append(_start_worker(config))
                outcome = await asyncio.wait_for(handle.result(), 360)
        finally:
            for worker in workers:
                _stop(worker)

        deliveries = _events(ledger, "delivery")
        attempts = [event for event in deliveries if event["step"] == INTERRUPTED_STEP]
        started = _events(ledger, "worker-started")
        launches = _events(ledger, "host-launched")

        # The completed steps never run again, on either worker.
        for step_id in STEP_IDS[:-1]:
            assert [
                (event["ordinal"], event["pid"])
                for event in deliveries
                if event["step"] == step_id
            ] == [(1, started[0]["pid"])]
        # Every attempt reused the workflow's admitted plan, which still pins
        # the image installed when the workflow started.
        assert {
            (event["executionPlanRef"], event["planImage"]) for event in launches
        } == {(plan.planRef, IMAGE_A)}

        bindings = DbRuntimeBindingStore(sessions)
        from sqlalchemy import select

        from api_service.db.models import OmnigentRuntimeBindingRecord

        async with sessions() as session:
            rows = (
                (await session.execute(select(OmnigentRuntimeBindingRecord)))
                .scalars()
                .all()
            )
        recorded = [await bindings.get(row.binding_id) for row in rows]
        # Every attempt's binding was confirmed stopped; none leaked capacity.
        assert recorded and all(
            binding.state is RuntimeBindingState.cleaned for binding in recorded
        )

        def attested_image(binding) -> str:
            ref = binding.attestationRefs["hostHarnessAttestationRef"]
            return json.loads((markers / ref.rsplit("/", 1)[-1]).read_text())[
                "imageRef"
            ]

        if scenario == "exhausted":
            assert [event["ordinal"] for event in attempts] == [1, 2, 3, 4]
            assert len(recorded) == 4
            # Without an update the installed image is the admitted one.
            assert {event["image"] for event in launches} == {IMAGE_A}
            restores = [event["restoreRef"] for event in attempts]
            assert restores[0] is None and all(restores[1:])
            return

        assert len({event["pid"] for event in started}) == 2
        old_pid, new_pid = (event["pid"] for event in started)
        assert old_pid != new_pid
        # Observer redelivery: the interrupted attempt reattached on the new
        # worker under the same Step Execution, without a second turn.
        first_attempt = [event for event in attempts if event["ordinal"] == 1]
        assert [event["pid"] for event in first_attempt] == [old_pid, new_pid]
        assert [(event["ordinal"], event["image"], event["pid"]) for event in launches][
            :1
        ] == [(1, IMAGE_A, old_pid)]
        lost = [
            event
            for event in _events(ledger, "attempt-result")
            if event["ordinal"] == 1
        ]
        assert [event["providerErrorCode"] for event in lost] == [
            "OMNIGENT_SESSION_HOST_LOST"
        ]
        if scenario == "stop_ack_lost":
            assert "unconfirmedAttemptStop" in lost[0]["metadataKeys"]
            confirmed = _events(ledger, "stop-confirmed")
            assert len(confirmed) == 1 and confirmed[0]["stopConfirmed"] is True
            assert confirmed[0]["savedWorkspaceCheckpoint"]["archiveRef"]
        else:
            # The replacement observer's own cleanup confirmed the stop, so it
            # offers the saved bytes and does not also report that stop as
            # unconfirmed or send the workflow to reconcile it again.
            assert "savedWorkspaceCheckpoint" in lost[0]["metadataKeys"]
            assert "unconfirmedAttemptStop" not in lost[0]["metadataKeys"], (
                "a resumed attempt that confirmed its stop must not also report "
                f"it unconfirmed: {lost[0]['metadataKeys']}"
            )
            assert _events(ledger, "stop-confirmed") == []

        if scenario == "cancelled":
            # Cancellation during the interruption starts no successor.
            assert outcome["cancelRequested"] is True
            assert [event["ordinal"] for event in attempts] == [1, 1]
            assert len(launches) == 1
            return

        assert outcome["cancelRequested"] is False
        assert outcome["stepExecutions"][INTERRUPTED_STEP] == 2
        successor = [event for event in attempts if event["ordinal"] == 2]
        assert [event["pid"] for event in successor] == [new_pid]
        assert successor[0]["restoreRef"]
        restored = _events(ledger, "workspace-restored")
        assert [event["ordinal"] for event in restored] == [2]
        assert SAVED_FILE in restored[0]["files"]
        # Production selection launched the successor on the newly installed
        # image although the admitted plan's image was still cached; the
        # predecessor's binding retains the image its host actually ran.
        assert [
            (event["ordinal"], event["image"], event["pid"]) for event in launches
        ] == [(1, IMAGE_A, old_pid), (2, IMAGE_B, new_pid)]
        by_ordinal = {}
        for binding in recorded:
            workspace = binding.phaseResults["workspace"]
            by_ordinal[workspace["stepExecution"]["executionOrdinal"]] = binding
        assert attested_image(by_ordinal[1]) == IMAGE_A
        assert attested_image(by_ordinal[2]) == IMAGE_B
        assert {binding.executionPlanRef for binding in recorded} == {plan.planRef}
        # The successor's own verified save holds the restored predecessor
        # bytes plus its completion, read back from the durable artifact store.
        final_workspace = (
            next(path for path in (tmp_path / "worker" / "temporal_sandbox").iterdir())
            / "repo"
        )
        assert (final_workspace / SAVED_FILE).read_text() == (
            f"saved by attempt 1 on {IMAGE_A}\n"
        )
        assert (final_workspace / COMPLETED_FILE).read_text() == (
            f"completed on {IMAGE_B} from: saved by attempt 1 on {IMAGE_A}\n"
        )
        assert by_ordinal[2].phaseResults["saved"]["archiveRef"]


if __name__ == "__main__":
    _worker_main()
