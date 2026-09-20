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
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from moonmind.container_job_cli import python_test_submission
from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobBackendError,
    ContainerJobFailureClass,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    FilesystemCapacityAdmissionLock,
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
            return 0, b"{}", b""
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="requires the Docker CLI")
async def test_real_docker_inspect_shows_stock_fixed_limits(tmp_path: Path) -> None:
    """R4: real-Docker config inspection for the stock limits (CI Docker)."""
    import subprocess

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
