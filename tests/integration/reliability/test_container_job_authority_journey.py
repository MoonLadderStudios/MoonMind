"""Production-boundary container-job journeys for MoonMind#3266.

These tests deliberately execute the production workflow, registered activity
methods, and Docker backend adapter together.  The daemon is represented by a
deterministic command boundary so the journey remains hermetic; unlike focused
unit tests, no lifecycle activity is replaced by a test implementation.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import ContainerJobWorkflowInput
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.workflows.container_job import MoonMindContainerJobWorkflow

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


class _HermeticSystemDaemon:
    """Stateful Docker command boundary with a deployment-wide image cache."""

    def __init__(self) -> None:
        self.images: set[str] = set()
        self.commands: list[tuple[str, ...]] = []
        self.pull_count = 0

    async def run(self, raw: Any) -> tuple[int, bytes, bytes]:
        command = tuple(str(item) for item in raw)
        self.commands.append(command)
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
            return 1, b"", b"container absent"
        if command[:3] == ("inspect", "--format", "{{json .State}}"):
            return 0, json.dumps({"Running": False, "ExitCode": 0}).encode(), b""
        if command[0] == "logs":
            return 0, b"journey-complete\n", b""
        return 0, b"", b""


def _workflow_input(job_id: str, image: str, workspace: Path) -> dict[str, Any]:
    request = ContainerJobWorkflowInput.model_validate(
        {
            "jobId": job_id,
            "observeIntervalSeconds": 1,
            "request": {
                "idempotencyKey": f"journey:{job_id}",
                "source": {
                    "source": "workflow",
                    "workflowId": f"journey:{job_id}",
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


async def _run_job(
    env: WorkflowEnvironment,
    workflow_queue: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    handle = await env.client.start_workflow(
        MoonMindContainerJobWorkflow.run,
        payload,
        id=f"container-job-authority-{uuid4()}",
        task_queue=workflow_queue,
    )
    return await handle.result()


async def test_public_and_dotnet_jobs_cross_one_authority_path_and_reuse_image(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("passed\n", encoding="utf-8")
    daemon = _HermeticSystemDaemon()
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
    )
    runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
    workflow_queue = f"container-job-journey-{uuid4()}"
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
            smoke = await _run_job(
                env,
                workflow_queue,
                _workflow_input("container-job:" + "1" * 32, "alpine:3.20", workspace),
            )
            dotnet = await _run_job(
                env,
                workflow_queue,
                _workflow_input(
                    "container-job:" + "2" * 32,
                    "mcr.microsoft.com/dotnet/sdk:8.0",
                    workspace,
                ),
            )
            repeated = await _run_job(
                env,
                workflow_queue,
                _workflow_input("container-job:" + "3" * 32, "alpine:3.20", workspace),
            )

    assert smoke["state"] == dotnet["state"] == repeated["state"] == "succeeded"
    assert smoke["logsRef"] and smoke["artifactsRef"]
    assert dotnet["logsRef"] and dotnet["artifactsRef"]
    assert repeated["logsRef"] and repeated["artifactsRef"]
    assert daemon.pull_count == 2, "each distinct image should be acquired exactly once"
    creates = [command for command in daemon.commands if command[0] == "create"]
    assert len(creates) == 3
    assert all("--privileged=false" in command for command in creates)
    assert all("--network" in command and "none" in command for command in creates)
    assert all("/var/run/docker.sock" not in " ".join(command) for command in creates)
    assert all("DOCKER_HOST" not in " ".join(command) for command in daemon.commands)
    assert not any(command[0] == "rmi" for command in daemon.commands)
    assert {job_id for job_id, state in projected if state == "succeeded"} == {
        "container-job:" + "1" * 32,
        "container-job:" + "2" * 32,
        "container-job:" + "3" * 32,
    }
    # Each job publishes a deterministic evidence set (#3258): a combined log,
    # separate stdout/stderr, runtime diagnostics, an output manifest, and the
    # one declared output artifact.
    for job_id in (
        "container-job:" + "1" * 32,
        "container-job:" + "2" * 32,
        "container-job:" + "3" * 32,
    ):
        names = {name for owner, name, _ in published if owner == job_id}
        assert {
            f"{job_id}-logs.txt",
            f"{job_id}-stdout.txt",
            f"{job_id}-stderr.txt",
            f"{job_id}-diagnostics.json",
            f"{job_id}-artifacts.json",
            f"{job_id}-output-result.txt",
        } <= names
    pull_diagnostics = [
        name for _, name, _ in published if name.endswith("-image-pull.txt")
    ]
    assert len(pull_diagnostics) == 2  # each distinct image is pulled exactly once
    # 6 evidence artifacts per job across 3 jobs, plus the 2 pull diagnostics.
    assert len(published) == 6 * 3 + 2
