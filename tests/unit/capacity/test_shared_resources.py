"""Incident replay and allocation retries at the actual admission ledger."""

import json
from pathlib import Path

import pytest

from moonmind.capacity import (
    MachineCapacityLedger,
    MachineResourceBudget,
    MachineTotals,
    ResourceDemand,
    ReservationRequest,
    WORKLOAD_CLASS_GENERIC_HOST,
    WORKLOAD_CLASS_CONTAINER_JOB,
)
from moonmind.container_job_cli import python_test_submission
from moonmind.schemas.container_job_models import ResourceLimits
from tests.unit.capacity import test_machine_capacity_boundaries as capacity_fixtures
from tests.unit.omnigent.test_mounted_container_cli import _load_cli

session_factory = capacity_fixtures.session_factory


def request(owner, *, cpu, memory, workload=WORKLOAD_CLASS_CONTAINER_JOB):
    return ReservationRequest(
        backend_ref="system",
        workload_class=workload,
        owner_kind=owner,
        owner_ref=owner,
        generation=1,
        demand=ResourceDemand(cpu_millis=cpu, memory_mib=memory),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("total_memory,expected", [(9934, 2857), (32768, 4096)])
async def test_incident_replay_and_retry_preserve_granted_memory(
    session_factory, total_memory, expected
):
    incident = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/reliability/shared-resources/incident.json"
        ).read_text()
    )
    ledger = MachineCapacityLedger(session_factory)
    budget = MachineResourceBudget.from_totals(
        MachineTotals(
            cpu_millis=incident["machine"]["cpuMillis"],
            memory_mib=total_memory,
            processes=6144,
            temporary_storage_mib=total_memory,
        ),
        env={},
    )
    # Historical six-CPU request reproduces the escaped production failure.
    await ledger.reserve(
        request=request(
            "agent", cpu=2000, memory=4096, workload=WORKLOAD_CLASS_GENERIC_HOST
        ),
        budget=budget,
    )
    denied = await ledger.reserve(
        request=request("old-test", cpu=4000, memory=4096), budget=budget
    )
    assert not denied.admitted
    assert denied.decision.limiting_resource == "machine_cpu"
    # The new test shares CPU and authorizes a bounded memory range. The
    # remaining CPU pool is reserved once by its separate kernel owner.
    job = request("new-test", cpu=0, memory=4096)
    outcome = await ledger.reserve(request=job, budget=budget, minimum_memory_mib=2048)
    assert outcome.admitted
    assert outcome.decision.demand.memory_mib == expected
    replay = await ledger.reserve(request=job, budget=budget, minimum_memory_mib=2048)
    assert replay.reused
    assert replay.decision.demand.memory_mib == expected
    assert (
        await ledger.usage(backend_ref="system")
    ).reserved_memory_mib == 4096 + expected


@pytest.mark.asyncio
async def test_memory_floor_waits_instead_of_overcommitting(session_factory):
    ledger = MachineCapacityLedger(session_factory)
    budget = MachineResourceBudget.from_totals(
        MachineTotals(
            cpu_millis=6000,
            memory_mib=8192,
            processes=6144,
            temporary_storage_mib=8192,
        ),
        env={},
    )
    await ledger.reserve(
        request=request(
            "agent", cpu=0, memory=4096, workload=WORKLOAD_CLASS_GENERIC_HOST
        ),
        budget=budget,
    )
    result = await ledger.reserve(
        request=request("test", cpu=0, memory=4096),
        budget=budget,
        minimum_memory_mib=2048,
    )
    assert not result.admitted and not result.unsatisfiable
    assert (await ledger.usage(backend_ref="system")).reserved_memory_mib == 4096


def test_cli_defaults_match_and_explicit_limits_remain_exact(monkeypatch):
    payload = python_test_submission(
        [],
        env={
            "MOONMIND_AGENT_RUN_ID": "run",
            "MOONMIND_RUNTIME_ID": "codex_cli",
            "MOONMIND_CONTAINER_JOBS_SESSION_ID": "session",
        },
    )
    cli = _load_cli()
    captured = {}
    monkeypatch.setattr(cli, "_run", lambda spec, **kwargs: captured.update(spec) or 0)
    cli._python_tests([], 3600)
    for key in ("resources", "command", "environment", "outputs"):
        assert captured[key] == payload["spec"][key]
    assert captured["resources"] == {
        "cpuMillis": 0,
        "memoryMiB": 4096,
        "minimumMemoryMiB": 2048,
        "pids": 512,
    }
    assert ResourceLimits(memoryMiB=4096).cpu_millis == 0
    fixed = ResourceLimits(cpuMillis=6000, memoryMiB=8192)
    assert fixed.cpu_millis == 6000 and fixed.minimum_memory_mib is None
    with pytest.raises(ValueError, match="must not exceed"):
        ResourceLimits(memoryMiB=2048, minimumMemoryMiB=4096)


@pytest.mark.asyncio
@pytest.mark.parametrize("driver,parent_prefix", [("systemd", ""), ("cgroupfs", "/")])
async def test_pool_parent_comes_from_daemon_and_immutable_worker_image(
    driver, parent_prefix
):
    from moonmind.capacity.cpu_pool import DockerCpuPool, CpuPoolUnavailable

    async def runner(args):
        return 0, f"2\t{driver}\t[]".encode(), b""

    pool = DockerCpuPool(
        runner=runner,
        ledger=object(),
        backend_ref="system",
        helper_image="sha256:" + "a" * 64,
    )
    args = await pool.launch_args()
    assert args == [
        "--cgroup-parent",
        parent_prefix + pool.leaf,
        "--cpu-shares",
        "1024",
    ]
    mutable = DockerCpuPool(
        runner=runner,
        ledger=object(),
        backend_ref="system",
        helper_image="image:latest",
    )
    with pytest.raises(CpuPoolUnavailable, match="immutable"):
        await mutable.launch_args()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observation", [b"1\tcgroupfs\t[]", b'2\tsystemd\t["rootless"]']
)
async def test_pool_rejects_missing_enforcement_authority_before_mutation(observation):
    from moonmind.capacity.cpu_pool import DockerCpuPool, CpuPoolUnavailable

    calls = []

    async def runner(args):
        calls.append(args)
        return 0, observation, b""

    pool = DockerCpuPool(
        runner=runner,
        ledger=object(),
        backend_ref="system",
        helper_image="sha256:" + "a" * 64,
    )
    with pytest.raises(CpuPoolUnavailable):
        await pool.launch_args()
    assert len(calls) == 1 and calls[0][0] == "info"
