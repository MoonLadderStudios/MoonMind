"""Focused lifecycle coverage for MoonLadderStudios/MoonMind#3277."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import (
    ContainerJobActivityResult,
    ContainerJobWorkflowInput,
    container_job_workflow_id,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.client import TemporalClientAdapter, WorkflowStartResult
from moonmind.workflows.temporal.workflow_registry import workflow_fleet_workflow_types
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"


def _input(*, timeout: int = 60) -> ContainerJobWorkflowInput:
    return ContainerJobWorkflowInput.model_validate(
        {
            "jobId": JOB_ID,
            "observeIntervalSeconds": 1,
            "request": {
                "idempotencyKey": "issue-3277",
                "source": {"source": "workflow", "workflowId": "mm:3277"},
                "spec": {
                    "image": "python:3.13",
                    "workspaceRef": {
                        "kind": "artifact-workspace",
                        "artifactRef": "art_workspace",
                    },
                    "command": ["python", "-V"],
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                    "timeoutSeconds": timeout,
                },
            },
        }
    )


def test_workflow_identity_registration_and_activity_routes() -> None:
    assert container_job_workflow_id(JOB_ID) == f"container-job-workflow:{JOB_ID}"
    assert "MoonMind.ContainerJob" in workflow_fleet_workflow_types(settings.temporal)
    routes = [
        item for item in build_default_activity_catalog().activities
        if item.family == "container_job"
    ]
    assert len(routes) == 12
    assert build_default_activity_catalog().resolve_activity(
        "container_job.create_container"
    ).retries.max_attempts == 1


@pytest.mark.asyncio
async def test_start_container_job_is_asynchronous_start_or_attach() -> None:
    adapter = TemporalClientAdapter()
    adapter.start_workflow = AsyncMock(
        return_value=WorkflowStartResult(
            workflow_id=container_job_workflow_id(JOB_ID), run_id="run-1"
        )
    )
    result = await adapter.start_container_job(_input())
    assert result.workflow_id == container_job_workflow_id(JOB_ID)
    assert adapter.start_workflow.await_args.kwargs["workflow_type"] == "MoonMind.ContainerJob"


@pytest.mark.asyncio
async def test_typed_activity_boundary_delegates_to_backend() -> None:
    backend = type(
        "Backend",
        (),
        {"resolve_workspace": AsyncMock(return_value={"resolvedWorkspaceRef": "ws:1"})},
    )()
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    inp = _input()
    result = await activities.container_job_resolve_workspace(
        {
            "jobId": JOB_ID,
            "ownershipToken": inp.ownership_token,
            "request": inp.request.model_dump(mode="json", by_alias=True),
        }
    )
    assert result["resolvedWorkspaceRef"] == "ws:1"
    backend.resolve_workspace.assert_awaited_once()


def _result_for(name: str) -> ContainerJobActivityResult:
    if name.endswith("resolve_workspace"):
        return ContainerJobActivityResult(resolvedWorkspaceRef="ws:resolved")
    if name.endswith("acquire_image"):
        return ContainerJobActivityResult(resolvedImageRef="sha256:image")
    if name.endswith("reconcile_container"):
        return ContainerJobActivityResult()
    if name.endswith("create_container"):
        return ContainerJobActivityResult(containerRef="owned:3277")
    if name.endswith("observe_container"):
        return ContainerJobActivityResult(terminalState="succeeded", exitCode=0)
    if name.endswith("publish_evidence"):
        return ContainerJobActivityResult(
            logsRef="art:logs", artifactsRef="art:outputs"
        )
    return ContainerJobActivityResult()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cancel_on", "expected"),
    [
        ("container_job.resolve_workspace", "canceled"),
        ("container_job.acquire_image", "canceled"),
        ("container_job.observe_container", "canceled"),
        ("container_job.publish_evidence", "succeeded"),
    ],
)
async def test_cancellation_during_each_lifecycle_phase(
    monkeypatch: pytest.MonkeyPatch, cancel_on: str, expected: str
) -> None:
    job = MoonMindContainerJobWorkflow()
    calls: list[str] = []

    async def activity(name, request):
        calls.append(name)
        if name == cancel_on:
            await job.cancel()
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == expected
    assert calls.count("container_job.publish_evidence") == 1
    assert calls.count("container_job.cleanup") == 1
    if cancel_on == "container_job.observe_container":
        assert calls.count("container_job.stop_container") == 1


@pytest.mark.asyncio
async def test_timeout_stops_then_publishes_evidence_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    calls: list[str] = []

    async def activity(name, request):
        calls.append(name)
        if name == "container_job.observe_container":
            raise TimeoutError
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "timed_out"
    assert result["terminal"]["failureClass"] == "timeout"
    assert calls.index("container_job.stop_container") < calls.index(
        "container_job.publish_evidence"
    )
    assert calls.index("container_job.publish_evidence") < calls.index(
        "container_job.cleanup"
    )


@pytest.mark.asyncio
async def test_primary_success_survives_publication_and_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()

    async def activity(name, request):
        if name in {"container_job.publish_evidence", "container_job.cleanup"}:
            raise RuntimeError("auxiliary failure")
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "succeeded"
    assert result["terminal"].get("failureClass") is None
    assert result["publication"]["state"] == "failed"
    assert result["cleanup"]["state"] == "failed"
