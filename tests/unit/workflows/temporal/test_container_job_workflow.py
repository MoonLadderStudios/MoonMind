"""Boundary coverage for MoonLadderStudios/MoonMind#3253."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityResult,
    ContainerJobInput,
    container_job_workflow_id,
)
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.client import (
    TemporalClientAdapter,
    WorkflowStartResult,
)
from moonmind.workflows.temporal.workflow_registry import workflow_fleet_workflow_types
from moonmind.config.settings import settings
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)


def _input() -> ContainerJobInput:
    return ContainerJobInput(
        jobId="job-3253",
        requestRef="artifact:req",
        workspaceRef="workspace:1",
        imageRef="image:sha256",
        timeoutSeconds=60,
    )


def test_contract_has_stable_identity_and_rejects_unbounded_fields() -> None:
    assert _input().ownership_token == "container-job:job-3253:v1"
    assert container_job_workflow_id("job-3253") == "container-job:job-3253"
    with pytest.raises(ValueError):
        ContainerJobInput.model_validate(
            {**_input().model_dump(by_alias=True), "logs": "secret"}
        )


def test_workflow_and_typed_activities_are_registered_on_existing_fleets() -> None:
    assert "MoonMind.ContainerJob" in workflow_fleet_workflow_types(settings.temporal)
    catalog = build_default_activity_catalog()
    activities = [item for item in catalog.activities if item.family == "container_job"]
    assert len(activities) == 9
    assert {item.fleet for item in activities} == {AGENT_RUNTIME_FLEET}
    assert (
        catalog.resolve_activity("container_job.create_container").retries.max_attempts
        == 1
    )
    assert (
        catalog.resolve_activity("container_job.resolve_workspace").retries.max_attempts
        == 3
    )


@pytest.mark.asyncio
async def test_submission_returns_existing_or_new_workflow_without_waiting() -> None:
    adapter = TemporalClientAdapter()
    adapter.start_workflow = AsyncMock(
        return_value=WorkflowStartResult(
            workflow_id="container-job:job-3253", run_id="run"
        )
    )
    result = await adapter.start_container_job(_input())
    assert result.workflow_id == "container-job:job-3253"
    adapter.start_workflow.assert_awaited_once()
    assert (
        adapter.start_workflow.await_args.kwargs["workflow_type"]
        == "MoonMind.ContainerJob"
    )


@pytest.mark.asyncio
async def test_activity_boundary_validates_and_delegates_versioned_payload() -> None:
    backend = type(
        "Backend",
        (),
        {
            "resolve_workspace": AsyncMock(
                return_value=ContainerJobActivityResult(workspaceRef="resolved:1")
            )
        },
    )()
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    result = await activities.container_job_resolve_workspace(
        {
            "contractVersion": 1,
            "jobId": "job-3253",
            "ownershipToken": "container-job:job-3253:v1",
            "requestRef": "artifact:req",
            "workspaceRef": "workspace:1",
        }
    )
    assert result["workspaceRef"] == "resolved:1"
    backend.resolve_workspace.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_preserves_primary_success_through_cleanup_failure(
    monkeypatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    calls: list[str] = []

    async def activity(name, request):
        calls.append(name)
        if name == "container_job.resolve_workspace":
            return ContainerJobActivityResult(workspaceRef="resolved:1")
        if name == "container_job.acquire_image":
            return ContainerJobActivityResult(imageRef="image:digest")
        if name == "container_job.create_container":
            return ContainerJobActivityResult(containerRef="owned:job-3253")
        if name == "container_job.observe_container":
            return ContainerJobActivityResult(terminalState="succeeded", exitCode=0)
        if name == "container_job.publish_evidence":
            return ContainerJobActivityResult(evidenceRefs=["artifact:result"])
        if name == "container_job.cleanup":
            raise RuntimeError("remove failed")
        return ContainerJobActivityResult()

    async def project(request, state):
        job._snapshot.state = state

    monkeypatch.setattr(job, "_activity", activity)
    monkeypatch.setattr(job, "_project", project)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "succeeded"
    assert result["primaryOutcome"] == "succeeded"
    assert result["cleanupDiagnostics"] == ["cleanup_failed"]
    assert calls.count("container_job.create_container") == 1


@pytest.mark.asyncio
async def test_repeated_prelaunch_cancellation_is_idempotent(monkeypatch) -> None:
    job = MoonMindContainerJobWorkflow()
    calls: list[str] = []
    await job.cancel()
    await job.cancel()

    async def activity(name, request):
        calls.append(name)
        return ContainerJobActivityResult()

    async def project(request, state):
        job._snapshot.state = state

    monkeypatch.setattr(job, "_activity", activity)
    monkeypatch.setattr(job, "_project", project)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "canceled"
    assert "container_job.create_container" not in calls
    assert calls.count("container_job.publish_evidence") == 1
    assert calls.count("container_job.cleanup") == 1


def test_all_container_job_activities_have_runtime_handlers() -> None:
    activities = TemporalAgentRuntimeActivities()
    for definition in build_default_activity_catalog().activities:
        if definition.family == "container_job":
            suffix = definition.activity_type.split(".", 1)[1]
            assert callable(getattr(activities, f"container_job_{suffix}"))
