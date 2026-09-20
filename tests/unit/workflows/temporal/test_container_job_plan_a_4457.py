"""Plan A count-only admission qualification for MoonLadderStudios/MoonMind#4457.

These tests exercise the production ``DockerContainerJobBackend`` owners
against a Docker-faithful fake daemon: ``docker start`` on a running
container is refused, ``docker start`` on an exited container restarts it
(a second execution), and ``docker create`` on an existing name conflicts.
That is the boundary a lost start acknowledgment or a worker death meets on
retry, and the owners must reconcile the existing container instead of
repeating the side effect.

The cross-process race below uses the real ``FilesystemCapacityAdmissionLock``
on a shared directory with real worker processes: fcntl serialization cannot
be proven inside one process.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os

import pytest

from moonmind.container_job_cli import python_test_submission
from moonmind.omnigent.execution_profiles import POLICIES, PROFILES
from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
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

_ZERO_STARTED_AT = "0001-01-01T00:00:00Z"
_RUN_STARTED_AT = "2026-09-20T22:00:00Z"
_RUN_FINISHED_AT = "2026-09-20T22:00:05Z"


def _request(tmp_path, job_id: str = JOB_ID, **spec_overrides):
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": ["python", "-V"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(spec_overrides)
    payload = {
        "jobId": job_id,
        "ownershipToken": f"{job_id}:v1",
        "request": {
            "idempotencyKey": f"plan-a-4457:{job_id}",
            "source": {"source": "workflow", "workflowId": "mm:4457"},
            "spec": spec,
        },
        "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
        "resolvedImageRef": "sha256:" + "a" * 64,
    }
    return ContainerJobActivityRequest.model_validate(payload)


class _DockerFaithfulDaemon:
    """Fake daemon with Docker's create/start/observe identity semantics."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.containers: dict[str, dict] = {}

    def _name_state(self, name: str) -> dict | None:
        entry = self.containers.get(name)
        if entry is None or entry["removed"]:
            return None
        return entry
    async def run(self, raw) -> tuple[int, bytes, bytes]:
        args = tuple(str(item) for item in raw)
        self.commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            name = args[-1]
            entry = self._name_state(name)
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            return 0, json.dumps({"moonmind.ownership": entry["ownership"]}).encode(), b""
        if args[:3] == ("inspect", "--format", "{{.State.Running}}"):
            entry = self._name_state(args[-1])
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            return 0, (b"true" if entry["state"] == "running" else b"false"), b""
        if args[:3] == ("inspect", "--format", "{{json .State}}"):
            entry = self._name_state(args[-1])
            if entry is None:
                return 1, b"", b"Error: No such object: moonmind-container-job"
            running = entry["state"] == "running"
            if entry["runs"] == 0:
                started, finished, exit_code = _ZERO_STARTED_AT, _ZERO_STARTED_AT, 0
            elif running:
                started, finished, exit_code = _RUN_STARTED_AT, _ZERO_STARTED_AT, 0
            else:
                started, finished, exit_code = (
                    _RUN_STARTED_AT,
                    _RUN_FINISHED_AT,
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
        if args[0] == "ps":
            lines = []
            for name, entry in self.containers.items():
                if entry["removed"]:
                    continue
                lines.append(f"{name}\t{entry['state']}")
            return 0, ("\n".join(lines) + ("\n" if lines else "")).encode(), b""
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            if self._name_state(name) is not None:
                return 1, b"", b"Error: Conflict. The container name is already in use"
            ownership = ""
            for token in args:
                if token.startswith("moonmind.ownership="):
                    ownership = token.split("=", 1)[1]
            self.containers[name] = {
                "ownership": ownership,
                "state": "created",
                "runs": 0,
                "exit_code": 0,
                "removed": False,
            }
            return 0, name.encode(), b""
        if args[0] == "start":
            entry = self._name_state(args[1])
            if entry is None:
                return 1, b"", b"Error: No such container"
            if entry["state"] == "running":
                return 1, b"", b"Error: Container is already running"
            entry["state"] = "running"
            entry["runs"] += 1
            return 0, args[1].encode(), b""
        if args[0] == "stop":
            entry = self._name_state(args[1])
            if entry is None:
                return 1, b"", b"Error: No such container"
            entry["state"] = "exited"
            return 0, args[1].encode(), b""
        if args[0] == "rm":
            entry = self.containers.get(args[-1])
            if entry is not None:
                entry["removed"] = True
            return 0, b"", b""
        if args[0] == "logs":
            return 0, b"output\n", b""
        return 0, b"", b""

    def start_count(self) -> int:
        return sum(1 for command in self.commands if command[0] == "start")

    def state_count(self) -> int:
        return sum(
            1
            for command in self.commands
            if command[:3] == ("inspect", "--format", "{{json .State}}")
        )


def _backend(tmp_path, daemon, **overrides):
    return DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=daemon.run, **overrides
    )


@pytest.mark.asyncio
async def test_lost_start_ack_on_running_container_reattaches_without_restart(
    tmp_path,
) -> None:
    """Worker death after a successful start must not fail or duplicate."""

    daemon = _DockerFaithfulDaemon()
    backend = _backend(tmp_path, daemon)
    request = _request(tmp_path)
    name = DockerContainerJobBackend._name(request)
    daemon.containers[name] = {
        "ownership": f"{JOB_ID}:v1",
        "state": "running",
        "runs": 1,
        "exit_code": 0,
        "removed": False,
    }

    result = await backend.start_container(request)

    assert result.running is True
    assert result.container_ref == name
    assert daemon.start_count() == 0, "an already-running container must not be started again"


@pytest.mark.asyncio
async def test_retry_after_container_finished_does_not_execute_twice(tmp_path) -> None:
    """A container that finished between observation and retry keeps one run."""

    daemon = _DockerFaithfulDaemon()
    backend = _backend(tmp_path, daemon)
    request = _request(tmp_path)
    name = DockerContainerJobBackend._name(request)
    daemon.containers[name] = {
        "ownership": f"{JOB_ID}:v1",
        "state": "exited",
        "runs": 1,
        "exit_code": 0,
        "removed": False,
    }

    result = await backend.start_container(request)

    assert result.running is not True
    assert daemon.containers[name]["runs"] == 1
    assert daemon.start_count() == 0, "a finished container must not be restarted"


@pytest.mark.asyncio
async def test_death_before_start_starts_the_created_container_once(tmp_path) -> None:
    """A created container that never started is still started normally."""

    daemon = _DockerFaithfulDaemon()
    backend = _backend(tmp_path, daemon)
    request = _request(tmp_path)
    name = DockerContainerJobBackend._name(request)
    daemon.containers[name] = {
        "ownership": f"{JOB_ID}:v1",
        "state": "created",
        "runs": 0,
        "exit_code": 0,
        "removed": False,
    }

    result = await backend.start_container(request)

    assert result.running is True
    assert daemon.containers[name]["runs"] == 1
    assert daemon.start_count() == 1


@pytest.mark.asyncio
async def test_reconcile_fails_closed_when_the_daemon_is_unreadable(tmp_path) -> None:
    """An unreadable daemon is never reported as an absent container."""

    async def runner(args):
        args = tuple(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 0, json.dumps({"moonmind.ownership": f"{JOB_ID}:v1"}).encode(), b""
        if args[:3] == ("inspect", "--format", "{{.State.Running}}"):
            return 1, b"", b"connection refused"
        return 0, b"", b""

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.reconcile_container(_request(tmp_path))

    assert raised.value.failure_class is ContainerJobFailureClass.INFRASTRUCTURE


@pytest.mark.asyncio
async def test_reconcile_still_reports_a_vanished_container_as_absent(tmp_path) -> None:
    """Fail-closed must not turn a genuinely removed container into a retry."""

    async def runner(args):
        args = tuple(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 0, json.dumps({"moonmind.ownership": f"{JOB_ID}:v1"}).encode(), b""
        if args[:3] == ("inspect", "--format", "{{.State.Running}}"):
            return 1, b"", b"Error: No such object: moonmind-container-job"
        return 0, b"", b""

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
    result = await backend.reconcile_container(_request(tmp_path))

    assert result.container_ref is None
    assert result.running is False


def _race_worker(
    lock_dir: str,
    workspace_dir: str,
    job_id: str,
    shared_state: object,
    state_lock: object,
    outcomes: object,
    entered: object,
    active: object,
    max_active: object,
    done: object,
    hold_seconds: float,
) -> None:
    """One real worker process: real lock, real admission, blocking start."""

    import asyncio as _asyncio

    async def _main() -> str:
        async def _runner(raw):
            args = tuple(str(item) for item in raw)
            if args[0] == "ps":
                with state_lock:
                    entered.value += 1
                    snapshot = dict(shared_state)
                lines = [f"{cname}\t{cstate}" for cname, cstate in snapshot.items()]
                payload = "\n".join(lines)
                return (
                    0,
                    (payload + "\n").encode() if payload else b"",
                    b"",
                )
            if args[0] == "start":
                # The capacity lock is held across admit+start, so recording
                # the running state here is atomic with the admission read.
                with state_lock:
                    shared_state[args[1]] = "running"
                    active.value += 1
                    if active.value > max_active.value:
                        max_active.value = active.value
                await _asyncio.sleep(hold_seconds)
                with state_lock:
                    active.value -= 1
                return 0, args[1].encode(), b""
            if args[:3] == ("inspect", "--format", "{{json .State}}"):
                return (
                    0,
                    json.dumps(
                        {
                            "Running": False,
                            "ExitCode": 0,
                            "StartedAt": _ZERO_STARTED_AT,
                            "FinishedAt": _ZERO_STARTED_AT,
                        }
                    ).encode(),
                    b"",
                )
            return 0, b"", b""
        spec = {
            "image": "python:3.13",
            "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
            "command": ["python", "-V"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": 60,
        }
        request = ContainerJobActivityRequest.model_validate(
            {
                "jobId": job_id,
                "ownershipToken": f"{job_id}:v1",
                "request": {
                    "idempotencyKey": f"plan-a-4457-race:{job_id}",
                    "source": {"source": "workflow", "workflowId": "mm:4457"},
                    "spec": spec,
                },
                "resolvedWorkspaceRef": os.path.join(workspace_dir, "art_workspace"),
                "resolvedImageRef": "sha256:" + "a" * 64,
            }
        )
        backend = DockerContainerJobBackend(
            workspace_root=workspace_dir,
            command_runner=_runner,
            capacity_lock=FilesystemCapacityAdmissionLock(lock_dir),
        )
        try:
            result = await backend.start_container(request)
            if result.capacity_wait:
                return "wait"
            return "started" if result.running else "other"
        except ContainerJobBackendError as exc:
            if exc.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED:
                return "refused"
            raise

    outcome = _asyncio.run(_main())
    with state_lock:
        done.value += 1
    outcomes[job_id] = outcome


def _spawn_race(
    tmp_path,
    job_ids: list[str],
    *,
    created: bool = False,
    hold_seconds: float = 0.3,
) -> dict:
    pytest.importorskip("fcntl", reason="capacity lock workers run on Linux")
    ctx = multiprocessing.get_context("spawn")
    manager = ctx.Manager()
    if created:
        preseed = {
            DockerContainerJobBackend._name(_request(tmp_path, job_id=job_id)): "created"
            for job_id in job_ids
        }
    else:
        preseed = {}
    shared_state = manager.dict(preseed)
    state_lock = manager.Lock()
    outcomes = manager.dict()
    entered = manager.Value("i", 0)
    active = manager.Value("i", 0)
    max_active = manager.Value("i", 0)
    done = manager.Value("i", 0)

    processes = [
        ctx.Process(
            target=_race_worker,
            args=(
                str(tmp_path / "capacity"),
                str(tmp_path),
                job_id,
                shared_state,
                state_lock,
                outcomes,
                entered,
                active,
                max_active,
                done,
                hold_seconds,
            ),
        )
        for job_id in job_ids
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(60)
        assert process.exitcode == 0, f"race worker exited {process.exitcode}"
    return {
        "outcomes": dict(outcomes),
        "max_active": max_active.value,
        "entered": entered.value,
    }


@pytest.mark.skipif(os.name != "posix", reason="capacity lock workers run on Linux")
def test_two_worker_processes_race_for_the_final_slot_without_overlap(tmp_path) -> None:
    """Two real processes share one lock mount: overlap never exceeds 1."""

    job_ids = [
        "container-job:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "container-job:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    summary = _spawn_race(tmp_path, job_ids)

    assert summary["entered"] == 2
    assert sorted(summary["outcomes"].values()) == ["refused", "started"]
    assert summary["max_active"] <= 1


@pytest.mark.skipif(os.name != "posix", reason="capacity lock workers run on Linux")
def test_created_waiters_make_forward_progress_instead_of_deadlock(tmp_path) -> None:
    """Created containers hold no slot: one waiter starts instead of deadlock."""

    job_ids = [
        "container-job:cccccccccccccccccccccccccccccccc",
        "container-job:dddddddddddddddddddddddddddddddd",
        "container-job:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    ]
    summary = _spawn_race(tmp_path, job_ids, created=True)

    assert summary["entered"] == 3
    assert sorted(summary["outcomes"].values()) == ["refused", "refused", "started"]
    assert summary["max_active"] <= 1


def test_host_and_container_job_counts_are_independent(tmp_path) -> None:
    """A saturated host ledger never blocks the container-job slot ledger."""

    host_decision = evaluate_generic_host_capacity(
        active_hosts=8,
        recent_cold_launches=0,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )
    assert host_decision.admitted is False

    async def _main() -> None:
        daemon = _DockerFaithfulDaemon()
        backend = _backend(tmp_path, daemon)
        request = _request(tmp_path)
        daemon.containers[DockerContainerJobBackend._name(request)] = {
            "ownership": f"{JOB_ID}:v1",
            "state": "created",
            "runs": 0,
            "exit_code": 0,
            "removed": False,
        }
        result = await backend.start_container(request)
        assert result.running is True

    asyncio.run(_main())


def _cli_env() -> dict[str, str]:
    """Isolated fixtures for the CLI route; no paid production calls."""

    return {
        "MOONMIND_AGENT_RUN_ID": "plan-a-4457",
        "MOONMIND_RUNTIME_ID": "plan-a-4457-runtime",
        "MOONMIND_CONTAINER_JOBS_SESSION_ID": "plan-a-4457-session",
    }


def test_cli_test_submission_carries_the_stock_fixed_limits() -> None:
    """The normal CLI route pins 2 CPUs / 4 GiB / 512 pids, never a pool."""

    submission = python_test_submission(["tests/unit/test_example.py"], env=_cli_env())

    assert submission["spec"]["resources"] == {
        "cpuMillis": 2000,
        "memoryMiB": 4096,
        "pids": 512,
    }


@pytest.mark.asyncio
async def test_cli_limits_reach_docker_verbatim_without_a_pool_probe(tmp_path) -> None:
    """Fixed CLI limits become exact Docker flags; no info probe is issued."""

    submission = python_test_submission(["tests/unit/test_example.py"], env=_cli_env())
    (tmp_path / "art_workspace").mkdir()

    daemon = _DockerFaithfulDaemon()

    async def runner(args):
        args = tuple(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"Error: No such object: moonmind-container-job"
        if args[0] == "version":
            return 0, b"27.0.0", b""
        return await daemon.run(args)

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
    request = _request(tmp_path, networkMode="none")
    request.request.spec.resources.cpu_millis = submission["spec"]["resources"]["cpuMillis"]
    request.request.spec.resources.memory_mib = submission["spec"]["resources"]["memoryMiB"]
    request.request.spec.resources.pids = submission["spec"]["resources"]["pids"]

    await backend.create_container(request)

    creates = [command for command in daemon.commands if command[0] == "create"]
    assert len(creates) == 1
    assert "--cpus" in creates[0] and "2.0" in creates[0]
    assert "--memory" in creates[0] and "4096m" in creates[0]
    assert "--pids-limit" in creates[0] and "512" in creates[0]
    assert not any(command[0] == "info" for command in daemon.commands)


def test_batch_pr_resolver_preset_profiles_use_fixed_limits_without_pool_helpers() -> None:
    """Every execution profile reaches host execution on fixed limits only."""

    for ref, policy in POLICIES.items():
        assert policy.limits["cpuMillis"] == 2000, ref
        assert policy.limits["memoryMiB"] == 4096, ref
        assert policy.host_mode in {"static_compose", "on_demand_docker"}, ref
        assert not hasattr(policy, "cpu_pool"), ref
        assert not hasattr(policy, "machine_budget"), ref
    assert PROFILES, "preset profiles must exist to carry the fixed limits"
