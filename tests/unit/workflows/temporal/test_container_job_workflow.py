"""Focused lifecycle coverage for MoonLadderStudios/MoonMind#3277."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.config.settings import settings
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobWorkflowInput,
    ImageObservation,
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
                        "kind": "external_state",
                        "artifactRef": "art_workspace",
                    },
                    "command": ["python", "-V"],
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                    "timeoutSeconds": timeout,
                },
            },
        }
    )


def _source_input(*, timeout: int = 3600) -> ContainerJobWorkflowInput:
    raw = _input(timeout=timeout).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    spec = raw["request"]["spec"]
    spec.pop("image")
    spec["imageSourceRef"] = "moonmind-python-tests"
    return ContainerJobWorkflowInput.model_validate(raw)


def test_workflow_identity_registration_and_activity_routes() -> None:
    assert container_job_workflow_id(JOB_ID) == f"container-job-workflow:{JOB_ID}"
    assert "MoonMind.ContainerJob" in workflow_fleet_workflow_types(settings.temporal)
    routes = [
        item
        for item in build_default_activity_catalog().activities
        if item.family == "container_job"
    ]
    assert len(routes) == 15
    assert {route.activity_type for route in routes} >= {
        "container_job.submit",
        "container_job.status",
        "container_job.cancel",
    }
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
async def test_local_source_gets_build_budget_without_changing_direct_image_history(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    async def execute_activity(_name, _payload, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.container_job.workflow.execute_activity",
        execute_activity,
    )
    workflow_instance = MoonMindContainerJobWorkflow()
    for inp in (_input(timeout=3600), _source_input(timeout=3600)):
        request = ContainerJobActivityRequest(
            jobId=inp.job_id,
            ownershipToken=inp.ownership_token,
            request=inp.request,
        )
        await workflow_instance._activity("container_job.acquire_image", request)

    assert calls[0]["start_to_close_timeout"] == timedelta(seconds=300)
    assert calls[0]["schedule_to_close_timeout"] == timedelta(seconds=300)
    assert calls[1]["start_to_close_timeout"] == timedelta(seconds=1800)
    assert calls[1]["schedule_to_close_timeout"] == timedelta(seconds=2100)


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
            if args[2] == "{{json .Config.Labels}}":
                return 1, b"", b"missing"
            if args[2] == "{{json .State}}":
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
    reconciliation = await activities.container_job_reconcile_container(payload)
    assert "containerRef" not in reconciliation
    assert reconciliation["running"] is False
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


@pytest.mark.asyncio
async def test_backend_resolves_sandbox_relative_path(tmp_path) -> None:
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo" / "nested"
    workspace.mkdir(parents=True)
    raw = _input().model_dump(mode="json", by_alias=True)
    raw["request"]["spec"]["workspaceRef"] = {
        "kind": "sandbox", "workspaceId": "run-1", "relativePath": "repo/nested"
    }
    inp = ContainerJobWorkflowInput.model_validate(raw)
    backend = DockerContainerJobBackend(workspace_root=tmp_path)
    result = await backend.resolve_workspace(
        ContainerJobActivityRequest(
            jobId=JOB_ID,
            ownershipToken=inp.ownership_token,
            request=inp.request,
        )
    )
    assert result.resolved_workspace_ref == str(workspace.resolve())


def _result_for(name: str) -> ContainerJobActivityResult:
    if name.endswith("resolve_workspace"):
        return ContainerJobActivityResult(
            resolvedWorkspaceRef="ws:resolved",
            resolvedWorkspaceVolumeName="agent_workspaces",
            resolvedWorkspaceVolumeSubpath="run-1/repo",
        )
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
async def test_workspace_volume_mount_survives_activity_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    create_request: ContainerJobActivityRequest | None = None

    async def activity(name, request):
        nonlocal create_request
        if name == "container_job.create_container":
            create_request = request.model_copy(deep=True)
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))

    assert result["state"] == "succeeded"
    assert create_request is not None
    assert create_request.resolved_workspace_volume_name == "agent_workspaces"
    assert create_request.resolved_workspace_volume_subpath == "run-1/repo"


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


@pytest.mark.asyncio
async def test_image_acquisition_failure_maps_to_granular_failure_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()

    async def activity(name, request):
        if name == "container_job.acquire_image":
            # The trusted backend surfaces the class via the ApplicationError type.
            raise ApplicationError("image absent", type="image_not_found")
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "failed"
    assert result["terminal"]["failureClass"] == "image_not_found"


@pytest.mark.asyncio
async def test_workflow_emits_distinct_lifecycle_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable projection distinguishes each #3258 lifecycle phase."""

    job = MoonMindContainerJobWorkflow()
    states: list[str] = []

    async def activity(name, request):
        if name == "container_job.project_status":
            value = request.state
            states.append(getattr(value, "value", value))
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))
    assert result["state"] == "succeeded"

    # Every distinct phase requested "at least" by the issue must be emitted, in
    # order, as its own monotonic status projection.
    expected_order = [
        "resolving_workspace",
        "acquiring_image",
        "starting",
        "running",
        "publishing_artifacts",
        "cleaning_up",
        "succeeded",
    ]
    positions = [states.index(phase) for phase in expected_order]
    assert positions == sorted(positions), states


@pytest.mark.asyncio
async def test_workspace_failure_projects_workspace_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    states: list[str] = []

    async def activity(name, request):
        if name == "container_job.project_status":
            value = request.state
            states.append(getattr(value, "value", value))
        if name == "container_job.resolve_workspace":
            raise ApplicationError("workspace missing", type="workspace")
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_input().model_dump(mode="json", by_alias=True))

    assert result["state"] == "failed"
    assert result["terminal"]["failureClass"] == "workspace"
    assert "workspace_not_visible" in states


@pytest.mark.asyncio
async def test_terminal_projection_carries_timing_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections = []

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        if name == "container_job.observe_container":
            return ContainerJobActivityResult(
                terminalState="succeeded",
                exitCode=0,
                startedAt="2024-01-01T00:00:01+00:00",
                finishedAt="2024-01-01T00:00:03+00:00",
                durationMs=2000,
                logCursor="2024-01-01T00:00:03+00:00|3",
            )
        if name == "container_job.publish_evidence":
            return ContainerJobActivityResult(
                logsRef="art:logs",
                artifactsRef="art:manifest",
                diagnosticsRef="art:diagnostics",
                eventsRef="art:events",
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    await job.run(_input().model_dump(mode="json", by_alias=True))

    terminal = projections[-1]
    assert terminal.duration_ms == 2000
    assert terminal.started_at is not None
    assert terminal.finished_at is not None
    assert terminal.events_ref == "art:events"
    assert terminal.publication.diagnostics_ref == "art:diagnostics"


@pytest.mark.asyncio
async def test_image_observation_threads_into_terminal_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections = []
    digest = "sha256:" + "d" * 64

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        if name == "container_job.acquire_image":
            return ContainerJobActivityResult(
                resolvedImageRef=digest,
                imageObservation=ImageObservation(
                    requestedReference="python:3.13",
                    resolvedDigest=digest,
                    cachePresent=False,
                    cacheHit=False,
                    pullLockWaitMs=12,
                    pullDurationMs=345,
                ),
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    await job.run(_input().model_dump(mode="json", by_alias=True))

    terminal = projections[-1]
    assert terminal.image_observation is not None
    assert terminal.image_observation.resolved_digest == digest
    assert terminal.image_observation.pull_duration_ms == 345
    assert terminal.image_observation.pull_lock_wait_ms == 12
