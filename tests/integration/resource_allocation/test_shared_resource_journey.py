"""Real Docker + durable workflow regression for the September 14 admission failures.

Run with tools/test_resource_allocation.sh; the daemon is disposable and cannot
enumerate or mutate the installed deployment's containers.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import MachineCapacityReservation
from moonmind.capacity import (
    MachineCapacityLedger,
    ReleaseEvidence,
    ReservationRequest,
    ResourceDemand,
    WORKLOAD_CLASS_GENERIC_HOST,
    machine_budget_from_runner,
)
from moonmind.capacity.cpu_pool import DockerCpuPool
from moonmind.config.settings import settings
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.integration.reliability.test_container_job_authority_journey import (
    _registered_activities,
    _workflow_input,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("MOONMIND_TEST_RESOURCE_DOCKER") != "1",
        reason="requires the isolated resource-qualification Docker daemon",
    ),
]


async def docker(args):
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), timeout=120)
    return process.returncode, out, err


async def checked(*args):
    code, out, err = await docker(args)
    assert code == 0, f"Docker {args[0]} failed: {err.decode()}"
    return out.decode().strip()


@pytest_asyncio.fixture
async def substrate(tmp_path):
    daemon_name = await checked("info", "--format", "{{.Name}}")
    assert daemon_name == "moonmind-test-resource-engine" or (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("MOONMIND_TEST_DISPOSABLE_RUNNER") == "1"
    ), "qualification requires its isolated daemon or an explicitly disposable CI runner"
    before = set((await checked("ps", "--all", "--quiet")).splitlines())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ledger.db")
    async with engine.begin() as conn:
        await conn.run_sync(MachineCapacityReservation.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ledger = MachineCapacityLedger(sessions)
    await checked("pull", "python:3.12-alpine")
    image = await checked(
        "image", "inspect", "--format", "{{.Id}}", "python:3.12-alpine"
    )
    image_ref = await checked(
        "image", "inspect", "--format", "{{index .RepoDigests 0}}", "python:3.12-alpine"
    )
    backend_ref = "resource-test-" + uuid4().hex
    pool = DockerCpuPool(
        runner=docker, ledger=ledger, backend_ref=backend_ref, helper_image=image
    )
    budget = await machine_budget_from_runner(docker, env={})
    try:
        yield SimpleNamespace(
            ledger=ledger,
            sessions=sessions,
            pool=pool,
            budget=budget,
            image=image,
            image_ref=image_ref,
            backend_ref=backend_ref,
            workspace=tmp_path,
        )
    finally:
        # The entire daemon is also removed by the test runner, even on failure.
        created = set((await checked("ps", "--all", "--quiet")).splitlines()) - before
        if created:
            await checked("rm", "--force", *sorted(created))
        await engine.dispose()


async def start_agent(s, *, cpu_millis=0, memory_mib=None):
    name = "test-agent-" + uuid4().hex
    reservation = ReservationRequest(
        backend_ref=s.backend_ref,
        workload_class=WORKLOAD_CLASS_GENERIC_HOST,
        owner_kind="generic_host",
        owner_ref=name,
        generation=1,
        demand=ResourceDemand(
            cpu_millis=cpu_millis,
            memory_mib=memory_mib or min(4096, s.budget.memory_mib - 2304),
            processes=64,
        ),
        container_ref=name,
    )
    outcome = await s.ledger.reserve(request=reservation, budget=s.budget)
    assert outcome.admitted
    from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
    from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
    from moonmind.omnigent.host_ports import HostLaunchSpec
    from moonmind.omnigent.harness_platform.host_classes import (
        HostClass,
        get_launch_policy,
    )

    async def budget():
        return s.budget

    launcher = DockerOmnigentHostLauncher(
        backend=DockerCommandBackend(),
        cpu_pool=s.pool,
        machine_budget_provider=budget,
        server_url="http://omnigent:8000",
        # Provider execution is immaterial to CPU admission. The real launcher
        # still owns volumes, UID, cgroup attachment, Docker create and start.
        runtime_scripts=SimpleNamespace(
            build_entrypoint=lambda **kwargs: (
                "exec python -c 'import time; time.sleep(180)'",
                {},
            )
        ),
    )
    host_class = HostClass.model_validate(
        {
            "hostClassId": "resource-test-host",
            "version": 1,
            "imageRef": s.image_ref,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [],
            "integrationModes": ["native-server"],
            "materializerRefs": [],
            "features": {"readOnlyRoot": True},
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )
    policy = get_launch_policy("omnigent-on-demand@1")
    policy = policy.model_copy(
        update={
            "limits": {
                **policy.limits,
                "cpuMillis": cpu_millis,
                "memoryMiB": reservation.demand.memory_mib,
                "processes": 64,
            }
        }
    )
    attachment = {
        "kind": "bind",
        "sourceRef": str(s.workspace),
        "targetPath": "/workspace",
        "accessMode": "read-write",
    }
    spec = HostLaunchSpec.model_validate(
        {
            "executionPlanRef": "plan:" + name,
            "stepExecutionId": "step-1",
            "runtimeBindingId": name,
            "hostLeaseRef": name,
            "hostLeaseGeneration": 1,
            "hostClassRef": host_class.ref,
            "imageRef": s.image_ref,
            "serverEndpointRef": "default",
            "serverUrl": "http://omnigent:8000",
            "networkRef": "none",
            "limits": policy.limits,
            "runtime": {},
            "correlationName": name,
            "workspaceAttachment": attachment,
            "skillAttachment": {
                **attachment,
                "targetPath": "/opt/skills",
                "accessMode": "read-only",
            },
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": name + "-state",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": {"moonmind.owner": "generic-omnigent-host"},
        }
    )
    await launcher.launch(
        spec=spec, host_class=host_class, launch_policy=policy, credential_handles=[]
    )
    await s.ledger.confirm(
        reservation_id=reservation.reservation_id, generation=1, container_ref=name
    )
    return name, reservation


async def remove_agent(s, name, reservation):
    await checked("rm", "--force", name)
    await checked("volume", "rm", name + "-state")
    await s.ledger.release(
        reservation_id=reservation.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(daemon_observed=True, consumer_removed=True),
    )


@pytest.mark.parametrize(
    "wait_first,agent_memory_mib", [(False, None), (True, None), (True, 512)]
)
async def test_default_job_runs_beside_agent_and_pool_recovers_after_worker_replacement(
    substrate, tmp_path, wait_first, agent_memory_mib
):
    s = substrate
    agent, reservation = await start_agent(s, memory_mib=agent_memory_mib)
    # Leave less than the test's 2-GiB minimum even on a large CI runner.
    # A fixed-size blocker can leave ample capacity and never exercise waiting.
    blocker = (
        await start_agent(
            s,
            memory_mib=s.budget.memory_mib - reservation.demand.memory_mib - 1024,
        )
        if wait_first
        else None
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("preserved candidate\n")
    published = {}
    projected = []

    async def publish(request, name, body):
        published[name] = body
        return "artifact:" + uuid4().hex

    async def project(request):
        nonlocal blocker
        projected.append(request.state)
        if str(request.state) == "waiting_for_capacity" and blocker is not None:
            await remove_agent(s, *blocker)
            blocker = None

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=docker,
        backend_ref=s.backend_ref,
        machine_capacity=s.ledger,
        cpu_pool=s.pool,
        evidence_publisher=publish,
        projection_writer=project,
    )
    raw = _workflow_input(
        "container-job:" + uuid4().hex, "python:3.12-alpine", workspace
    )
    raw["request"]["spec"].update(
        {
            "resources": {
                "cpuMillis": 0,
                "memoryMiB": 4096,
                "minimumMemoryMiB": 2048,
                "pids": 64,
            },
            "command": [
                "python",
                "-c",
                "from pathlib import Path; assert Path('/workspace/result.txt').read_text() == 'preserved candidate\\n'; print('tests actually executed')",
            ],
            "timeoutSeconds": 120,
        }
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            queue = "resources-" + uuid4().hex
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindContainerJobWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=settings.temporal.activity_agent_runtime_task_queue,
                    activities=_registered_activities(runtime),
                )
            )
            handle = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                raw,
                id="resource-journey-" + uuid4().hex,
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
        await Replayer(
            workflows=[MoonMindContainerJobWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
    assert result["state"] == "succeeded", result
    assert result["terminal"]["exitCode"] == 0
    assert ("waiting_for_capacity" in projected) is wait_first
    assert any(b"tests actually executed" in body for body in published.values())
    diagnostics = next(
        json.loads(body)
        for name, body in published.items()
        if name.endswith("-diagnostics.json")
    )
    granted = diagnostics["resolvedResources"]["memoryMiB"]
    assert (
        2048
        <= granted
        <= min(4096, s.budget.memory_mib - reservation.demand.memory_mib)
    )
    assert (workspace / "result.txt").read_text() == "preserved candidate\n"
    assert (
        int(await checked("inspect", "--format", "{{.HostConfig.NanoCpus}}", agent))
        == 0
    )
    # A fresh owner reconstructs enforcement from the ledger and kernel; an
    # empty consumer inventory alone must not release the live pool.
    replacement = DockerCpuPool(
        runner=docker, ledger=s.ledger, backend_ref=s.backend_ref, helper_image=s.image
    )
    assert await replacement.reconcile() is False
    await remove_agent(s, agent, reservation)

    async def empty_rows(**kwargs):
        return []

    from moonmind.capacity import probe_owned_containers

    async def inventory():
        return await probe_owned_containers(docker)

    # Exercise the automatic recovery owner, including a repeated delivery.
    janitor = GenericOmnigentHostJanitor(
        host_leases=SimpleNamespace(list_recoverable=empty_rows),
        runtime_bindings=SimpleNamespace(list_recoverable=empty_rows),
        realizer=object(),
        machine_capacity=s.ledger,
        machine_backend_ref=s.backend_ref,
        container_inventory=inventory,
        cpu_pool=replacement,
    )
    await janitor.run()
    await janitor.run()
    assert (await s.ledger.usage(backend_ref=s.backend_ref)).reserved_cpu_millis == 0


async def test_pool_enforces_aggregate_cpu_and_can_burst_above_one_cpu(substrate):
    s = substrate
    # Use a bounded test quota regardless of the CI runner's machine size.
    from dataclasses import replace

    budget = replace(s.budget, cpu_millis=min(s.budget.cpu_millis, 2200))
    lease = await s.pool.prepare(budget)
    name = "test-cpu-" + uuid4().hex
    script = """
import concurrent.futures, json, time
def burn(_):
    start = time.process_time(); end = time.monotonic() + 4
    while time.monotonic() < end: pass
    return time.process_time() - start
start = time.monotonic()
with concurrent.futures.ProcessPoolExecutor(8) as pool:
    cpu = sum(pool.map(burn, range(8)))
print(json.dumps({'cpu': cpu, 'elapsed': time.monotonic() - start}))
"""
    await checked(
        "run",
        "--detach",
        "--name",
        name,
        *lease.docker_args,
        "--memory",
        "256m",
        "--pids-limit",
        "64",
        "--network",
        "none",
        s.image,
        "python",
        "-c",
        script,
    )
    await s.pool.finish_launch(lease, name)
    assert await checked("wait", name) == "0"
    measurement = json.loads(await checked("logs", name))
    ratio = measurement["cpu"] / measurement["elapsed"]
    assert ratio <= budget.cpu_millis / 1000 + 0.2, measurement
    assert ratio > 1.1, measurement
    await checked("rm", name)
    assert await s.pool.reconcile()


async def test_historical_job_reclaims_idle_pool_before_fixed_cpu_admission(
    substrate, tmp_path, monkeypatch
):
    from temporalio import workflow

    s = substrate
    agent, reservation = await start_agent(s)
    await remove_agent(s, agent, reservation)
    # Shared cleanup has removed the last consumer, but the automatic janitor
    # has not yet observed that the full CPU reservation can be released.
    assert (
        await s.ledger.usage(backend_ref=s.backend_ref)
    ).reserved_cpu_millis == s.budget.cpu_millis
    workspace = tmp_path / "historical-workspace"
    workspace.mkdir()
    (workspace / "candidate.txt").write_text("saved candidate\n")
    published = {}

    async def publish(request, name, body):
        assert request.wait_for_capacity is False
        published[name] = body
        return "artifact:" + uuid4().hex

    projected = []

    async def project(request):
        projected.append(request.state)

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=docker,
        backend_ref=s.backend_ref,
        machine_capacity=s.ledger,
        cpu_pool=s.pool,
        evidence_publisher=publish,
        projection_writer=project,
    )
    raw = _workflow_input(
        "container-job:" + uuid4().hex, "python:3.12-alpine", workspace
    )
    raw["request"]["spec"].update(
        resources={"cpuMillis": 1000, "memoryMiB": 512, "pids": 64},
        command=["python", "-c", "print('historical tests executed')"],
        timeoutSeconds=120,
    )
    # Produce the historical fixed-CPU command stream, then replay it using
    # the current patch implementation after restoring workflow.patched.
    patched = workflow.patched
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda marker: (
            False
            if marker == "container-job-shared-capacity-wait-v1"
            else patched(marker)
        ),
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            queue = "historical-resources-" + uuid4().hex
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindContainerJobWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=settings.temporal.activity_agent_runtime_task_queue,
                    activities=_registered_activities(runtime),
                )
            )
            handle = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                raw,
                id="historical-resources-" + uuid4().hex,
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
        monkeypatch.setattr(workflow, "patched", patched)
        await Replayer(
            workflows=[MoonMindContainerJobWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
    assert result["state"] == "succeeded", result
    assert "waiting_for_capacity" not in projected
    assert any(b"historical tests executed" in body for body in published.values())
    assert (workspace / "candidate.txt").read_text() == "saved candidate\n"
    assert (await s.ledger.usage(backend_ref=s.backend_ref)).reserved_cpu_millis == 0
