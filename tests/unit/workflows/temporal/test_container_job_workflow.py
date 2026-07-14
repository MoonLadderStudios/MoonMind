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
from moonmind.workflows.temporal.client import (
    TemporalClientAdapter,
    WorkflowStartResult,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
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
        item
        for item in build_default_activity_catalog().activities
        if item.family == "container_job"
    ]
    assert len(routes) == 12
    assert (
        build_default_activity_catalog()
        .resolve_activity("container_job.create_container")
        .retries.max_attempts
        == 1
    )


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
    assert (
        adapter.start_workflow.await_args.kwargs["workflow_type"]
        == "MoonMind.ContainerJob"
    )


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


@pytest.mark.asyncio
async def test_backend_denial_becomes_nonretryable_application_error() -> None:
    from temporalio.exceptions import ApplicationError

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
        failure_class_from_exception,
    )

    async def deny(request):
        raise ContainerJobBackendError(
            ContainerJobFailureClass.IMAGE_USE_DENIED, "not authorized"
        )

    backend = type("Backend", (), {"acquire_image": staticmethod(deny)})()
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    inp = _input()
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": inp.ownership_token,
        "request": inp.request.model_dump(mode="json", by_alias=True),
    }
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_acquire_image(payload)
    assert excinfo.value.non_retryable
    assert excinfo.value.type == "image_use_denied"
    assert (
        failure_class_from_exception(excinfo.value)
        == ContainerJobFailureClass.IMAGE_USE_DENIED
    )


@pytest.mark.asyncio
async def test_production_backend_makes_every_registered_activity_callable(
    tmp_path,
) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return 0, b"sha256:" + b"a" * 64, b""
        if args[:2] == ("inspect", "--format"):
            if "json" in args[2]:
                return 0, b'{"Running":false,"ExitCode":0}', b""
            return 1, b"", b"missing"
        if args[0] == "logs":
            return 0, b"completed", b""
        return 0, b"", b""

    publish = AsyncMock(return_value="art:logs")
    project = AsyncMock()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publish,
        projection_writer=project,
    )
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    inp = _input()
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": inp.ownership_token,
        "request": inp.request.model_dump(mode="json", by_alias=True),
    }
    resolved = await activities.container_job_resolve_workspace(payload)
    payload["resolvedWorkspaceRef"] = resolved["resolvedWorkspaceRef"]
    image = await activities.container_job_acquire_image(payload)
    payload["resolvedImageRef"] = image["resolvedImageRef"]
    assert not (await activities.container_job_reconcile_container(payload)).get(
        "containerRef"
    )
    created = await activities.container_job_create_container(payload)
    payload["containerRef"] = created["containerRef"]
    await activities.container_job_start_container(payload)
    observed = await activities.container_job_observe_container(payload)
    assert observed["terminalState"] == "succeeded"
    await activities.container_job_stop_container(payload)
    evidence = await activities.container_job_publish_evidence(payload)
    assert evidence["logsRef"] == "art:logs"
    payload.update(
        {"state": "succeeded", "terminalState": "succeeded", "projectionSequence": 7}
    )
    await activities.container_job_project_status(payload)
    await activities.container_job_repair_projection(payload)
    await activities.container_job_remove_container(payload)
    await activities.container_job_cleanup(payload)
    assert any(command[0] == "create" for command in commands)
    create = next(command for command in commands if command[0] == "create")
    assert "sha256:" + "a" * 64 in create
    assert "python:3.13" not in create
    assert all("DOCKER_HOST" not in " ".join(command) for command in commands)
    assert project.await_count == 2


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


@pytest.mark.asyncio
async def test_authorization_flows_to_acquire_image_and_failure_class_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    job = MoonMindContainerJobWorkflow()
    seen: dict[str, object] = {}

    async def activity(name, request):
        if name == "container_job.acquire_image":
            seen["authorization"] = request.registry_authorization
            # Simulate a trusted-backend denial after the API authorized the job;
            # the specific failure class must reach the terminal outcome even
            # after crossing the (simulated) activity boundary.
            raise RuntimeError(
                "Activity task failed: "
                + str(
                    ContainerJobBackendError(
                        ContainerJobFailureClass.REGISTRY_AUTH_FAILED, "registry denied"
                    )
                )
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    payload = _input().model_dump(mode="json", by_alias=True)
    payload["registryAuthorization"] = {
        "authorized": True,
        "registry": "ghcr.io",
        "repository": "org/app",
        "reference": "ghcr.io/org/app:1",
        "credentialRef": "db://ghcr",
        "scope": "org/*",
    }
    result = await job.run(payload)
    assert seen["authorization"] is not None
    assert seen["authorization"].credential_ref == "db://ghcr"
    assert result["state"] == "failed"
    assert result["terminal"]["failureClass"] == "registry_auth_failed"


@pytest.mark.asyncio
async def test_terminal_projection_carries_authoritative_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections = []

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))

    terminal = projections[-1]
    assert result["state"] == "succeeded"
    assert terminal.exit_code == 0
    assert terminal.publication.state == "succeeded"
    assert terminal.cleanup_outcome.state == "succeeded"
    assert terminal.logs_ref == "art:logs"
    assert terminal.artifacts_ref == "art:outputs"
