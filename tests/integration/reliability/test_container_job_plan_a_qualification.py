"""Plan A real-boundary qualification for MoonLadderStudios/MoonMind#4457.

Hermetic-first journeys through the production container-job workflow,
registered activities, and Docker backend adapter, plus real-Docker legs that
execute against a disposable ``moonmind-test-*`` footprint and skip with a
recorded reason when no daemon is available (a missing environment never
counts as a pass).

Scope is strictly the count-only admission owners: the filesystem capacity
lock, the daemon-side slot ledger, start reconciliation, and the fixed stock
limits. No pool helper, ledger, or migration framework.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import (
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobWorkflowInput,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    FilesystemCapacityAdmissionLock,
)
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
    pytest.mark.asyncio,
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


def _docker_unavailable_reason() -> str:
    """Return why the real-Docker legs cannot run here, or ``""``."""

    if shutil.which("docker") is None:
        return "the docker client is not installed on this runner"
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the docker daemon is unreachable: {exc}"
    if completed.returncode != 0:
        return "the docker daemon did not report a server version"
    return ""


_REQUIRES_DOCKER = pytest.mark.skipif(
    bool(_docker_unavailable_reason()),
    reason=f"no real Docker daemon: {_docker_unavailable_reason() or 'available'}",
)


class _HermeticSystemDaemon:
    """Stateful Docker command boundary with a deployment-wide image cache."""

    def __init__(self) -> None:
        self.images: set[str] = set()
        self.commands: list[tuple[str, ...]] = []
        self.pull_count = 0
        self.ps_calls = 0
        self.ps_full_for_first_calls = 0

    async def run(self, raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        self.commands.append(command)
        if command[0] == "ps":
            self.ps_calls += 1
            if self.ps_calls <= self.ps_full_for_first_calls:
                return 0, b"moonmind-container-job-other\trunning\n", b""
            return 0, b"", b""
        if command[:2] == ("image", "inspect"):
            image = command[-1]
            if image not in self.images:
                return 1, b"", b"Error: No such image"
            if command[2:4] == ("--format", "{{.Id}}"):
                return 0, _DIGEST.encode(), b""
            return 0, f"{_DIGEST}\t{image}@{_DIGEST}".encode(), b""
        if command[0] == "pull":
            self.pull_count += 1
            self.images.add(command[1])
            return 0, b"pulled", b""
        if command[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"Error: No such object: moonmind-container-job"
        if command[:3] == ("inspect", "--format", "{{json .State}}"):
            return 0, json.dumps({"Running": False, "ExitCode": 0}).encode(), b""
        if command[0] == "logs":
            return 0, b"journey-complete\n", b""
        return 0, b"", b""


def _workflow_input(
    job_id: str, image: str, workspace: Path, *, timeout_seconds: int = 60
) -> dict[str, Any]:
    request = ContainerJobWorkflowInput.model_validate(
        {
            "jobId": job_id,
            "observeIntervalSeconds": 1,
            "request": {
                "idempotencyKey": f"plan-a:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a:{job_id}",
                    "runId": "run-1",
                    "stepId": "container-test",
                },
                "spec": {
                    "image": image,
                    "workspaceRef": {
                        "kind": "external_state",
                        "artifactRef": workspace.name,
                    },
                    "command": ["test", "-f", "/workspace/result.txt"],
                    "outputs": [{"name": "result", "relativePath": "result.txt"}],
                    "networkMode": "none",
                    "resources": {"cpuMillis": 500, "memoryMiB": 512, "pids": 64},
                    "timeoutSeconds": timeout_seconds,
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


def _harness(
    tmp_path: Path,
    daemon: _HermeticSystemDaemon,
    published: list,
    projected: list,
) -> tuple[DockerContainerJobBackend, TemporalAgentRuntimeActivities]:
    async def publish(request: Any, name: str, payload: bytes) -> str:
        published.append((request.job_id, name, payload))
        return f"artifact:{len(published)}"

    async def project(request: Any) -> None:
        state = request.state or request.terminal_state
        projected.append((request.job_id, getattr(state, "value", state)))

    workspace_root = tmp_path
    backend = DockerContainerJobBackend(
        workspace_root=workspace_root,
        backend_ref="system-proxy",
        docker_host="tcp://dockerproxy:2375",
        command_runner=daemon.run,
        evidence_publisher=publish,
        projection_writer=project,
        image_lock_root=tmp_path / "image-locks",
        workspace_volume_name="agent_workspaces",
    )
    return backend, TemporalAgentRuntimeActivities(container_job_backend=backend)


async def _run_journey(
    tmp_path: Path,
    daemon: _HermeticSystemDaemon,
    payload: dict[str, Any],
    published: list,
    projected: list,
) -> dict[str, Any]:
    _, runtime = _harness(tmp_path, daemon, published, projected)
    workflow_queue = f"container-job-plan-a-{uuid4()}"
    activity_queue = settings.temporal.activity_agent_runtime_task_queue
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
                payload,
                id=f"container-job-plan-a-{uuid4()}",
                task_queue=workflow_queue,
            )
            return await handle.result()


async def test_slot_wait_releases_and_restart_proceeds(tmp_path: Path) -> None:
    """A parked job starts once the holder releases, via the real workflow.

    MoonLadderStudios/MoonMind#4457 R3: the first admission observes a full
    count and parks in ``waiting_for_capacity``; the retry after the release
    starts the same container without a duplicate create.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _HermeticSystemDaemon()
    daemon.ps_full_for_first_calls = 1
    published: list = []
    projected: list = []

    result = await _run_journey(
        tmp_path,
        daemon,
        _workflow_input("container-job:" + "a" * 32, "alpine:3.20", workspace),
        published,
        projected,
    )

    assert result["state"] == "succeeded"
    assert daemon.ps_calls >= 2, "admission must have been retried after the wait"
    assert sum(command[0] == "start" for command in daemon.commands) == 1
    assert sum(command[0] == "create" for command in daemon.commands) == 1
    states = [state for _, state in projected]
    assert "waiting_for_capacity" in states
    assert not any(command[0] == "info" for command in daemon.commands)


async def test_cancel_while_waiting_for_a_slot(tmp_path: Path) -> None:
    """Cancellation during a capacity wait ends the job without a start.

    MoonLadderStudios/MoonMind#4457 R3: the production workflow owns the
    wait, so a cancel signal while parked terminates the lifecycle instead
    of leaking a waiter or starting a canceled job.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _HermeticSystemDaemon()
    daemon.ps_full_for_first_calls = 10_000
    published: list = []
    projected: list = []
    job_id = "container-job:" + "b" * 32
    backend, runtime = _harness(tmp_path, daemon, published, projected)
    workflow_queue = f"container-job-plan-a-cancel-{uuid4()}"
    activity_queue = settings.temporal.activity_agent_runtime_task_queue
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
                _workflow_input(job_id, "alpine:3.20", workspace,
                               timeout_seconds=300),
                id=f"container-job-plan-a-cancel-{uuid4()}",
                task_queue=workflow_queue,
            )
            await handle.signal("cancel")
            result = await handle.result()

    assert result["state"] == "canceled"
    assert not any(command[0] == "start" for command in daemon.commands)
    assert backend is not None


async def test_reconcile_before_retry_starts_nothing_twice(tmp_path: Path) -> None:
    """A reattached running container skips create and start entirely.

    MoonLadderStudios/MoonMind#4457 R2: worker death after a successful start
    retries into reconcile, which reattaches to the existing owned container.
    The container finishes between observation and retry, so the run succeeds
    with no duplicate side effect and cleanup still removes the consumer.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    job_id = "container-job:" + "c" * 32
    ownership_token = f"{job_id}:v1"
    daemon = _HermeticSystemDaemon()
    published: list = []
    projected: list = []

    original_run = daemon.run

    async def reconciling_run(raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        if command[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                json.dumps({"moonmind.ownership": ownership_token}).encode(),
                b"",
            )
        if command[:3] == ("inspect", "--format", "{{.State.Running}}"):
            return 0, b"true", b""
        return await original_run(raw)

    daemon.run = reconciling_run  # type: ignore[method-assign]

    result = await _run_journey(
        tmp_path, daemon, _workflow_input(job_id, "alpine:3.20", workspace),
        published, projected,
    )

    assert result["state"] == "succeeded"
    assert not any(command[0] == "create" for command in daemon.commands)
    assert not any(command[0] == "start" for command in daemon.commands)
    assert any(command[0] == "rm" for command in daemon.commands), (
        "the reattached consumer must still be removed; no abandoned consumer"
    )


@_REQUIRES_DOCKER
def test_real_lock_released_by_worker_death(tmp_path: Path) -> None:
    """A SIGKILLed holder releases the shared mount; the next worker acquires.

    MoonLadderStudios/MoonMind#4457 R1/R2: the capacity lock is OS-held, so
    worker death before, during, or after start cannot wedge admission for
    survivors sharing the lock mount.
    """

    async def scenario() -> str:
        lock_root = tmp_path / "capacity"
        key = f"plan-a-death-{uuid4().hex[:8]}"
        script = (
            "import asyncio,sys;"
            "from pathlib import Path;"
            "from moonmind.workflows.temporal.container_job_backend import "
            "FilesystemCapacityAdmissionLock;"
            f"lock=FilesystemCapacityAdmissionLock(Path({str(lock_root)!r}));"
            "lease=asyncio.run(lock.acquire("
            f"{key!r},wait_seconds=10.0,poll_seconds=0.05));"
            "asyncio.run(asyncio.sleep(3600))"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(2.0)
        proc.send_signal(signal.SIGKILL)
        await proc.wait()
        successor = FilesystemCapacityAdmissionLock(lock_root)
        lease = await asyncio.wait_for(
            successor.acquire(key, wait_seconds=5.0, poll_seconds=0.05),
            timeout=10,
        )
        await successor.release(lease)
        return "reacquired"

    assert asyncio.run(scenario()) == "reacquired"


@_REQUIRES_DOCKER
def test_real_slot_cap_never_exceeded_and_releases(tmp_path: Path) -> None:
    """Overlapping real starts never exceed limit 1; release readmits.

    MoonLadderStudios/MoonMind#4457 R1: two disposable containers race for
    the final slot through the production owners on the real daemon. The
    loser is refused (never started), and stopping the holder readmits it.
    Multiple created waiters still make progress rather than deadlocking.
    """

    image = _ensure_plan_a_image()
    # Real production container names derived from valid job ids, tagged with
    # a disposable run label so the finally-cleanup below can identify them.
    run_tag = f"moonmind-test-plan-a-{uuid4().hex[:12]}"
    first_request = _plan_a_activity_request(tmp_path, job_id="container-job:" + "d" * 32)
    second_request = _plan_a_activity_request(tmp_path, job_id="container-job:" + "e" * 32)
    first = DockerContainerJobBackend._name(first_request)
    second = DockerContainerJobBackend._name(second_request)
    created: list[str] = []
    try:
        for name in (first, second):
            _docker(
                "create", "--name", name,
                "--label", "moonmind.container_job=plan-a-4457",
                "--label", f"moonmind.test_run={run_tag}",
                image, "sleep", "300",
            )
            created.append(name)
        _docker("start", first)

        async def scenario() -> None:
            backend = DockerContainerJobBackend(
                workspace_root=tmp_path,
                backend_ref="plan-a-test",
            )
            holders = await backend._slot_holders()
            assert first in holders, f"real holder missing: {holders}"
            assert holders[first] == "running"
            assert second not in holders, (
                "a merely created waiter holds no slot"
            )
            second_request.wait_for_capacity = False
            with pytest.raises(ContainerJobBackendError) as refused:
                await backend._admit_job_slot(second_request, container_name=second)
            assert (
                refused.value.failure_class
                is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
            )
            _docker("stop", "--time", "1", first)
            _docker("rm", "--force", first)
            created.remove(first)
            # The released slot readmits the created waiter: forward progress.
            await backend._admit_job_slot(second_request, container_name=second)

        asyncio.run(scenario())
        states = _docker("ps", "--all", "--filter",
                         "label=moonmind.container_job=plan-a-4457",
                         "--format", "{{.Names}}\t{{.State}}")
        running = [
            line for line in states.splitlines()
            if line.strip().endswith("\trunning")
        ]
        assert len(running) <= 1, f"cap exceeded on the real daemon: {running}"
    finally:
        for name in created:
            _docker_quiet("stop", "--time", "1", name)
            _docker_quiet("rm", "--force", name)


@_REQUIRES_DOCKER
def test_real_stock_limits_inspected_on_the_daemon(tmp_path: Path) -> None:
    """The stock 2 CPU / 4 GiB limits land on the real container config.

    MoonLadderStudios/MoonMind#4457 R4: through the production create owner
    on the normal CLI-to-container route, the real Docker configuration
    carries the stock quotas and PID bound with no machine-budget probe.
    """

    image = _ensure_plan_a_image()
    (tmp_path / "art_workspace").mkdir(exist_ok=True)
    (tmp_path / "art_workspace" / "result.txt").write_text("passed\n")
    commands: list[tuple[str, ...]] = []

    async def scenario() -> str:
        backend = DockerContainerJobBackend(
            workspace_root=tmp_path,
            backend_ref="plan-a-test",
        )
        inner = backend._runner

        async def recording_runner(args: Any) -> tuple[int, bytes, bytes]:
            commands.append(tuple(str(item) for item in args))
            return await inner(args)

        backend._runner = recording_runner  # type: ignore[method-assign]
        request = _plan_a_activity_request(
            tmp_path,
            job_id=f"container-job:{uuid4().hex[:8]}{'0' * 24}",
            resources={"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
        )
        request.resolved_image_ref = image
        created = await backend.create_container(request)
        assert created.container_ref is not None
        return created.container_ref

    name = asyncio.run(scenario())
    try:
        assert not any(command[0] == "info" for command in commands)
        raw = _docker("inspect", "--format", "{{json .HostConfig}}", name)
        host_config = json.loads(raw)
        assert host_config["NanoCpus"] == 2_000_000_000
        assert host_config["Memory"] == 4 * 1024**3
        assert host_config["PidsLimit"] == 512
    finally:
        _docker_quiet("rm", "--force", name)


def _ensure_plan_a_image() -> str:
    """Return a small local image ref, pulling once when the daemon allows."""

    candidate = os.getenv("MOONMIND_PLAN_A_TEST_IMAGE", "").strip() or "alpine:3.20"
    inspected = subprocess.run(
        ["docker", "image", "inspect", candidate],
        capture_output=True,
        check=False,
    )
    if inspected.returncode != 0:
        pulled = subprocess.run(
            ["docker", "pull", candidate],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if pulled.returncode != 0:
            pytest.skip(f"test image {candidate} is unavailable on this daemon")
    return candidate


def _docker(*args: str) -> str:
    completed = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"docker {' '.join(args)} failed: {completed.stderr.strip()[:500]}"
    )
    return (completed.stdout or "").strip()


def _docker_quiet(*args: str) -> None:
    subprocess.run(["docker", *args], capture_output=True, check=False)


def _plan_a_activity_request(
    tmp_path: Path, *, job_id: str, resources: dict | None = None
):
    from moonmind.schemas.container_job_models import ContainerJobActivityRequest

    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": f"plan-a:{job_id}",
                "source": {"source": "workflow", "workflowId": f"plan-a:{job_id}"},
                "spec": {
                    "image": "alpine:3.20",
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
                    "command": ["true"],
                    "resources": resources
                    or {"cpuMillis": 1000, "memoryMiB": 512},
                    "timeoutSeconds": 60,
                },
            },
            "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
            "resolvedImageRef": "sha256:" + "a" * 64,
        }
    )
    return request
