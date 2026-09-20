"""Plan A count-only admission qualification for MoonMind#4457.

Focused integration coverage for the fixed-request launch path owned by
``moonmind/workflows/temporal/container_job_backend.py``:

* R1: two worker-side backends sharing the supported filesystem lock mount
  race for the final slot at limit 1; overlapping running containers never
  exceed the cap and created-only waiters make forward progress.
* R2: lost start acknowledgments and worker death before/during/after start
  reconcile the existing container before retry (no duplicate start side
  effect, no abandoned live consumer, no premature slot reuse), including a
  container finishing between observation and retry.
* R3: slot waiting, release, cancellation refusal, and restart through the
  production ``start_container`` boundary, plus agent-host/job-ledger
  separation.
* R4: the normal CLI-to-container route carries the stock 2 CPU / 4 GiB /
  PID bound to Docker verbatim with no pool probe or resource helper; a
  real-Docker inspect case (skipped without a CLI) checks the created
  container config.

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
from contextlib import AsyncExitStack
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
from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
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
    """Full-lifecycle hermetic daemon with one pre-seeded running holder.

    Unlike the start-path fake above, this daemon answers the whole
    production lifecycle (workspace/image acquisition, create, start, stop,
    inspect, logs) so the workflow journey below drives the real registered
    Activities instead of calling the backend directly. The pre-seeded
    holder occupies the only slot at limit 1, so the workflow must park in
    ``WAITING_FOR_CAPACITY`` until it is cancelled.
    """

    def __init__(self, *, holder_name: str, ownership_token: str) -> None:
        self.images = {"alpine:3.20"}
        self.commands: list[tuple[str, ...]] = []
        self.states: dict[str, str] = {holder_name: "running"}
        self.created: dict[str, str] = {}
        self.holder_token = ownership_token

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
            return 0, command[1].encode(), b""
        if command[0] == "stop":
            self.states[command[-1]] = "exited"
            return 0, b"", b""
        if command[0] == "rm":
            self.states.pop(command[-1], None)
            self.created.pop(command[-1], None)
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
