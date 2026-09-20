"""Plan A count-only admission qualification for MoonMind#4457.

Focused integration coverage for the fixed-request launch path owned by
``moonmind/workflows/temporal/container_job_backend.py``:

* R1: two worker-side backends sharing the supported filesystem lock mount
  race for the final slot at limit 1; overlapping running containers never
  exceed the cap and created-only waiters make forward progress. A
  Docker-gated variant below races two worker *processes* on real
  containers and observes the overlap through the daemon (Docker-backed
  CI; skips without a daemon).
* R2: lost start acknowledgments and worker death before/during/after start
  reconcile the existing container before retry (no duplicate start side
  effect, no abandoned live consumer, no premature slot reuse), including a
  container finishing between observation and retry. A Docker-gated
  variant injects the lost ack on the real ``docker start`` path and
  reconciles through the daemon ledger.
* R3: slot waiting, release, cancellation refusal, and restart through the
  production ``start_container`` boundary, plus agent-host/job-ledger
  separation -- and wait/release/proceed/restart plus host-full
  subordinate execution through the production workflow with its registered
  Activities.
* R4: the normal CLI-to-container route carries the stock 2 CPU / 4 GiB /
  PID bound to Docker verbatim with no pool probe or resource helper; a
  real-Docker inspect case (skipped without a daemon) checks the created
  container config; and the Batch PR Resolver preset route (resolver run
  request, scoped capability, canonical submission) reaches host execution
  with isolated fixtures.

Unlike ``test_container_job_authority_journey.py`` (hermetic ``ps``-empty
doubles, sequential runs), these tests use separate backend/lock instances
sharing one lock root -- the supported cross-worker mount shape -- and drive
concurrent ``start_container`` calls against a stateful fake daemon that
implements the Docker ``ps``/``start`` contract, including ``created`` states
that must not count toward the cap. No new resource-management subsystem,
pool helper, ledger, or CI platform is introduced.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.container_job_cli import python_test_submission
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobWorkflowInput,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.container_job_backend import (
    LABEL_OWNERSHIP,
    DockerContainerJobBackend,
    FilesystemCapacityAdmissionLock,
)
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    build_resolver_run_request,
)
from tests.unit.omnigent.test_generic_plane_production_boundary_concurrency import (
    _catalog,
    _compile_kwargs,
    _ready_opencode_image_pair,  # noqa: F401 - autouse image-pair fixture
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
    pytest.mark.asyncio,
]

_CLI_ENV = {
    "MOONMIND_URL": "http://api:8000",
    "MOONMIND_AGENT_RUN_ID": "mm:plan-a-r4",
    "MOONMIND_RUNTIME_ID": "codex_cli",
    "MOONMIND_TASK_WORKFLOW_ID": "mm:plan-a-r4",
    "MOONMIND_CONTAINER_JOBS_SESSION_ID": "sess-plan-a",
}


def _job_id() -> str:
    return "container-job:" + uuid.uuid4().hex


def _request(
    tmp_path: Path,
    job_id: str,
    *,
    wait_for_capacity: bool = True,
    resources: dict[str, Any] | None = None,
    container_ref: str | None = None,
) -> ContainerJobActivityRequest:
    payload = {
        "jobId": job_id,
        "ownershipToken": f"{job_id}:v1",
        "request": {
            "idempotencyKey": f"plan-a:{job_id}",
            "source": {"source": "workflow", "workflowId": f"plan-a:{job_id}"},
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
                "command": ["python", "-V"],
                "resources": resources
                or {"cpuMillis": 1000, "memoryMiB": 512, "pids": 64},
                "timeoutSeconds": 60,
            },
        },
        "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
        "resolvedImageRef": "sha256:" + "a" * 64,
        "waitForCapacity": wait_for_capacity,
    }
    if container_ref is not None:
        payload["containerRef"] = container_ref
    return ContainerJobActivityRequest.model_validate(payload)


class _FakeDockerDaemon:
    """Stateful fake implementing the Docker ps/start contract under test.

    Tracks every command, the per-container lifecycle state, the peak number
    of concurrently ``running`` containers (the overlapping-execution cap
    signal), and how many ``start`` calls actually transitioned a container
    (duplicate starts of an already-running container are idempotent and must
    not count as new side effects).
    """

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.states: dict[str, str] = {}
        self.running_now = 0
        self.max_overlap = 0
        self.start_calls = 0
        self.real_starts = 0
        self.drop_next_start_ack = False

    def _record_running(self) -> None:
        self.running_now = sum(1 for s in self.states.values() if s == "running")
        self.max_overlap = max(self.max_overlap, self.running_now)

    async def run(self, raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        self.commands.append(command)
        if command[0] == "ps":
            lines = "\n".join(
                f"{name}\t{state}" for name, state in self.states.items()
            )
            return 0, lines.encode(), b""
        if command[0] == "start":
            self.start_calls += 1
            name = command[1]
            if self.states.get(name) != "running":
                self.real_starts += 1
            self.states[name] = "running"
            self._record_running()
            if self.drop_next_start_ack:
                # The daemon applied the start but the acknowledgment never
                # reached the worker (lost ack / worker death during start).
                self.drop_next_start_ack = False
                raise RuntimeError("injected lost start acknowledgment")
            return 0, name.encode(), b""
        if command[0] == "stop":
            self.states[command[1]] = "exited"
            self._record_running()
            return 0, b"", b""
        if command[0] == "inspect":
            # Containers the daemon never created read as absent (its 404),
            # so create-path ownership checks behave like production. Known
            # containers report an empty label set.
            name = command[-1]
            if name not in self.states:
                return 1, b"", f"Error: No such object: {name}".encode()
            return 0, b"{}", b""
        if command[0] == "create":
            return 0, command[command.index("--name") + 1].encode(), b""
        return 0, b"", b""

    def ps_count(self) -> int:
        return sum(1 for c in self.commands if c[0] == "ps")

    def info_count(self) -> int:
        return sum(1 for c in self.commands if c[0] == "info")


def _backends(
    tmp_path: Path, daemon: _FakeDockerDaemon, *, count: int = 2
) -> list[DockerContainerJobBackend]:
    lock_root = tmp_path / "capacity-locks"
    return [
        DockerContainerJobBackend(
            workspace_root=tmp_path,
            command_runner=daemon.run,
            # Separate lock instances sharing one root: the supported
            # cross-worker mount shape. Serialization between them relies on
            # the OS-held flock, never on a shared in-process lock.
            capacity_lock=FilesystemCapacityAdmissionLock(lock_root),
        )
        for _ in range(count)
    ]


async def test_final_slot_race_never_exceeds_cap(tmp_path: Path) -> None:
    """R1: two workers race for one free slot at limit 1 (default).

    Both racers observe the slot concurrently; the shared filesystem lock
    serializes enumerate-then-start so exactly one wins and overlapping
    running containers never exceed the cap. The loser parks durably.
    """
    daemon = _FakeDockerDaemon()
    first_backend, second_backend = _backends(tmp_path, daemon)
    first = _request(tmp_path, _job_id())
    second = _request(tmp_path, _job_id())

    first_result, second_result = await asyncio.gather(
        first_backend.start_container(first),
        second_backend.start_container(second),
    )

    assert isinstance(first_result, ContainerJobActivityResult)
    assert isinstance(second_result, ContainerJobActivityResult)
    parked = [r for r in (first_result, second_result) if r.capacity_wait]
    started = [r for r in (first_result, second_result) if not r.capacity_wait]
    # Exactly one racer wins the free slot; the other parks instead of
    # overlapping it or deadlocking.
    assert len(started) == 1, "exactly one racer must win the free slot"
    assert len(parked) == 1, "the loser must park behind the won slot"
    assert all(r.running is True for r in started)
    assert daemon.max_overlap <= 1, (
        f"overlapping running containers exceeded the cap: {daemon.max_overlap}"
    )
    assert daemon.real_starts == 1


async def test_created_waiters_make_forward_progress(tmp_path: Path) -> None:
    """R1: N+1 created waiters at limit N never deadlock on each other."""
    daemon = _FakeDockerDaemon()
    first_backend, second_backend = _backends(tmp_path, daemon)
    first = _request(tmp_path, _job_id())
    second = _request(tmp_path, _job_id())
    # Both jobs have created-only containers: created claims no slot, so the
    # first admission must succeed rather than observing the other waiter.
    daemon.states[DockerContainerJobBackend._name(first)] = "created"
    daemon.states[DockerContainerJobBackend._name(second)] = "created"

    first_result, second_result = await asyncio.gather(
        first_backend.start_container(first),
        second_backend.start_container(second),
    )

    progressed = [r for r in (first_result, second_result) if r.capacity_wait is None]
    waited = [r for r in (first_result, second_result) if r.capacity_wait is not None]
    # Exactly one start per free slot; the other parks durably instead of
    # deadlocking, then proceeds once the slot is released.
    assert len(progressed) == 1, "one created waiter must claim the free slot"
    assert len(waited) == 1, "the other created waiter must park, not deadlock"
    assert daemon.real_starts == 1
    assert daemon.max_overlap <= 1

    winner = first if first_result.capacity_wait is None else second
    loser_backend = second_backend if winner is first else first_backend
    loser = second if winner is first else first
    daemon.states[DockerContainerJobBackend._name(winner)] = "exited"
    daemon._record_running()
    retry = await loser_backend.start_container(loser)
    assert retry.capacity_wait is None
    assert retry.running is True
    assert daemon.max_overlap <= 1


def _capacity_lock_worker(lock_root: str, key: str, tag: str, order_path: str) -> None:
    """Hold the shared capacity lock across a widened race window.

    Top-level so ``spawn``-context worker processes can import it. Each
    worker builds its own lock instance over the shared root -- the
    supported cross-worker mount shape -- so serialization relies only on
    the OS-held flock, never on shared in-process state.
    """

    import asyncio

    from moonmind.workflows.temporal.container_job_backend import (
        FilesystemCapacityAdmissionLock,
    )

    async def _main() -> None:
        lock = FilesystemCapacityAdmissionLock(lock_root)
        lease = await lock.acquire(key, wait_seconds=30, poll_seconds=0.01)
        try:
            with open(order_path, "a", encoding="utf-8") as handle:
                handle.write(f"enter-{tag}\n")
            await asyncio.sleep(0.3)
            with open(order_path, "a", encoding="utf-8") as handle:
                handle.write(f"exit-{tag}\n")
        finally:
            await lock.release(lease)

    asyncio.run(_main())


def _process_context() -> Any:
    try:
        return multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - non-POSIX hosts
        return multiprocessing.get_context("spawn")


async def test_capacity_lock_serializes_two_worker_processes(
    tmp_path: Path,
) -> None:
    """R1: two independent worker processes serialize on the shared lock.

    The same-process race below proves enumerate-then-start admission; this
    test proves the mechanism it relies on across real process boundaries:
    two separate lock instances in two OS processes sharing one lock root
    never hold the lock together, so two workers racing for the final slot
    cannot both observe it free.
    """
    lock_root = tmp_path / "capacity-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    order_path = tmp_path / "order.log"
    order_path.write_text("", encoding="utf-8")
    ctx = _process_context()
    processes = [
        ctx.Process(
            target=_capacity_lock_worker,
            args=(str(lock_root), "plan-a-r1-process-race", tag, str(order_path)),
        )
        for tag in ("a", "b")
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            await asyncio.to_thread(process.join, 30)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
    assert all(process.exitcode == 0 for process in processes)
    lines = order_path.read_text(encoding="utf-8").split()
    assert len(lines) == 4, f"both workers must log enter/exit: {lines}"
    pairs = {("enter-a", "exit-a"), ("enter-b", "exit-b")}
    assert (lines[0], lines[1]) in pairs, f"lock overlapped: {lines}"
    assert (lines[2], lines[3]) in pairs, f"lock overlapped: {lines}"


async def test_lost_start_ack_reconciles_without_duplicate_side_effect(
    tmp_path: Path,
) -> None:
    """R2: a start applied by the daemon but unacknowledged is reconciled."""
    daemon = _FakeDockerDaemon()
    daemon.drop_next_start_ack = True
    (backend,) = _backends(tmp_path, daemon, count=1)
    request = _request(tmp_path, _job_id())

    with pytest.raises(RuntimeError, match="lost start acknowledgment"):
        await backend.start_container(request)

    # The container is provably running on the daemon despite the lost ack.
    name = DockerContainerJobBackend._name(request)
    assert daemon.states[name] == "running"
    holders = await backend._slot_holders()
    assert holders.get(name) == "running"

    # Retry reconciles first: the job already holds its slot, so admission
    # succeeds under a full count and `docker start` stays idempotent -- one
    # real side effect, no duplicate execution, no abandoned consumer.
    retry = await backend.start_container(request)
    assert retry.running is True
    assert daemon.real_starts == 1, "retry must not launch a second container"
    assert daemon.states[name] == "running"


async def test_worker_death_releases_capacity_lock_for_next_worker(
    tmp_path: Path,
) -> None:
    """R2: a worker lost mid-admission cannot wedge the shared lock."""
    import os

    lock_root = tmp_path / "capacity-locks"
    victim = FilesystemCapacityAdmissionLock(lock_root)
    survivor = FilesystemCapacityAdmissionLock(lock_root)
    lease = await victim.acquire(
        "dead-worker-key", wait_seconds=5, poll_seconds=0.01
    )
    # Simulate worker death: close the fd without the userspace release, as
    # the OS does on process exit (flock is released with the file description).
    os.close(lease.file_descriptor)
    next_lease = await asyncio.wait_for(
        survivor.acquire("dead-worker-key", wait_seconds=5, poll_seconds=0.01),
        timeout=5,
    )
    await survivor.release(next_lease)


async def test_worker_death_before_start_leaves_no_side_effect(
    tmp_path: Path,
) -> None:
    """R2: a worker lost before its start leaves nothing to reconcile.

    The victim holds the shared admission lock and dies before any Docker
    call (the OS releases the flock with the file description). The next
    worker admits and starts cleanly: no wedged lock, no phantom container,
    exactly one side effect.
    """
    daemon = _FakeDockerDaemon()
    survivor, _spare = _backends(tmp_path, daemon, count=2)
    victim = FilesystemCapacityAdmissionLock(tmp_path / "capacity-locks")
    lease = await victim.acquire(
        survivor._capacity_lock_key(), wait_seconds=5, poll_seconds=0.01
    )
    # Simulate worker death before the start: close the fd without the
    # userspace release, as the OS does on process exit.
    os.close(lease.file_descriptor)
    assert daemon.states == {}

    request = _request(tmp_path, _job_id())
    started = await survivor.start_container(request)

    assert started.running is True
    assert daemon.states == {DockerContainerJobBackend._name(request): "running"}
    assert daemon.real_starts == 1


async def test_container_finishing_between_observation_and_retry(
    tmp_path: Path,
) -> None:
    """R2: a container that exits after admission frees its slot on retry."""
    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    request = _request(tmp_path, _job_id())
    first = await backend.start_container(request)
    assert first.running is True
    name = DockerContainerJobBackend._name(request)
    # The container finishes between the worker's observation and its retry.
    daemon.states[name] = "exited"
    daemon._record_running()
    holders = await backend._slot_holders()
    assert name not in holders, "an exited container must release its slot"

    other = _request(tmp_path, _job_id())
    admitted = await backend.start_container(other)
    assert admitted.running is True
    assert daemon.max_overlap <= 1


async def test_own_paused_container_keeps_its_slot_on_retry(tmp_path: Path) -> None:
    """R2: a retry for a slot-holding own container never parks or recounts."""
    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    request = _request(tmp_path, _job_id())
    own = DockerContainerJobBackend._name(request)
    daemon.states[own] = "paused"
    daemon.states["moonmind-container-job-other"] = "running"
    daemon._record_running()

    result = await backend.start_container(request)

    assert result.running is True, "own paused holder must be admitted"
    assert daemon.real_starts == 1


async def test_slot_wait_release_cancel_and_restart(tmp_path: Path) -> None:
    """R3: wait, release on stop, non-waitable refusal, and restart."""
    daemon = _FakeDockerDaemon()
    daemon.states["moonmind-container-job-holder"] = "running"
    daemon._record_running()
    (backend,) = _backends(tmp_path, daemon, count=1)

    waiter = _request(tmp_path, _job_id(), wait_for_capacity=True)
    parked = await backend.start_container(waiter)
    assert parked.capacity_wait is not None
    assert "container-job slot" in parked.capacity_wait
    assert daemon.start_calls == 0, "a parked job must not reach docker start"

    refused = _request(tmp_path, _job_id(), wait_for_capacity=False)
    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(refused)
    assert (
        raised.value.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
    )

    # Release via stop, then the waiter proceeds and restarts cleanly.
    daemon.states["moonmind-container-job-holder"] = "exited"
    daemon._record_running()
    proceeded = await backend.start_container(waiter)
    assert proceeded.running is True
    name = DockerContainerJobBackend._name(waiter)
    daemon.states[name] = "exited"
    daemon._record_running()
    restarted = await backend.start_container(waiter)
    assert restarted.running is True
    assert daemon.max_overlap <= 1


async def test_agent_host_and_job_counts_stay_separate(tmp_path: Path) -> None:
    """R3: a full host ledger never blocks a subordinate test job."""
    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    # All 8 generic hosts are occupied, yet the job ledger is empty.
    hosts_full = evaluate_generic_host_capacity(
        active_hosts=8,
        recent_cold_launches=0,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )
    assert hosts_full.admitted is False
    job = _request(tmp_path, _job_id())
    admitted = await backend.start_container(job)
    assert admitted.running is True, (
        "an agent holding the last host slot can still run its test job"
    )

    # Conversely a full job ledger parks the next job without touching hosts.
    daemon.states["moonmind-container-job-holder"] = "running"
    daemon._record_running()
    holders = await backend._slot_holders()
    assert len([n for n in holders if n != DockerContainerJobBackend._name(job)]) >= 1


async def test_stock_cli_route_reaches_docker_verbatim_without_probe(
    tmp_path: Path,
) -> None:
    """R4: stock 2 CPU / 4 GiB / PID bound, no pool probe or helper."""
    submission = python_test_submission(
        ["tests/unit/example.py"], env=dict(_CLI_ENV)
    )
    resources = submission["spec"]["resources"]
    assert resources["cpuMillis"] == 2000
    assert resources["memoryMiB"] == 4096
    assert resources["pids"] == 512
    assert "pool" not in json.dumps(submission).lower()

    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    request = _request(
        tmp_path,
        _job_id(),
        resources={"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
    )
    result = await backend.start_container(request)
    assert result.running is True
    assert daemon.info_count() == 0, "Plan A performs no machine-budget probe"
    assert daemon.ps_count() == 1, "each start performs one slot enumeration"


async def test_stock_create_carries_fixed_limits_verbatim(
    tmp_path: Path,
) -> None:
    """R4: stock 2 CPU / 4 GiB / PID bound reach `docker create` verbatim.

    The hermetic complement to the real-Docker inspect below: the normal
    CLI-to-container route carries the stock Batch PR Resolver limits to the
    daemon boundary with no pool probe, no helper, and no cgroup parent.
    """
    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    request = _request(
        tmp_path,
        _job_id(),
        resources={"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
    )
    created = await backend.create_container(request)

    assert created.container_ref == DockerContainerJobBackend._name(request)
    creates = [c for c in daemon.commands if c[0] == "create"]
    assert len(creates) == 1
    command = " ".join(creates[0])
    assert "--cpus 2.0" in command
    assert "--memory 4096m" in command
    assert "--pids-limit 512" in command
    assert "--cgroup-parent" not in command
    assert "pool" not in command.lower()
    assert daemon.info_count() == 0, "Plan A performs no machine-budget probe"


@pytest.mark.skipif(shutil.which("docker") is None, reason="requires the Docker CLI")
async def test_real_docker_inspect_shows_stock_fixed_limits(tmp_path: Path) -> None:
    """R4: real-Docker config inspection for the stock limits (CI Docker)."""
    import subprocess

    probing = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probing.returncode != 0:
        pytest.skip(
            "requires a reachable Docker daemon: "
            f"{probing.stderr.strip()[:200]}"
        )

    del tmp_path  # real Docker needs no fixture workspace
    name = f"moonmind-test-plan-a-{uuid.uuid4().hex[:12]}"
    try:
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--cpus",
                "2",
                "--memory",
                "4096m",
                "--pids-limit",
                "512",
                "alpine:3.20",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert created.returncode == 0, created.stderr
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .HostConfig}}", name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert inspected.returncode == 0, inspected.stderr
        host_config = json.loads(inspected.stdout)
        assert host_config["NanoCpus"] == 2_000_000_000
        assert host_config["Memory"] == 4096 * 1024 * 1024
        assert host_config["PidsLimit"] == 512
        assert host_config.get("CgroupParent", "") in ("", None)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            timeout=120,
        )


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


class _PlanAWorkflowDaemon:
    """Full-lifecycle hermetic daemon with an optional pre-seeded holder.

    Unlike the start-path fake above, this daemon answers the whole
    production lifecycle (workspace/image acquisition, create, start, stop,
    inspect, logs) so the workflow journeys below drive the real registered
    Activities instead of calling the backend directly. With a pre-seeded
    holder occupying the only slot at limit 1, the workflow parks in
    ``WAITING_FOR_CAPACITY`` until the holder is released or the workflow
    is cancelled. Without a holder the slot is free and the workflow runs
    to ``succeeded`` once its container finishes. ``max_overlap`` tracks
    the peak number of concurrently running containers (the R1 cap signal)
    across the whole driven lifecycle.
    """

    def __init__(
        self, *, holder_name: str | None, ownership_token: str
    ) -> None:
        self.images = {"alpine:3.20"}
        self.commands: list[tuple[str, ...]] = []
        self.states: dict[str, str] = (
            {holder_name: "running"} if holder_name is not None else {}
        )
        self.created: dict[str, str] = {}
        self.holder_token = ownership_token
        self.max_overlap = 0

    def _record_running(self) -> None:
        running = sum(1 for state in self.states.values() if state == "running")
        self.max_overlap = max(self.max_overlap, running)

    async def run(self, raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        self.commands.append(command)
        if command[0] == "ps":
            lines = "\n".join(
                f"{name}\t{state}" for name, state in self.states.items()
            )
            return 0, lines.encode(), b""
        if command[0] == "image" and len(command) > 1 and command[1] == "inspect":
            image = command[-1]
            if image not in self.images:
                return 1, b"", b"Error: No such image"
            if "--format" in command:
                return 0, _DIGEST.encode(), b""
            return 0, f"{_DIGEST}\t{image}@{_DIGEST}".encode(), b""
        if command[0] == "pull":
            self.images.add(command[1])
            return 0, b"pulled", b""
        if command[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            name = command[-1]
            if name in self.created:
                labels = {LABEL_OWNERSHIP: self.created[name]}
                return 0, json.dumps(labels).encode(), b""
            if name in self.states:
                labels = {LABEL_OWNERSHIP: self.holder_token}
                return 0, json.dumps(labels).encode(), b""
            return 1, b"", f"Error: No such object: {name}".encode()
        if command[:3] == ("inspect", "--format", "{{json .State}}"):
            running = self.states.get(command[-1]) == "running"
            return 0, json.dumps({"Running": running, "ExitCode": 0}).encode(), b""
        if command[0] == "create":
            name = command[command.index("--name") + 1]
            token = next(
                (
                    item.split("=", 1)[1]
                    for item in command
                    if item.startswith(f"{LABEL_OWNERSHIP}=")
                ),
                "",
            )
            self.created[name] = token
            self.states[name] = "created"
            return 0, name.encode(), b""
        if command[0] == "start":
            self.states[command[1]] = "running"
            self._record_running()
            return 0, command[1].encode(), b""
        if command[0] == "stop":
            self.states[command[-1]] = "exited"
            self._record_running()
            return 0, b"", b""
        if command[0] == "rm":
            self.states.pop(command[-1], None)
            self.created.pop(command[-1], None)
            self._record_running()
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
                "idempotencyKey": f"plan-a-workflow:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"plan-a-workflow:{job_id}",
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
                    "timeoutSeconds": 60,
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


async def test_capacity_wait_cancel_through_production_workflow(
    tmp_path: Path,
) -> None:
    """R3: slot waiting and the cancel signal through the real workflow.

    The production ``MoonMindContainerJobWorkflow`` with its registered
    Activities parks in ``WAITING_FOR_CAPACITY`` behind a running holder,
    then honors the ``cancel`` signal: no ``docker start`` for the parked
    job, the created container is stopped during cancellation, and the
    terminal state is ``canceled``.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    job_id = "container-job:" + "9" * 32
    holder_token = "container-job:holder:v1"
    daemon = _PlanAWorkflowDaemon(
        holder_name="moonmind-container-job-holder",
        ownership_token=holder_token,
    )
    published: list[tuple[str, str, bytes]] = []
    projected: list[tuple[str, str]] = []

    async def publish(request: Any, name: str, payload: bytes) -> str:
        published.append((request.job_id, name, payload))
        return f"artifact:{len(published)}"

    async def project(request: Any) -> None:
        state = request.state or request.terminal_state
        projected.append((request.job_id, getattr(state, "value", state)))

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        backend_ref="system-proxy",
        docker_host="tcp://dockerproxy:2375",
        command_runner=daemon.run,
        evidence_publisher=publish,
        projection_writer=project,
        image_lock_root=tmp_path / "image-locks",
        workspace_volume_name="agent_workspaces",
        settings=resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
        ),
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    workflow_queue = f"container-job-plan-a-{uuid.uuid4()}"
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
                _workflow_input(job_id, workspace),
                id=f"container-job-plan-a-{uuid.uuid4()}",
                task_queue=workflow_queue,
            )
            for _ in range(200):
                status = await handle.query("status")
                if "waiting_for_capacity" in str(status.get("state")).lower():
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("workflow never parked in WAITING_FOR_CAPACITY")
            await handle.signal("cancel")
            result = await handle.result()

    assert result["state"] == "canceled"
    assert job_id in {owner for owner, _ in projected}
    assert "waiting_for_capacity" in {state for _, state in projected}
    assert "canceled" in {state for _, state in projected}
    assert not any(command[0] == "start" for command in daemon.commands), (
        "a parked job must never reach docker start"
    )
    assert sum(command[0] == "create" for command in daemon.commands) == 1
    assert any(command[0] == "stop" for command in daemon.commands), (
        "cancellation must stop the created container"
    )
    assert not any(command[0] == "info" for command in daemon.commands)


# ------------------------------------------------- real-Docker R1/R2 journeys
#
# The tests below qualify the same admission owners against a reachable
# Docker daemon: two worker processes sharing the supported lock mount race
# on real containers, and a lost start acknowledgment reconciles the
# daemon-observed container before retry. They skip without a reachable
# daemon (local and daemon-less managed runs) and run in Docker-backed
# required CI. No new subsystem is introduced: the backends under test use
# the production ``docker`` command path with disposable ``moonmind-test-*``
# containers and isolated fixtures.

_REAL_TEST_IMAGE = "alpine:3.20"


def _require_real_docker_image() -> None:
    """Skip unless the Docker CLI, a reachable daemon, and the fixture image exist."""
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("requires the Docker CLI")
    probing = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probing.returncode != 0:
        pytest.skip(
            "requires a reachable Docker daemon: "
            f"{probing.stderr.strip()[:200]}"
        )
    pulled = subprocess.run(
        ["docker", "pull", _REAL_TEST_IMAGE],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if pulled.returncode != 0:
        pytest.skip(
            f"requires fixture image {_REAL_TEST_IMAGE}: "
            f"{pulled.stderr.strip()[:200]}"
        )


async def _real_docker_run(raw: Any) -> tuple[int, bytes, bytes]:
    """Production-shaped Docker CLI runner: bare ``docker`` like the backend."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *(str(item) for item in raw),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout, stderr


async def _real_container_status(name: str) -> str:
    code, stdout, _ = await _real_docker_run(
        ("inspect", "--format", "{{.State.Status}}", name)
    )
    return stdout.decode(errors="replace").strip() if code == 0 else ""


def _real_backend(
    tmp_path: Path, lock_root: Path, runner: Any
) -> DockerContainerJobBackend:
    return DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=FilesystemCapacityAdmissionLock(lock_root),
        settings=resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
        ),
    )


def _real_docker_race_worker(
    lock_root: str, workspace_root: str, payload: dict[str, Any], queue: Any
) -> None:
    """Start one pre-created real container in a separate worker process.

    Top-level so ``spawn``-context workers can import it. Each worker
    builds its own backend over the shared lock root -- the supported
    cross-worker mount shape -- and talks to the real daemon through the
    production ``docker`` command path.
    """

    import asyncio

    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.schemas.container_job_models import (
        ContainerJobActivityRequest,
    )
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
        FilesystemCapacityAdmissionLock,
    )
    from tests.integration.reliability.test_container_job_plan_a_qualification_journey import (
        _real_docker_run,
    )

    async def _main() -> None:
        backend = DockerContainerJobBackend(
            workspace_root=workspace_root,
            command_runner=_real_docker_run,
            capacity_lock=FilesystemCapacityAdmissionLock(lock_root),
            settings=resolve_container_backend_settings(
                {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
            ),
        )
        request = ContainerJobActivityRequest.model_validate(payload)
        try:
            result = await backend.start_container(request)
            queue.put(
                {
                    "job_id": request.job_id,
                    "running": bool(result.running),
                    "parked": result.capacity_wait is not None,
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported to the parent
            queue.put({"job_id": request.job_id, "error": repr(exc)})

    asyncio.run(_main())


async def test_real_docker_two_workers_race_final_slot(tmp_path: Path) -> None:
    """R1: two worker processes race for the final slot on real Docker.

    Both containers are pre-created (``created`` claims no slot) and both
    workers share the supported lock mount at limit 1. Daemon-observed
    overlapping running containers never exceed the cap, exactly one worker
    starts, the loser parks, and it proceeds once the winner's slot is
    released. Docker-backed required CI only; skips without a daemon.
    """
    import subprocess

    _require_real_docker_image()
    lock_root = tmp_path / "capacity-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    names = [f"moonmind-test-plan-a-{uuid.uuid4().hex[:12]}" for _ in range(2)]
    requests = [
        _request(tmp_path, _job_id(), container_ref=name) for name in names
    ]
    for name, job in zip(names, requests):
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL_CONTAINER_JOB}={job.job_id}",
                _REAL_TEST_IMAGE,
                "sleep",
                "120",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert created.returncode == 0, created.stderr
    try:
        ctx = _process_context()
        queue = ctx.Queue()
        processes = [
            ctx.Process(
                target=_real_docker_race_worker,
                args=(
                    str(lock_root),
                    str(tmp_path),
                    request.model_dump(mode="json", by_alias=True),
                    queue,
                ),
            )
            for request in requests
        ]
        for process in processes:
            process.start()
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 120
            max_overlap = 0
            while any(process.is_alive() for process in processes):
                if loop.time() > deadline:
                    break
                running = 0
                for name in names:
                    # Daemon-observed overlap: count actually running
                    # containers while both workers race.
                    if await _real_container_status(name) == "running":
                        running += 1
                max_overlap = max(max_overlap, running)
                await asyncio.sleep(0.05)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                await asyncio.to_thread(process.join, 30)
        assert all(process.exitcode == 0 for process in processes), (
            "both worker processes must exit cleanly"
        )
        outcomes = [await asyncio.to_thread(queue.get, True, 60) for _ in processes]
        assert not [o for o in outcomes if "error" in o], outcomes
        started = [o for o in outcomes if o["running"]]
        parked = [o for o in outcomes if o["parked"]]
        assert len(started) == 1, f"exactly one worker must win: {outcomes}"
        assert len(parked) == 1, f"the loser must park: {outcomes}"
        assert max_overlap <= 1, (
            f"daemon-observed overlap exceeded the cap: {max_overlap}"
        )
        # Created-waiter progress on real Docker: stopping the winner frees
        # its slot and the parked loser proceeds without deadlock.
        winner = next(o["job_id"] for o in started)
        loser = next(o["job_id"] for o in parked)
        subprocess.run(
            ["docker", "stop", names[[o["job_id"] for o in outcomes].index(winner)]],
            capture_output=True,
            timeout=120,
        )
        parent = _real_backend(tmp_path, lock_root, _real_docker_run)
        retry = await parent.start_container(
            next(r for r in requests if r.job_id == loser)
        )
        assert retry.running is True
        assert await _real_container_status(
            names[[r.job_id for r in requests].index(loser)]
        ) == "running"
    finally:
        for name in names:
            subprocess.run(
                ["docker", "rm", "--force", name],
                capture_output=True,
                timeout=120,
            )


async def test_real_docker_lost_start_ack_reconciles_before_retry(
    tmp_path: Path,
) -> None:
    """R2: a lost start ack reconciles the real container before retry.

    The daemon applies the start but the acknowledgment never reaches the
    worker; the retry must observe the daemon-ledger slot holder first, so
    no duplicate container is launched and no live consumer is abandoned.
    A container finishing between observation and retry then frees its slot
    for the next job on real Docker. Docker-backed required CI only.
    """
    import subprocess

    _require_real_docker_image()
    lock_root = tmp_path / "capacity-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    first_name = f"moonmind-test-plan-a-{uuid.uuid4().hex[:12]}"
    second_name = f"moonmind-test-plan-a-{uuid.uuid4().hex[:12]}"
    first = _request(tmp_path, _job_id(), container_ref=first_name)
    second = _request(tmp_path, _job_id(), container_ref=second_name)
    for name, job in ((first_name, first), (second_name, second)):
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL_CONTAINER_JOB}={job.job_id}",
                _REAL_TEST_IMAGE,
                "sleep",
                "120",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert created.returncode == 0, created.stderr
    try:
        calls: list[tuple[str, ...]] = []
        dropped = False

        async def flaky(raw: Any) -> tuple[int, bytes, bytes]:
            nonlocal dropped
            command = tuple(str(item) for item in raw)
            calls.append(command)
            if command[:2] == ("start", first_name) and not dropped:
                dropped = True
                # The daemon applies the start; only the ack is lost.
                await _real_docker_run(command)
                raise RuntimeError("injected lost start acknowledgment")
            return await _real_docker_run(raw)

        victim = _real_backend(tmp_path, lock_root, flaky)
        with pytest.raises(RuntimeError, match="lost start acknowledgment"):
            await victim.start_container(first)
        assert dropped, "the fault injection must have fired"
        # Daemon-observed reconcile evidence: the container is provably
        # running and still holds its slot despite the lost ack.
        assert await _real_container_status(first_name) == "running"
        survivor = _real_backend(tmp_path, lock_root, _real_docker_run)
        holders = await survivor._slot_holders()
        assert holders.get(first_name) == "running"

        retry = await survivor.start_container(first)
        assert retry.running is True
        listed = subprocess.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"name={first_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert [n.strip() for n in listed.stdout.splitlines() if n.strip()] == [
            first_name
        ], "retry must not launch a duplicate container"

        # The container finishes between observation and retry: stopping it
        # releases its slot and the next created waiter proceeds.
        stopped = subprocess.run(
            ["docker", "stop", first_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert stopped.returncode == 0, stopped.stderr
        holders = await survivor._slot_holders()
        assert first_name not in holders, "a stopped container frees its slot"
        proceeded = await survivor.start_container(second)
        assert proceeded.running is True
        assert await _real_container_status(second_name) == "running"
        assert not any(command[0] == "info" for command in calls), (
            "Plan A performs no machine-budget probe"
        )
    finally:
        for name in (first_name, second_name):
            subprocess.run(
                ["docker", "rm", "--force", name],
                capture_output=True,
                timeout=120,
            )


# ------------------------------------------- production-workflow R3 journeys


@asynccontextmanager
async def _production_workflow_harness(tmp_path: Path, *, daemon: _PlanAWorkflowDaemon):
    """Share one backend/daemon across sequential production-workflow runs.

    Yields ``(client, workflow_queue, workspace, daemon, published,
    projected)`` so each journey drives the production
    ``MoonMindContainerJobWorkflow`` with its registered Activities through
    the same limit-1 backend the cancel journey uses.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    published: list[tuple[str, str, bytes]] = []
    projected: list[tuple[str, str]] = []

    async def publish(request: Any, name: str, payload: bytes) -> str:
        published.append((request.job_id, name, payload))
        return f"artifact:{len(published)}"

    async def project(request: Any) -> None:
        state = request.state or request.terminal_state
        projected.append((request.job_id, getattr(state, "value", state)))

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        backend_ref="system-proxy",
        docker_host="tcp://dockerproxy:2375",
        command_runner=daemon.run,
        evidence_publisher=publish,
        projection_writer=project,
        image_lock_root=tmp_path / "image-locks",
        workspace_volume_name="agent_workspaces",
        settings=resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
        ),
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    workflow_queue = f"container-job-plan-a-{uuid.uuid4()}"
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
            yield (env.client, workflow_queue, workspace, daemon, published, projected)


async def _await_workflow_success(
    handle: Any, daemon: _PlanAWorkflowDaemon, *, holder_name: str | None
) -> dict[str, Any]:
    """Let running job containers finish, then await the terminal result.

    The hermetic daemon never finishes a container on its own; the journey
    marks every running non-holder container exited (the observable
    completion the workflow polls for) until the workflow terminates. The
    holder, if any, is owned by the journey, never auto-finished.
    """
    result_task = asyncio.create_task(handle.result())
    for _ in range(600):
        if result_task.done():
            break
        for name, state in list(daemon.states.items()):
            if name != holder_name and state == "running":
                daemon.states[name] = "exited"
        await asyncio.sleep(0.05)
    if not result_task.done():
        result_task.cancel()
        pytest.fail("production workflow did not reach a terminal state")
    return await result_task


async def test_capacity_release_proceeds_and_restarts_through_production_workflow(
    tmp_path: Path,
) -> None:
    """R3: slot release, proceed, and restart through the real workflow.

    The production ``MoonMindContainerJobWorkflow`` parks in
    ``WAITING_FOR_CAPACITY`` behind a running holder, proceeds once the
    holder is released, and runs to ``succeeded``; a second workflow then
    restarts on the freed slot through the same Activities. Overlapping
    running containers never exceed the cap and no pool probe runs.
    """
    holder = "moonmind-container-job-holder"
    daemon = _PlanAWorkflowDaemon(
        holder_name=holder,
        ownership_token="container-job:holder:v1",
    )
    async with _production_workflow_harness(tmp_path, daemon=daemon) as (
        client,
        workflow_queue,
        workspace,
        _,
        _published,
        projected,
    ):
        first_id = "container-job:" + "7" * 32
        first = await client.start_workflow(
            MoonMindContainerJobWorkflow.run,
            _workflow_input(first_id, workspace),
            id=f"container-job-plan-a-{uuid.uuid4()}",
            task_queue=workflow_queue,
        )
        for _ in range(200):
            status = await first.query("status")
            if "waiting_for_capacity" in str(status.get("state")).lower():
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("workflow never parked in WAITING_FOR_CAPACITY")
        # Slot release through the production path: the holder finishes and
        # the parked waiter must proceed instead of waiting forever.
        daemon.states[holder] = "exited"
        result = await _await_workflow_success(first, daemon, holder_name=holder)
        assert result["state"] == "succeeded"

        # Restart through the production path: the next job reuses the
        # freed slot through the same workflow and Activities.
        second_id = "container-job:" + "8" * 32
        second = await client.start_workflow(
            MoonMindContainerJobWorkflow.run,
            _workflow_input(second_id, workspace),
            id=f"container-job-plan-a-{uuid.uuid4()}",
            task_queue=workflow_queue,
        )
        second_result = await _await_workflow_success(
            second, daemon, holder_name=holder
        )
        assert second_result["state"] == "succeeded"

    assert daemon.max_overlap <= 1, (
        f"overlapping running containers exceeded the cap: {daemon.max_overlap}"
    )
    assert not any(command[0] == "info" for command in daemon.commands)
    states = {state for _, state in projected}
    assert "waiting_for_capacity" in states
    assert "succeeded" in states


async def test_subordinate_test_job_runs_while_hosts_full_through_workflow(
    tmp_path: Path,
) -> None:
    """R3: a full host ledger never blocks a subordinate test job workflow.

    All 8 generic hosts are occupied, yet the production
    ``MoonMindContainerJobWorkflow`` still runs its subordinate test job to
    ``succeeded``: agent-host leases and the container-job slot ledger are
    counted separately through the production Activities.
    """
    hosts_full = evaluate_generic_host_capacity(
        active_hosts=8,
        recent_cold_launches=0,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )
    assert hosts_full.admitted is False
    daemon = _PlanAWorkflowDaemon(
        holder_name=None,
        ownership_token="container-job:holder:v1",
    )
    async with _production_workflow_harness(tmp_path, daemon=daemon) as (
        client,
        workflow_queue,
        workspace,
        _,
        _published,
        _projected,
    ):
        job_id = "container-job:" + "6" * 32
        handle = await client.start_workflow(
            MoonMindContainerJobWorkflow.run,
            _workflow_input(job_id, workspace),
            id=f"container-job-plan-a-{uuid.uuid4()}",
            task_queue=workflow_queue,
        )
        result = await _await_workflow_success(handle, daemon, holder_name=None)

    assert result["state"] == "succeeded", (
        "an agent holding the last host slot can still run its test job"
    )
    assert daemon.max_overlap <= 1
    assert any(command[0] == "start" for command in daemon.commands), (
        "the subordinate job must reach docker start"
    )
    assert not any(command[0] == "info" for command in daemon.commands)


# ------------------------------------------------------- preset-route R4 journey


async def test_batch_pr_resolver_preset_route_reaches_host_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: the resolver preset route reaches host execution with stock limits.

    The normal Batch PR Resolver child route -- resolver run request with an
    omnigent target runtime, scoped capability environment, canonical
    ``python_test_submission`` -- carries the stock 2 CPU / 4 GiB / PID
    bound to the production create/start boundary with no CPU-pool probe or
    resource helper. Provider/GitHub effects stay isolated: the PR ref is a
    fixture and no network call runs. The hermetic complement to the
    Docker-gated inspect case above.
    """
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "1")
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED", "1")
    plan = compile_execution_plan(**_compile_kwargs(_catalog(), "1"))
    assert plan.payload.harnessId == "opencode-native"
    assert plan.payload.executionRealizerRef == "generic-omnigent-host@1"
    selected_profile = plan.payload.credentialBindings[
        "primary-model"
    ].providerProfileRef
    child = build_resolver_run_request(
        parent_workflow_id="merge-owner",
        pull_request={
            "repo": "example/repo",
            "number": 1,
            "url": "https://github.com/example/repo/pull/1",
            "headSha": "a" * 40,
            "headBranch": "candidate",
            "baseBranch": "main",
        },
        jira_issue_key=None,
        merge_method="squash",
        resolver_template={
            "targetRuntime": "omnigent",
            "executionProfileRef": selected_profile,
        },
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "resolver-child",
            "idempotencyKey": "resolver-step",
            "parameters": child["initial_parameters"],
            "workspaceSpec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "resolver-workspace",
                    "relativePath": "repo",
                }
            },
            "stepExecution": {
                "workflowId": "resolver-child",
                "runId": "child-run",
                "logicalStepId": "node-1",
                "executionOrdinal": 1,
                "stepExecutionId": "resolver-child:child-run:node-1:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000", signing_secret="test-secret"
    ).build(
        request=request,
        plan=plan,
        host_lease_ref="resolver-lease",
        launch_policy=get_launch_policy(plan.payload.launchPolicyRef),
        workspace_attachment={"accessMode": "read-write"},
    )
    capability = verify_container_job_session_capability(
        environment["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"], secret="test-secret"
    )
    assert capability.runtime_id == "opencode-native"
    assert capability.workflow_id == "resolver-child"
    assert capability.workspace_id == "resolver-workspace"
    submission = python_test_submission(["tests/unit/example.py"], env=environment)
    resources = submission["spec"]["resources"]
    assert resources["cpuMillis"] == 2000
    assert resources["memoryMiB"] == 4096
    assert resources["pids"] == 512
    assert "pool" not in json.dumps(submission).lower()

    # Host-execution leg: the preset-route submission runs through the
    # production create/start boundary with the stock limits verbatim.
    daemon = _FakeDockerDaemon()
    (backend,) = _backends(tmp_path, daemon, count=1)
    job = _request(
        tmp_path,
        _job_id(),
        resources={
            "cpuMillis": resources["cpuMillis"],
            "memoryMiB": resources["memoryMiB"],
            "pids": resources["pids"],
        },
    )
    created = await backend.create_container(job)
    assert created.container_ref == DockerContainerJobBackend._name(job)
    started = await backend.start_container(job)
    assert started.running is True
    creates = [c for c in daemon.commands if c[0] == "create"]
    assert len(creates) == 1
    command = " ".join(creates[0])
    assert "--cpus 2.0" in command
    assert "--memory 4096m" in command
    assert "--pids-limit 512" in command
    assert "pool" not in command.lower()
    assert daemon.info_count() == 0, "Plan A performs no machine-budget probe"

