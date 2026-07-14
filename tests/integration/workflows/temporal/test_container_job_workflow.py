"""Temporal test-server and replay evidence for MoonLadderStudios/MoonMind#3277."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

JOB_ID = "container-job:fedcba9876543210fedcba9876543210"


def _input() -> dict[str, Any]:
    return {
        "jobId": JOB_ID,
        "observeIntervalSeconds": 1,
        "request": {
            "idempotencyKey": "issue-3277-integration",
            "source": {"source": "workflow", "workflowId": "mm:3277"},
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {
                    "kind": "artifact-workspace",
                    "artifactRef": "art_workspace",
                },
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "timeoutSeconds": 60,
            },
        },
    }


def _activities() -> list[Any]:
    names = (
        "resolve_workspace",
        "probe_workspace",
        "acquire_image",
        "create_container",
        "start_container",
        "observe_container",
        "reconcile_container",
        "stop_container",
        "remove_container",
        "publish_evidence",
        "project_status",
        "repair_projection",
        "cleanup",
    )
    handlers = []
    for operation in names:
        def make_handler(op: str):
            async def handler(payload: dict[str, Any]) -> dict[str, Any]:
                del payload
                if op == "resolve_workspace":
                    return {
                        "contractVersion": "v1",
                        "resolvedWorkspaceRef": "ws:resolved",
                    }
                if op == "acquire_image":
                    return {
                        "contractVersion": "v1",
                        "resolvedImageRef": "sha256:image",
                    }
                if op == "create_container":
                    return {"contractVersion": "v1", "containerRef": "owned:3277"}
                if op == "observe_container":
                    return {
                        "contractVersion": "v1",
                        "terminalState": "succeeded",
                        "exitCode": 0,
                    }
                if op == "publish_evidence":
                    return {
                        "contractVersion": "v1",
                        "logsRef": "art:logs",
                        "artifactsRef": "art:outputs",
                    }
                return {"contractVersion": "v1"}

            return handler

        handler = make_handler(operation)
        handler.__name__ = f"container_job_{operation}"
        handlers.append(activity.defn(name=f"container_job.{operation}")(handler))
    return handlers


async def test_container_job_executes_on_test_server_and_replays() -> None:
    workflow_queue = f"container-job-workflow-{uuid4()}"
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
                    activities=_activities(),
                )
            )
            handle = await env.client.start_workflow(
                MoonMindContainerJobWorkflow.run,
                _input(),
                id=f"container-job-boundary-{uuid4()}",
                task_queue=workflow_queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()

    assert result["state"] == "succeeded"
    assert result["logsRef"] == "art:logs"
    assert result["projectionSequence"] > 0
    await Replayer(
        workflows=[MoonMindContainerJobWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
