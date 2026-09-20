"""Plan A count-only admission qualification for MoonLadderStudios/MoonMind#4457.

Part 1 runs the production container-job workflow and registered Activities
against a blocking fake daemon: slot waiting, release, cancellation, and
restart through the real durable path, with host-capacity saturation proving
the ledgers are independent.

Part 2 runs the same owners against a real Docker daemon when one is
reachable (required CI provides it; constrained sandboxes skip): two worker
processes race for the final slot while the parent observes overlapping
execution, and the normal CLI-to-container route is inspected for the stock
2 CPU / 4 GiB limits and PID bound. Real containers use bounded
``moonmind-test-4457-*`` job identities and are always removed.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import subprocess
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.container_job_cli import python_test_submission
from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobWorkflowInput,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.workflows.container_job import MoonMindContainerJobWorkflow

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_DIGEST = "sha256:" + "a" * 64
_OPERATIONS = (
    "resolve_workspace",
    "acquire_image",
    "reconcile_container",
    "create_container",
    "start_container",
    "observe_container",
    "stop_container",
    "publish_evidence",
    "remove_container",
    "cleanup",
    "project_status",
    "repair_projection",
)

_ZERO = "0001-01-01T00:00:00Z"


class _BlockingDaemon:
    """Fake daemon with controllable lifecycles and Docker-faithful create/start."""

    def __init__(self) -> None:
        self.images: set[str] = set()
        self.commands: list[tuple[str, ...]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.max_running = 0

    # -- lifecycle control -------------------------------------------------
    def release(self, name: str, exit_code: int = 0) -> None:
        entry = self.containers[name]
        assert entry["state"] == "running", name
        entry["state"] = "exited"
        entry["exit_code"] = exit_code

    def _recount(self) -> None:
        running = sum(
            1 for entry in self.containers.values() if entry["state"] == "running"
        )
        self.max_running = max(self.max_running, running)

    def _live(self, name: str) -> dict[str, Any] | None:
        entry = self.containers.get(name)
        if entry is None or entry["removed"]:
            return None
        return entry

    def name_for_job(self, job_id: str) -> str | None:
        for command in self.commands:
            if command[0] != "create":
                continue
            name = command[command.index("--name") + 1]
            label = f"moonmind.container_job={job_id}"
            if label in command:
                return name
        return None

    def starts_for(self, name: str) -> int:
        return sum(1 for command in self.commands if command == ("start", name))

    def creates_for(self, name: str) -> int:
        hits = 0
        for command in self.commands:
            if command[0] != "create" or "--name" not in command:
                continue
            if command[command.index("--name") + 1] == name:
                hits += 1
        return hits

    # -- command boundary --------------------------------------------------
    async def run(self, raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        self.commands.append(command)
        if command[0] == "ps":
            lines = [
                f"{name}\t{entry['state']}"
                for name, entry in self.containers.items()
                if not entry["removed"]
            ]
            return 0, ("\n".join(lines) + ("\n" if lines else "")).encode(), b""
        if command[0] == "image" and command[1] == "inspect":
            image = command[-1]
            if image not in self.images:
                return 1, b"", b"Error: No such image"
            if command[2:4] == ("--format", "{{.Id}}"):
                return 0, _DIGEST.encode(), b""
            return 0, f"{_DIGEST}\t{image}@{_DIGEST}".encode(), b""
        if command[0] == "pull":
            self.images.add(command[1])
            return 0, b"pulled", b""
        if command[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            entry = self._live(command[-1])
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            return (
                0,
                json.dumps({"moonmind.ownership": entry["ownership"]}).encode(),
                b"",
            )
        if command[:3] == ("inspect", "--format", "{{.State.Running}}"):
            entry = self._live(command[-1])
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            return 0, (b"true" if entry["state"] == "running" else b"false"), b""
        if command[:3] == ("inspect", "--format", "{{json .State}}"):
            entry = self._live(command[-1])
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            running = entry["state"] == "running"
            if entry["runs"] == 0:
                started, finished, exit_code = _ZERO, _ZERO, 0
            elif running:
                started, finished, exit_code = (
                    "2026-09-20T22:00:00Z",
                    _ZERO,
                    0,
                )
            else:
                started, finished, exit_code = (
                    "2026-09-20T22:00:00Z",
                    "2026-09-20T22:00:05Z",
                    entry["exit_code"],
                )
            return (
                0,
                json.dumps(
                    {
                        "Running": running,
                        "ExitCode": exit_code,
                        "StartedAt": started,
                        "FinishedAt": finished,
                    }
                ).encode(),
                b"",
            )
        if command[0] == "create":
            name = command[command.index("--name") + 1]
            if self._live(name) is not None:
                return 1, b"", b"Error: Conflict. The container name is in use"
            ownership = ""
            for token in command:
                if token.startswith("moonmind.ownership="):
                    ownership = token.split("=", 1)[1]
            self.containers[name] = {
                "ownership": ownership,
                "state": "created",
                "runs": 0,
                "exit_code": 0,
                "removed": False,
            }
            self._recount()
            return 0, name.encode(), b""
        if command[0] == "start":
            entry = self._live(command[1])
            if entry is None:
                return 1, b"", b"Error: No such container"
            if entry["state"] == "running":
                return 1, b"", b"Error: Container is already running"
            entry["state"] = "running"
            entry["runs"] += 1
            self._recount()
            return 0, command[1].encode(), b""
        if command[0] == "stop":
            entry = self._live(command[-1])
            if entry is None:
                return 1, b"", b"Error: No such container"
            entry["state"] = "exited"
            self._recount()
            return 0, command[-1].encode(), b""
        if command[0] == "rm":
            entry = self.containers.get(command[-1])
            if entry is not None:
                entry["removed"] = True
            self._recount()
            return 0, b"", b""
        if command[0] == "logs":
            return 0, b"journey-complete\n", b""
        return 0, b"", b""


def _workflow_input(job_id: str, workspace: Path) -> dict[str, Any]:
    request = ContainerJobWorkflowInput.model_validate(
        {
            "jobId": job_id,
            "observeIntervalSeconds": 1,
            "request": {
                "idempotencyKey": f"plan-a-4457:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a-4457:{job_id}",
                    "runId": "run-1",
                    "stepId": "container-test",
                },
                "spec": {
                    "image": "alpine:3.20",
                    "workspaceRef": {
                        "kind": "external_state",
                        "artifactRef": workspace.name,
                    },
                    "command": ["test", "-f", "/workspace/result.txt"],
                    "outputs": [{"name": "result", "relativePath": "result.txt"}],
                    "networkMode": "none",
                    "resources": {"cpuMillis": 500, "memoryMiB": 512, "pids": 64},
                    "timeoutSeconds": 300,
                },
            },
        }
    )
    return request.model_dump(mode="json", by_alias=True, exclude_none=True)


def _registered_activities(runtime: TemporalAgentRuntimeActivities) -> list[Any]:
    handlers: list[Any] = []
    for operation in _OPERATIONS:
        method = getattr(runtime, f"container_job_{operation}")

        def bind(bound_method: Any):
            async def handler(payload: dict[str, Any]) -> dict[str, Any]:
                return await bound_method(payload)

            return handler

        handler = bind(method)
        handler.__name__ = f"container_job_{operation}"
        handlers.append(activity.defn(name=f"container_job.{operation}")(handler))
    return handlers


def _harness(tmp_path: Path, daemon: _BlockingDaemon):
    published: list[tuple[str, str, bytes]] = []
    projected: list[tuple[str, str]] = []

    async def publish(request: Any, name: str, payload: bytes) -> str:
        published.append((request.job_id, name, payload))
        return f"artifact:{len(published)}"

    async def project(request: Any) -> None:
        state = request.state or request.terminal_state
        projected.append((request.job_id, getattr(state, "value", state)))

    backend_settings = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=backend_settings,
        backend_ref="plan-a-4457",
        command_runner=daemon.run,
        evidence_publisher=publish,
        projection_writer=project,
        image_lock_root=tmp_path / "image-locks",
        workspace_volume_name="agent_workspaces",
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    return backend, runtime, published, projected


async def _wait_for(predicate, what: str, timeout_seconds: float = 60.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


def _daemon_summary(daemon: _BlockingDaemon, tail: int = 30) -> str:
    kinds: dict[str, int] = {}
    for command in daemon.commands:
        kinds[command[0]] = kinds.get(command[0], 0) + 1
    lines = [f"{count}x {kind}" for kind, count in sorted(kinds.items())]
    lines.append("--- tail ---")
    lines.extend(" ".join(command[:4]) for command in daemon.commands[-tail:])
    return "\n".join(lines)


@pytest.mark.asyncio
async def test_slot_wait_release_and_cancel_through_production_workflow(
    tmp_path: Path,
) -> None:
    """Waiting parks durably, release admits, cancel cleans up, cap holds."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _BlockingDaemon()
    _, runtime, _, projected = _harness(tmp_path, daemon)
    workflow_queue = f"plan-a-4457-wait-{uuid4()}"
    activity_queue = settings.temporal.activity_agent_runtime_task_queue
    job_a = "container-job:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    job_b = "container-job:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    job_c = "container-job:cccccccccccccccccccccccccccccccc"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=workflow_queue,
                    workflows=[MoonMindContainerJobWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=activity_queue,
                    activities=_registered_activities(runtime),
                )
            )

            handle_a = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job_a, workspace),
                id=f"plan-a-4457-a-{uuid4()}",
                task_queue=workflow_queue,
            )
            await _wait_for(
                lambda: daemon.name_for_job(job_a) is not None
                and daemon.containers[daemon.name_for_job(job_a) or ""]["state"]
                == "running",
                "job A to hold the only slot",
            )
            name_a = daemon.name_for_job(job_a)
            assert name_a is not None

            handle_b = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job_b, workspace),
                id=f"plan-a-4457-b-{uuid4()}",
                task_queue=workflow_queue,
            )
            try:
                await _wait_for(
                    lambda: (job_b, "waiting_for_capacity") in projected,
                    "job B to park in the durable capacity wait",
                )
            except TimeoutError:
                status_b = await handle_b.query("status")
                raise AssertionError(
                    "job B stalled before admission; "
                    f"workflow status={status_b}\n{_daemon_summary(daemon)}"
                )
            name_b = daemon.name_for_job(job_b)
            assert name_b is not None
            # The waiter is created but never started: created waiters hold no
            # slot and the workflow parks in the durable wait, not a deadlock.
            assert daemon.containers[name_b]["state"] == "created"
            assert daemon.containers[name_b]["runs"] == 0
            assert daemon.starts_for(name_b) == 0

            await handle_b.signal("cancel")
            result_b = await handle_b.result()
            assert result_b["state"] == "canceled"
            # Cancellation removes the waiter's created container: no
            # abandoned consumer and no leaked slot.
            assert daemon.containers[name_b]["removed"] is True

            daemon.release(name_a)
            result_a = await handle_a.result()
            assert result_a["state"] == "succeeded"
            assert daemon.containers[name_a]["removed"] is True

            handle_c = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job_c, workspace),
                id=f"plan-a-4457-c-{uuid4()}",
                task_queue=workflow_queue,
            )
            await _wait_for(
                lambda: daemon.name_for_job(job_c) is not None
                and daemon.containers[daemon.name_for_job(job_c) or ""]["state"]
                == "running",
                "job C to be admitted after release",
            )
            name_c = daemon.name_for_job(job_c)
            assert name_c is not None
            daemon.release(name_c)
            result_c = await handle_c.result()
            assert result_c["state"] == "succeeded"

    assert daemon.max_running <= 1, "overlapping execution never exceeded the cap"
    assert daemon.containers[name_a]["runs"] == 1
    assert daemon.containers[name_c]["runs"] == 1
    assert daemon.starts_for(name_b) == 0, "a canceled waiter is never started"
    assert {job for job, state in projected if state == "succeeded"} >= {job_a, job_c}
    assert not any(command[0] == "info" for command in daemon.commands)


@pytest.mark.asyncio
async def test_restart_reconciles_the_existing_container_instead_of_recreating(
    tmp_path: Path,
) -> None:
    """A resubmitted job reuses its created container; a cleaned one recurs."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _BlockingDaemon()
    backend, runtime, _, _ = _harness(tmp_path, daemon)
    workflow_queue = f"plan-a-4457-restart-{uuid4()}"
    activity_queue = settings.temporal.activity_agent_runtime_task_queue
    job_d = "container-job:dddddddddddddddddddddddddddddddd"

    # Simulate worker death between create and start: the created container
    # already exists when the workflow (re)submits the same job identity.
    direct = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_d,
            "ownershipToken": f"{job_d}:v1",
            "request": {
                "idempotencyKey": f"plan-a-4457:{job_d}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a-4457:{job_d}",
                    "runId": "run-1",
                    "stepId": "container-test",
                },
                "spec": {
                    "image": "alpine:3.20",
                    "workspaceRef": {
                        "kind": "external_state",
                        "artifactRef": workspace.name,
                    },
                    "command": ["test", "-f", "/workspace/result.txt"],
                    "networkMode": "none",
                    "resources": {"cpuMillis": 500, "memoryMiB": 512, "pids": 64},
                    "timeoutSeconds": 300,
                },
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": _DIGEST,
        }
    )
    created = await backend.create_container(direct)
    assert created.container_ref is not None
    name_d = created.container_ref

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=workflow_queue,
                    workflows=[MoonMindContainerJobWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=activity_queue,
                    activities=_registered_activities(runtime),
                )
            )

            handle = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job_d, workspace),
                id=f"plan-a-4457-d-{uuid4()}",
                task_queue=workflow_queue,
            )
            await _wait_for(
                lambda: daemon.containers.get(name_d, {}).get("state") == "running",
                "resubmitted job D to start its existing container",
            )
            daemon.release(name_d)
            result = await handle.result()
            assert result["state"] == "succeeded"

            # After terminal cleanup removed the container, the same identity
            # runs again as a fresh execution: same name, no collision.
            handle2 = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job_d, workspace),
                id=f"plan-a-4457-d2-{uuid4()}",
                task_queue=workflow_queue,
            )
            await _wait_for(
                lambda: daemon.name_for_job(job_d) is not None
                and daemon.containers.get(daemon.name_for_job(job_d) or "") is not None
                and not daemon.containers[daemon.name_for_job(job_d) or ""]["removed"]
                and daemon.containers[daemon.name_for_job(job_d) or ""]["state"]
                == "running",
                "resubmitted job D to run again after cleanup",
            )
            daemon.release(daemon.name_for_job(job_d) or "")
            result2 = await handle2.result()
            assert result2["state"] == "succeeded"

    assert daemon.creates_for(name_d) == 2, (
        "one create before the first run, one after cleanup removed it"
    )
    assert daemon.containers[name_d]["runs"] == 1
    assert daemon.max_running <= 1


@pytest.mark.asyncio
async def test_subordinate_test_job_runs_while_host_capacity_is_full(
    tmp_path: Path,
) -> None:
    """Host and container-job ledgers are separate counts (R3 separation)."""

    host_decision = evaluate_generic_host_capacity(
        active_hosts=8,
        recent_cold_launches=0,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )
    assert host_decision.admitted is False

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _BlockingDaemon()
    _, runtime, _, _ = _harness(tmp_path, daemon)
    workflow_queue = f"plan-a-4457-hostsep-{uuid4()}"
    activity_queue = settings.temporal.activity_agent_runtime_task_queue
    job = "container-job:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=workflow_queue,
                    workflows=[MoonMindContainerJobWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=activity_queue,
                    activities=_registered_activities(runtime),
                )
            )
            handle = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _workflow_input(job, workspace),
                id=f"plan-a-4457-hostsep-{uuid4()}",
                task_queue=workflow_queue,
            )
            await _wait_for(
                lambda: daemon.name_for_job(job) is not None
                and daemon.containers[daemon.name_for_job(job) or ""]["state"]
                == "running",
                "subordinate test job to run despite full host capacity",
            )
            daemon.release(daemon.name_for_job(job) or "")
            result = await handle.result()

    assert result["state"] == "succeeded"
    # The container-job path never consults host admission or machine probes.
    assert not any(command[0] == "info" for command in daemon.commands)


def _cli_env() -> dict[str, str]:
    return {
        "MOONMIND_AGENT_RUN_ID": "plan-a-4457",
        "MOONMIND_RUNTIME_ID": "plan-a-4457-runtime",
        "MOONMIND_CONTAINER_JOBS_SESSION_ID": "plan-a-4457-session",
    }


def test_batch_pr_resolver_route_compiles_fixed_limits_to_host_execution() -> None:
    """Each preset profile compiles to host execution on fixed limits only."""

    from moonmind.omnigent.execution_profiles import (
        PROFILES,
        compile_effective_launch,
    )

    digest_env = {
        "OMNIGENT_IMAGE_REF": "example.com/moonmind/server@sha256:" + "b" * 64,
        "OMNIGENT_HOST_IMAGE_REF": "example.com/moonmind/host@sha256:" + "c" * 64,
    }
    old = {key: os.environ.get(key) for key in digest_env}
    try:
        os.environ.update(digest_env)
        assert PROFILES, "preset profiles must exist"
        for ref, profile in PROFILES.items():
            launch = compile_effective_launch(
                profile_ref=ref,
                policy_ref=None,
                provider_profile_id="plan-a-4457-provider-profile",
            )
            assert launch["limits"]["cpuMillis"] == 2000, ref
            assert launch["limits"]["memoryMiB"] == 4096, ref
            assert launch["harness"], ref
            assert "cpu_pool" not in launch and "machineBudget" not in launch, ref
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------- real Docker


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _require_real_docker(image: str = "alpine:3.20") -> None:
    if not _docker_available():
        pytest.skip("no reachable Docker daemon for the real-Docker qualification")
    try:
        completed = subprocess.run(
            ["docker", "pull", image], capture_output=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not pull {image} for the real-Docker qualification: {exc}")
    if completed.returncode != 0:
        pytest.skip(f"could not pull {image} for the real-Docker qualification")


def _real_request(job_id: str, workspace: Path, *, command: list[str]) -> dict[str, Any]:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": f"plan-a-4457-real:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a-4457-real:{job_id}",
                    "runId": "run-1",
                    "stepId": "container-test",
                },
                "spec": {
                    "image": "alpine:3.20",
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
                    "command": command,
                    "networkMode": "none",
                    "resources": {"cpuMillis": 500, "memoryMiB": 512, "pids": 64},
                    "timeoutSeconds": 300,
                },
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": "alpine:3.20",
        }
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def _real_backend(
    workspace_root: Path, lock_root: Path, recorded: list[tuple[str, ...]] | None = None
):
    from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command

    async def runner(args):
        if recorded is not None:
            recorded.append(tuple(str(item) for item in args))
        return await run_runtime_command(("docker", *args))

    backend_settings = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
    )
    return DockerContainerJobBackend(
        workspace_root=workspace_root,
        settings=backend_settings,
        backend_ref="plan-a-4457-real",
        command_runner=runner,
        image_lock_root=lock_root,
    )


def _real_race_worker(
    workspace_dir: str,
    lock_dir: str,
    job_id: str,
    outcomes: Any,
) -> None:
    import asyncio as _asyncio

    async def _main() -> None:
        backend = _real_backend(Path(workspace_dir), Path(lock_dir))
        request = ContainerJobActivityRequest.model_validate(
            _real_request(job_id, Path(workspace_dir), command=["sleep", "60"])
        )
        request.wait_for_capacity = True
        try:
            result = await backend.start_container(request)
            if result.capacity_wait:
                outcomes[job_id] = "wait"
            else:
                outcomes[job_id] = "started" if result.running else "other"
        except Exception as exc:  # noqa: BLE001 - outcome recorded for the parent
            outcomes[job_id] = f"error:{type(exc).__name__}:{exc}"[:500]

    _asyncio.run(_main())


def _inspect_running(name: str) -> bool | None:
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode().strip() == "true"


@pytest.mark.skipif(os.name != "posix", reason="worker processes share a POSIX lock")
def test_two_processes_race_for_the_final_slot_on_real_docker(tmp_path: Path) -> None:
    """Overlapping real starts never exceed the cap; waiters progress."""

    _require_real_docker()
    (tmp_path / "art_workspace").mkdir()
    lock_root = tmp_path / "locks"
    job_ids = [
        "container-job:4457f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1",
        "container-job:4457f2f2f2f2f2f2f2f2f2f2f2f2f2f2f2",
    ]
    names = [
        DockerContainerJobBackend._name(
            ContainerJobActivityRequest.model_validate(
                _real_request(
                    job_id, tmp_path / "art_workspace", command=["sleep", "60"]
                )
            )
        )
        for job_id in job_ids
    ]
    parent = _real_backend(tmp_path, lock_root)
    created_names: list[str] = []

    async def _prepare() -> None:
        for job_id in job_ids:
            request = ContainerJobActivityRequest.model_validate(
                _real_request(
                    job_id,
                    tmp_path / "art_workspace",
                    command=["sh", "-c", "echo EXECUTED && sleep 60"],
                )
            )
            created = await parent.create_container(request)
            assert created.container_ref is not None
            created_names.append(created.container_ref)

    async def _cleanup(names_to_remove: list[str]) -> None:
        for job_id, name in zip(job_ids, names_to_remove):
            request = ContainerJobActivityRequest.model_validate(
                _real_request(job_id, tmp_path / "art_workspace", command=["sleep", "60"])
            )
            request.container_ref = name
            try:
                await parent.stop_container(request)
            except Exception:  # noqa: BLE001 - best-effort test cleanup
                pass
            try:
                await parent.remove_container(request)
            except Exception:  # noqa: BLE001 - best-effort test cleanup
                pass

    async def _scenario() -> dict[str, str]:
        await _prepare()
        ctx = multiprocessing.get_context("spawn")
        manager = ctx.Manager()
        outcomes = manager.dict()
        processes = [
            ctx.Process(
                target=_real_race_worker,
                args=(str(tmp_path), str(lock_root), job_id, outcomes),
            )
            for job_id in job_ids
        ]
        for process in processes:
            process.start()
        max_overlap = 0
        try:
            deadline = asyncio.get_running_loop().time() + 120
            while any(process.is_alive() for process in processes):
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError("real-Docker race workers did not finish")
                states = [_inspect_running(name) for name in created_names]
                overlap = sum(1 for state in states if state is True)
                max_overlap = max(max_overlap, overlap)
                await asyncio.sleep(0.02)
        finally:
            for process in processes:
                process.join(120)
        for process in processes:
            assert process.exitcode == 0, f"race worker exited {process.exitcode}"
        result = dict(outcomes)
        result["max_overlap"] = str(max_overlap)
        return result

    async def _forward_progress(summary: dict[str, str]) -> None:
        # The winner is stopped (slot released); the waiter is admitted next
        # and each container executed exactly once.
        outcomes = {key: value for key, value in summary.items() if key != "max_overlap"}
        winner = next(
            job_id for job_id in job_ids if outcomes.get(job_id) == "started"
        )
        waiter = next(job_id for job_id in job_ids if outcomes.get(job_id) == "wait")
        winner_name = created_names[job_ids.index(winner)]
        waiter_name = created_names[job_ids.index(waiter)]
        stop_request = ContainerJobActivityRequest.model_validate(
            _real_request(winner, tmp_path / "art_workspace", command=["sleep", "60"])
        )
        stop_request.container_ref = winner_name
        await parent.stop_container(stop_request)
        retry = ContainerJobActivityRequest.model_validate(
            _real_request(waiter, tmp_path / "art_workspace", command=["sleep", "60"])
        )
        retry.wait_for_capacity = True
        retried = await parent.start_container(retry)
        assert retried.running is True, retried
        assert _inspect_running(waiter_name) is True
        for name in created_names:
            completed = subprocess.run(
                ["docker", "logs", name], capture_output=True, timeout=30
            )
            assert completed.returncode == 0, completed.stderr.decode()[:500]
            assert completed.stdout.decode().count("EXECUTED") == 1, name

    async def _scenario_and_progress() -> dict[str, str]:
        summary = await _scenario()
        assert created_names == names
        assert sorted(
            value for key, value in summary.items() if key != "max_overlap"
        ) == ["started", "wait"], summary
        assert int(summary["max_overlap"]) <= 1, summary
        await _forward_progress(summary)
        return summary

    try:
        asyncio.run(_scenario_and_progress())
    finally:
        asyncio.run(_cleanup(created_names))


@pytest.mark.skipif(os.name != "posix", reason="real Docker qualification needs Linux")
def test_real_docker_inspect_shows_stock_cli_limits(tmp_path: Path) -> None:
    """The CLI route creates real containers with 2 CPU / 4 GiB / 512 pids."""

    _require_real_docker()
    (tmp_path / "art_workspace").mkdir()
    submission = python_test_submission(["tests/unit/test_example.py"], env=_cli_env())
    resources = submission["spec"]["resources"]
    assert resources == {"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512}

    recorded: list[tuple[str, ...]] = []
    backend = _real_backend(tmp_path, tmp_path / "locks", recorded)
    job_id = "container-job:4457c1c1c1c1c1c1c1c1c1c1c1c1c1c1"
    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": f"plan-a-4457-inspect:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a-4457-inspect:{job_id}",
                    "runId": "run-1",
                    "stepId": "container-test",
                },
                "spec": {
                    # Stock CLI resources through the normal spec shape; the
                    # image is the pulled qualification fixture and network
                    # stays none so this stays a limit inspection, not an
                    # egress-network test. Fixed-limit plumbing is identical
                    # for bridge (which only adds labels/env/attestation).
                    "image": "alpine:3.20",
                    "workspaceRef": {
                        "kind": "sandbox",
                        "workspaceId": "art_workspace",
                    },
                    "command": ["true"],
                    "networkMode": "none",
                    "resources": resources,
                    "timeoutSeconds": 120,
                },
            },
            "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
            "resolvedImageRef": "alpine:3.20",
        }
    )

    async def _scenario() -> str:
        created = await backend.create_container(request)
        assert created.container_ref is not None
        return created.container_ref

    name = asyncio.run(_scenario())
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{json .HostConfig}}", name],
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr.decode()[:500]
        host_config = json.loads(completed.stdout.decode())
        assert host_config["NanoCpus"] == 2_000_000_000, host_config["NanoCpus"]
        assert host_config["Memory"] == 4 * 1024**3, host_config["Memory"]
        assert host_config["PidsLimit"] == 512, host_config["PidsLimit"]
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, timeout=60
        )
    assert not any(command[0] == "info" for command in recorded)
