"""Plan A count-only admission qualification for MoonLadderStudios/MoonMind#4457.

Hermetic, deterministic coverage for the real-boundary journeys the issue
requires: a job that already holds its slot in any started Docker state is
never parked or double-counted, a lost start acknowledgment retries
idempotently, created waiters never deadlock admission, the cross-worker lock
is mutually exclusive across independent OS processes, and the normal
CLI-to-container route carries the stock 2 CPU / 4 GiB limits with no
machine-budget probe.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from moonmind.container_job_cli import python_test_submission
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    FilesystemCapacityAdmissionLock,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"

_STOCK_ENV = {
    "MOONMIND_AGENT_RUN_ID": "agent-run-plan-a",
    "MOONMIND_RUNTIME_ID": "runtime-plan-a",
    "MOONMIND_CONTAINER_JOBS_SESSION_ID": "session-plan-a",
}


def _request(tmp_path, **spec_overrides) -> ContainerJobActivityRequest:
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": ["python", "-V"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(spec_overrides)
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": f"{JOB_ID}:v1",
        "request": {
            "idempotencyKey": "issue-4457",
            "source": {"source": "workflow", "workflowId": "mm:4457"},
            "spec": spec,
        },
        "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
        "resolvedImageRef": "sha256:" + "a" * 64,
    }
    return ContainerJobActivityRequest.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("own_state", ["restarting", "paused", "removing"])
async def test_start_retry_admits_own_slot_in_any_started_state(
    tmp_path, own_state: str
) -> None:
    """A retry whose container already holds its slot proceeds, never waits.

    MoonLadderStudios/MoonMind#4457 R2: the daemon is the slot ledger however
    the launching worker fared. ``running`` was already admitted; a container
    observed as restarting, paused, or being removed holds the same slot and
    must not be parked behind another holder or refused.
    """

    lock = AsyncMock()
    lock.acquire.return_value = object()
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    request.wait_for_capacity = True
    own = DockerContainerJobBackend._name(request)

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "ps":
            return (
                0,
                f"{own}\t{own_state}\nmoonmind-container-job-other\trunning\n".encode(),
                b"",
            )
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
    )

    result = await backend.start_container(request)

    assert result.running is True
    assert any(command[0] == "start" for command in commands)


@pytest.mark.asyncio
async def test_start_retry_is_idempotent_when_start_ack_is_lost(
    tmp_path,
) -> None:
    """A lost ``docker start`` acknowledgment retries without a duplicate.

    MoonLadderStudios/MoonMind#4457 R2: the container actually started, the
    worker never saw the ack, and the retry observes its own running slot. A
    daemon-side ``already started`` refusal is the same successful state, not
    a launch failure.
    """

    lock = AsyncMock()
    lock.acquire.return_value = object()
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    request.wait_for_capacity = True
    own = DockerContainerJobBackend._name(request)

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "ps":
            return (
                0,
                f"{own}\trunning\nmoonmind-container-job-other\trunning\n".encode(),
                b"",
            )
        if args[0] == "start":
            return 1, b"", b"Error: container is already started"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
    )

    result = await backend.start_container(request)

    assert result.running is True
    assert result.container_ref == own


@pytest.mark.asyncio
async def test_created_waiters_do_not_deadlock_admission(tmp_path) -> None:
    """Merely created containers hold no slot, so N+1 waiters still progress.

    MoonLadderStudios/MoonMind#4457 R1: counting ``created`` would deadlock
    every waiter at a full limit with none ever starting. Two created waiters
    at limit 1 are both admitted; the filesystem lock serializes their starts.
    """

    lock = AsyncMock()
    lock.acquire.return_value = object()
    starts = 0

    async def runner(args):
        nonlocal starts
        args = tuple(args)
        if args[0] == "ps":
            return 0, b"moonmind-container-job-a\tcreated\n", b""
        if args[0] == "start":
            starts += 1
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
    )

    first = await backend.start_container(_request(tmp_path))
    second = await backend.start_container(_request(tmp_path))

    assert first.running is True
    assert second.running is True
    assert starts == 2


def _child_lock_attempt(
    lock_root: str, key: str, queue: "multiprocessing.Queue[str]"
) -> None:
    """Acquire one capacity lease in a fully independent OS process."""

    async def attempt() -> None:
        lock = FilesystemCapacityAdmissionLock(Path(lock_root))
        try:
            lease = await lock.acquire(key, wait_seconds=0.5, poll_seconds=0.01)
        except TimeoutError:
            queue.put("timeout")
            return
        try:
            queue.put("acquired")
        finally:
            await lock.release(lease)

    asyncio.run(attempt())


@pytest.mark.skipif(
    sys.platform == "win32", reason="capacity lock is a POSIX flock boundary"
)
def test_capacity_lock_is_mutually_exclusive_across_processes(tmp_path) -> None:
    """Two independent worker processes cannot hold one slot snapshot together.

    MoonLadderStudios/MoonMind#4457 R1: the shared lock mount serializes
    enumeration and start across processes, not just asyncio tasks in one
    worker. A worker lost while holding the lease releases it with the OS
    file description, so the next worker acquires instead of deadlocking.
    """

    ctx = multiprocessing.get_context("spawn")
    lock_root = tmp_path / "capacity"
    key = "plan-a-race"
    parent = FilesystemCapacityAdmissionLock(lock_root)
    parent_lease = asyncio.run(
        parent.acquire(key, wait_seconds=2.0, poll_seconds=0.01)
    )
    try:
        queue: "multiprocessing.Queue[str]" = ctx.Queue()
        contender = ctx.Process(
            target=_child_lock_attempt, args=(str(lock_root), key, queue)
        )
        contender.start()
        contender.join(timeout=10)
        assert queue.get(timeout=10) == "timeout"
    finally:
        asyncio.run(parent.release(parent_lease))

    queue2: "multiprocessing.Queue[str]" = ctx.Queue()
    successor = ctx.Process(
        target=_child_lock_attempt, args=(str(lock_root), key, queue2)
    )
    successor.start()
    successor.join(timeout=10)
    assert queue2.get(timeout=10) == "acquired"


@pytest.mark.asyncio
async def test_stock_cli_limits_reach_docker_verbatim_without_a_probe(
    tmp_path,
) -> None:
    """The normal CLI-to-container route carries 2 CPU / 4 GiB, never a pool.

    MoonLadderStudios/MoonMind#4457 R4: the canonical python-tests submission
    is an explicit fixed limit, and the backend renders it as ordinary Docker
    quotas with no daemon-side machine-budget probe and no resource helper.
    """

    submission = python_test_submission(
        ["tests/unit/workflows/temporal/test_container_job_backend.py"],
        env=dict(_STOCK_ENV),
    )
    resources = submission["spec"]["resources"]
    assert resources["cpuMillis"] == 2000
    assert resources["memoryMiB"] == 4096
    assert resources["pids"] == 512

    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        if args[0] == "version":
            return 0, b"27.0.0", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    request = _request(
        tmp_path,
        resources={"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
    )
    await backend.create_container(request)

    create = next(command for command in commands if command[0] == "create")
    assert "--cpus" in create and "2.0" in create
    assert "--memory" in create and "4096m" in create
    assert "--pids-limit" in create and "512" in create
    assert not any(command[0] == "info" for command in commands)


def test_batch_pr_resolver_preset_route_uses_no_pool_probe_or_helper() -> None:
    """The Batch PR Resolver route reaches hosts without a resource subsystem.

    MoonLadderStudios/MoonMind#4457 R4: the preset must not consult a CPU
    pool, machine ledger, or daemon probe. This pins the completed removal:
    none of those owners may reappear on the preset path.
    """

    preset = (
        Path(__file__).resolve().parents[4]
        / ".agents"
        / "skills"
        / "batch-pr-resolver"
        / "bin"
        / "batch_pr_resolver.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "machine_capacity",
        "cpu_pool",
        "cpupool",
        "resource_helper",
        "docker info",
        "MachineUtilization",
        "MAX_ACTIVE_MEMORY",
    ):
        assert marker not in preset


@pytest.mark.asyncio
async def test_agent_host_containers_never_consume_job_slots(tmp_path) -> None:
    """Agent hosts and subordinate test jobs use separate counts.

    MoonLadderStudios/MoonMind#4457 R3: the slot inventory filters on the
    container-job label at the daemon, so an agent holding the last host
    lease never appears in the job ledger and its test job still admits.
    """

    lock = AsyncMock()
    lock.acquire.return_value = object()
    seen: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        seen.append(args)
        if args[0] == "ps":
            # The daemon-side label filter already excluded the agent host;
            # only job-labeled containers are ever enumerated.
            assert "label=moonmind.container_job" in args
            return 0, b"", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
    )

    result = await backend.start_container(_request(tmp_path))

    assert result.running is True
    assert any(command[0] == "start" for command in seen)


@pytest.mark.asyncio
async def test_full_slot_without_wait_stays_a_fixed_limit_refusal(tmp_path) -> None:
    """A non-waitable job at a full count is refused, never pooled or probed."""

    lock = AsyncMock()
    lock.acquire.return_value = object()
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "ps":
            return 0, b"moonmind-container-job-other\trunning\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
    )
    request = _request(tmp_path)
    request.wait_for_capacity = False

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(request)

    assert raised.value.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
    assert "container-job slot" in str(raised.value)
    assert not any(command[0] == "start" for command in commands)
    assert not any(command[0] == "info" for command in commands)
