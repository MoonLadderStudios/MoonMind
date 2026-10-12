"""MoonMind#4627: an update interrupts a running step and the workflow completes.

MoonMind.Run executes three agent steps through the real MoonMind.AgentRun
child. The Activity that runs them is served by a separate worker OS process
(``interrupted_step_journey_support``) that builds the production generic host
realizer over PostgreSQL, the artifact store, the workspace root and Docker.
Each attempt's host container is launched by ``DockerOmnigentHostLauncher``
and registers through the production registration waiter; the production
Omnigent bridge drives the session over HTTP.

Steps one and two complete. While step three runs, the update kills that
worker process outright and, in the host-lost case, removes the step's host
container. A new worker process with no inherited memory takes over: its
bridge observes the controlled server projecting the removed host offline, the
realizer saves the caller-owned workspace and confirms the host's removal, and
MoonMind.Run starts one successor Step Execution. The successor launches on
the newly installed image, restores the verified saved bytes and completes the
workflow without repeating steps one and two. In the observer-only case the
host survives, so the replacement reattaches to the same accepted turn.

Only the agent and Omnigent server are controlled: the stand-in serves stock
routes and derives host registration and liveness from the real container.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import signal
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RawValue
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base, OmnigentRuntimeBindingRecord
from moonmind.omnigent.bootstrap import store as bootstrap_store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.integration.reliability.interrupted_step_journey_support import (
    ControlledOmnigentServer,
    ControlledTurn,
    read_records,
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
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
# The controlled provider's host process: it keeps the launched container
# running, as the real `omnigent host` does, until Docker removes it.
_PROVIDER_HOST_PROCESS = "#!/bin/sh\nexec sleep 3600\n"
# The durable owners every worker process shares: plans, bindings, leases,
# bridge sessions, canonical control plane and artifacts.
_DURABLE_TABLES = (
    "omnigent_execution_plans",
    "omnigent_runtime_bindings",
    "omnigent_credential_runtimes",
    "omnigent_host_bindings_v2",
    "omnigent_host_leases_v2",
    "omnigent_oauth_host_leases",
    "omnigent_bridge_sessions",
    "omnigent_bridge_session_events",
    "omnigent_chat_binding_aliases",
    "omnigent_cleanup_authority",
    "omnigent_reconciliation_decisions",
    "omnigent_commands",
    "omnigent_observations",
    "omnigent_turn_attempts",
    "omnigent_sessions",
    "temporal_artifacts",
    "temporal_artifact_links",
    "temporal_artifact_pins",
    "temporal_artifact_use_claims",
    "temporal_artifact_deletion_intents",
)


def _tables(names: Sequence[str]) -> list:
    """The named tables with the tables their foreign keys reference."""

    needed: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name not in needed:
            needed.add(name)
            pending.extend(
                key.column.table.name for key in Base.metadata.tables[name].foreign_keys
            )
    return [table for table in Base.metadata.sorted_tables if table.name in needed]


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


class _WorkerProcess:
    """One activity worker OS process; the update kills it outright."""

    def __init__(self, config_path: Path, log_path: Path, records_path: Path):
        self._config_path = config_path
        self._log_path = log_path
        self._records_path = records_path
        self.process: subprocess.Popen[bytes] | None = None

    async def start(self) -> int:
        with open(self._log_path, "ab") as log:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.integration.reliability.interrupted_step_journey_support",
                    str(self._config_path),
                ],
                cwd=_REPOSITORY_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        pid = self.process.pid
        for _ in range(240):
            if any(
                item["kind"] == "worker_ready" and item["pid"] == pid
                for item in read_records(self._records_path)
            ):
                return pid
            if self.process.poll() is not None:
                break
            await asyncio.sleep(0.25)
        raise AssertionError(
            f"activity worker {pid} never became ready:\n{self.log_tail()}"
        )

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=30)

    def log_tail(self) -> str:
        if not self._log_path.exists():
            return ""
        return self._log_path.read_text(errors="replace")[-6000:]


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
    predecessor_image = await _image_digest(backend, _PREDECESSOR_TAG)
    installed_image = await _image_digest(backend, _INSTALLED_TAG)
    assert predecessor_image != installed_image

    # The deployment's installed runtime, as bootstrap records it. Before the
    # update it is the predecessor's image; the update installs the new one.
    resolved_path = tmp_path / "resolved.json"
    monkeypatch.setenv("MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(resolved_path))
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

    # An empty Skill projection, readable by the host's runtime user.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skills_dir.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )
    suffix = uuid4().hex[:12]
    network = f"moonmind-test-successor-{suffix}"
    credential_volume = f"moonmind-test-successor-credential-{suffix}"
    provider_volume = f"moonmind-test-successor-provider-{suffix}"
    await backend.run(["docker", "network", "create", "--internal", network])

    async def populate(volume: str, script: str) -> None:
        await backend.run(["docker", "volume", "create", volume])
        await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={volume},dst=/target",
                predecessor_image,
                "sh",
                "-ceu",
                script,
            ]
        )

    # The materialized credential generation the host entrypoint verifies.
    await populate(credential_volume, "printf 4 > /target/.moonmind-generation")
    # The controlled provider's host process, owned by the runtime user, in
    # the OpenCode runtime seed the host entrypoint copies.
    await populate(
        provider_volume,
        "mkdir -p /target/bin; "
        f"printf '%s' {shlex.quote(_PROVIDER_HOST_PROCESS)} > /target/bin/omnigent; "
        "chmod 0755 /target /target/bin /target/bin/omnigent; "
        "chown -R 1000:1000 /target",
    )

    interrupted = asyncio.Event()
    released = asyncio.Event()

    async def agent_turn(turn: ControlledTurn) -> None:
        step, ordinal = turn.logical_step_id, turn.execution_ordinal
        workspace = turn.host.workspace
        assert workspace is not None, "the host mounts its workspace"
        call = f"{step}-{ordinal}"
        await turn.work(call)
        if step != "finish":
            (workspace / f"{step}.txt").write_text("done\n")
            await turn.finish(call, f"{step} completed")
            return
        if ordinal == 1:
            # Unsaved progress, then the update replaces the worker.
            (workspace / "file.txt").write_text("interrupted candidate\n")
            (workspace / "new.bin").write_bytes(b"\x00\xff\x80")
            interrupted.set()
            # Only a surviving host finishes the turn it accepted.
            await released.wait()
            (workspace / "finish.txt").write_text("same session completed\n")
            await turn.finish(call, "finish completed on its host")
            return
        # The successor continues from the restored saved work.
        assert (workspace / "file.txt").read_text() == "interrupted candidate\n"
        assert (workspace / "new.bin").read_bytes() == b"\x00\xff\x80"
        (workspace / "finish.txt").write_text("successor completed\n")
        await turn.finish(call, "finish completed on the installed runtime")

    server = ControlledOmnigentServer(backend=backend, script=agent_turn)
    server_url = await server.start()
    records_path = tmp_path / "worker-records.jsonl"
    workers: list[_WorkerProcess] = []

    async with isolated_postgres(_tables(_DURABLE_TABLES)) as sessions:
        async with sessions() as session:
            schema = (await session.execute(text("select current_schema()"))).scalar()
        gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
        monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
        plan = _plan("opencode-go/model")
        await DbExecutionPlanStore(sessions).persist(plan)
        config_path = tmp_path / "worker-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "namespace": connected.namespace,
                    "taskQueue": queue + "-activity",
                    "schema": schema,
                    "blobRoot": str(blob_root),
                    "workspaceRoot": str(workspace_root),
                    "origin": str(origin),
                    "serverUrl": server_url,
                    "network": network,
                    "credentialVolume": credential_volume,
                    "providerVolume": provider_volume,
                    "skillsDir": str(skills_dir),
                    "plannedImage": predecessor_image,
                    "recordsPath": str(records_path),
                    "bridgeQuietSeconds": 0.5,
                    "hostLossGraceSeconds": 3.0,
                    "environment": {
                        "OMNIGENT_ENABLED": "true",
                        "OMNIGENT_SERVER_URL": server_url,
                        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH": str(resolved_path),
                        "WORKFLOW_WORKSPACE_ROOT": str(workspace_root),
                    },
                }
            )
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

        def new_worker() -> _WorkerProcess:
            worker = _WorkerProcess(
                config_path, tmp_path / f"worker-{len(workers) + 1}.log", records_path
            )
            workers.append(worker)
            return worker

        try:
            first_pid = await new_worker().start()
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
                    {started, ended}, timeout=90, return_when=asyncio.FIRST_COMPLETED
                )
                if not started.done():
                    started.cancel()
                    # Surface why the workflow ended before step three ran.
                    raise AssertionError(
                        "step three never ran: "
                        f"{await ended if ended.done() else 'timeout'}\n"
                        f"requests={server.requests[-40:]}\n"
                        f"unknown={server.unknown_routes}\n"
                        f"{workers[0].log_tail()}"
                    )
                ended.cancel()
                (finish_session,) = (
                    session_id
                    for session_id, session in server.sessions.items()
                    if ":finish:" in server.hosts[session.host_id].step_execution_id
                )
                finish_host = server.hosts[
                    server.sessions[finish_session].host_id
                ].container
                observed_streams = server.stream_opens.get(finish_session, 0)
                # The update: the Activity worker process is killed, the new
                # image is installed, and the running agent host is removed.
                workers[0].kill()
                install(installed_image)
                if host_replaced:
                    await backend.run(["docker", "rm", "-f", finish_host])
                replacement_pid = await new_worker().start()
                assert replacement_pid != first_pid
                if not host_replaced:
                    # The replacement observer reattaches to the accepted turn
                    # on its surviving host, which then finishes it.
                    for _ in range(240):
                        if server.stream_opens.get(finish_session, 0) > (
                            observed_streams
                        ):
                            break
                        await asyncio.sleep(0.25)
                    released.set()
                try:
                    outcome = await asyncio.wait_for(handle.result(), timeout=120)
                except BaseException:
                    print(workers[-1].log_tail())
                    raise

                await Replayer(
                    workflows=[
                        InterruptedStepRun,
                        MoonMindAgentRun,
                        MockProviderProfileManager,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(await handle.fetch_history())
                # Every attempt's host is gone: an interrupted one removed by
                # the update, the others by their own finalization.
                for host in server.hosts.values():
                    code, _, _ = await backend.run(
                        ["docker", "container", "inspect", host.container],
                        check=False,
                    )
                    assert code != 0, host.container
            async with sessions() as session:
                terminal_results = [
                    row.terminal_result_json
                    for row in (
                        await session.execute(select(OmnigentRuntimeBindingRecord))
                    ).scalars()
                    if row.terminal_result_json
                ]
        finally:
            for worker in workers:
                worker.kill()
            if "manager" in locals():
                await manager.terminate(reason="restartability journey finished")
            await server.stop()
            for host in server.hosts.values():
                await backend.run(["docker", "rm", "-f", host.container], check=False)
            for volume in (credential_volume, provider_volume):
                await backend.run(["docker", "volume", "rm", volume], check=False)
            await backend.run(["docker", "network", "rm", network], check=False)

        # The workflow completed and steps one and two ran exactly once.
        assert set(outcome["ledger"].values()) <= {"completed", "succeeded"}, outcome
        assert not server.turn_errors, server.turn_errors
        attempts = [("prepare", 1), ("implement", 1), ("finish", 1)]
        images = [predecessor_image] * 3
        if host_replaced:
            attempts.append(("finish", 2))
            images.append(installed_image)
        launched = [
            (
                host.step_execution_id.rsplit(":", 3)[-3],
                int(host.step_execution_id.rsplit(":", 1)[-1]),
                host.image,
            )
            for host in server.hosts.values()
        ]
        # One launched, registered host per attempt; only a confirmed host loss
        # produced a successor, on the newly installed image.
        assert [(step, ordinal) for step, ordinal, _ in launched] == attempts
        assert [image for *_, image in launched] == images
        # One provider session and one accepted turn per attempt: replacing
        # the observer never resubmitted the turn or started another agent.
        assert len(server.sessions) == len(attempts)
        assert [len(session.messages) for session in server.sessions.values()] == [
            1
        ] * len(attempts)
        records = read_records(records_path)
        prepared = {
            item["stepExecutionId"]: item
            for item in records
            if item["kind"] == "prepare"
        }
        workers_by_attempt = {
            (key.rsplit(":", 3)[-3], int(key.rsplit(":", 1)[-1])): item["pid"]
            for key, item in prepared.items()
        }
        # Steps one to three started in the first worker process; a successor
        # could only start in the separate replacement process.
        assert {workers_by_attempt[attempt] for attempt in attempts[:3]} == {first_pid}
        host_lost = [
            result
            for result in terminal_results
            if result.get("providerErrorCode") == "OMNIGENT_SESSION_HOST_LOST"
        ]
        if not host_replaced:
            assert not any(item["restoreRef"] for item in prepared.values())
            assert not host_lost
            return
        assert workers_by_attempt[("finish", 2)] == replacement_pid
        # The replacement's bridge observed the removed host as a typed loss.
        assert len(host_lost) == 1, terminal_results
        assert host_lost[0]["retryRecommendation"] == "retry_step_execution"
        # The replacement saved the interrupted workspace after the host was
        # removed, and the successor restored exactly those verified bytes.
        (saved,) = (
            item
            for item in records
            if item["kind"] == "save"
            and item["stepExecutionId"].endswith(":finish:execution:1")
        )
        assert saved["pid"] == replacement_pid
        (successor_id,) = (
            key for key in prepared if key.endswith(":finish:execution:2")
        )
        assert prepared[successor_id]["restoreRef"] == saved["archiveRef"]
        assert (
            "sha256:"
            + hashlib.sha256(await gateway.read_bytes(saved["archiveRef"])).hexdigest()
            == saved["archiveDigest"]
        )
        assert not any(
            item["restoreRef"] for key, item in prepared.items() if key != successor_id
        )
