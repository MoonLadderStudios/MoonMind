"""Dispatch-boundary coverage for generic NVIDIA GPU container requests.

Qualification for MoonLadderStudios/MoonMind#3777: a caller-supplied GPU
container request must cross the ordinary ``workload.run`` Activity boundary
under unrestricted workflow Docker mode, reach the trusted launcher unchanged,
and be denied with a stable generic outcome in every other mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from temporalio import exceptions as temporal_exceptions

from moonmind.schemas.workload_models import WorkloadGpuRequest, WorkloadResult
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry

# Fixture data only: MoonMind never selects this image or command.
QUALIFICATION_IMAGE = "docker.io/library/qualification-fixture:1.0.0"
QUALIFICATION_COMMAND = ("sh", "-lc", "probe --emit report.json")


def _workspace(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "agent-workspaces"
    repo = root / "task-gpu" / "repo"
    artifacts = root / "task-gpu" / "artifacts" / "gpu-step"
    scratch = root / "task-gpu" / "scratch" / "gpu-step"
    for path in (repo, artifacts, scratch):
        path.mkdir(parents=True, exist_ok=True)
    return {"root": root, "repo": repo, "artifacts": artifacts, "scratch": scratch}


def _payload(paths: dict[str, Path], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "toolName": "container.run_container",
        "agentRunId": "task-gpu",
        "stepId": "gpu-step",
        "attempt": 1,
        "repoDir": str(paths["repo"]),
        "artifactsDir": str(paths["artifacts"]),
        "scratchDir": str(paths["scratch"]),
        "image": QUALIFICATION_IMAGE,
        "command": list(QUALIFICATION_COMMAND),
        "networkMode": "none",
        "timeoutSeconds": 120,
        "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
        "declaredOutputs": {"output.primary": "gpu/report.json"},
    }
    payload.update(overrides)
    return payload


class _RecordingLauncher:
    def __init__(self) -> None:
        self.validated: Any | None = None

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.request.tool_name,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            metadata={"workload": {"gpu": {"deviceRequestArgs": ["--gpus", "all"]}}},
        )


class _FailingLauncher:
    async def run(self, _validated: object) -> object:
        raise AssertionError("launcher must not run for a denied request")


class _FailingRegistry:
    def validate_request(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("registry validation must not run for a denied request")


def _activities(
    paths: dict[str, Path],
    *,
    launcher: Any,
    mode: str = "unrestricted",
    registry: Any | None = None,
) -> TemporalAgentRuntimeActivities:
    return TemporalAgentRuntimeActivities(
        workload_registry=(
            registry
            if registry is not None
            else RunnerProfileRegistry.empty(workspace_root=paths["root"])
        ),
        workload_launcher=launcher,
        workflow_docker_mode=mode,
        workspace_root=paths["root"],
    )


@pytest.mark.asyncio
async def test_gpu_container_request_crosses_the_unrestricted_dispatch_boundary(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    launcher = _RecordingLauncher()
    activities = _activities(paths, launcher=launcher)

    result = await activities.workload_run({"request": _payload(paths)})

    assert launcher.validated is not None
    request = launcher.validated.request
    # Image, command, and GPU resource arrive exactly as the caller sent them.
    assert request.image == QUALIFICATION_IMAGE
    assert request.command == QUALIFICATION_COMMAND
    assert request.resources.gpu == WorkloadGpuRequest(vendor="nvidia", count="all")
    assert launcher.validated.profile is None
    assert result["status"] == "succeeded"
    assert result["labels"]["moonmind.workflow_docker_mode"] == "unrestricted"
    assert result["labels"]["moonmind.workload_access"] == "unrestricted_container"


@pytest.mark.asyncio
async def test_dispatched_gpu_request_reaches_the_trusted_docker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device request is built by the trusted launcher, not by the caller."""

    paths = _workspace(tmp_path)
    recorded: list[list[str]] = []

    from tests.unit.workloads.test_docker_workload_launcher import _Process

    async def _create(*args: str, **_kwargs: Any) -> _Process:
        recorded.append(list(args))
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec", _create
    )
    activities = _activities(paths, launcher=DockerWorkloadLauncher())

    result = await activities.workload_run(
        {"request": _payload(paths, resources={"gpu": {"count": 2}})}
    )

    run_args = next(args for args in recorded if args[1] == "run")
    assert run_args[run_args.index("--gpus") + 1] == "2"
    assert run_args[-4:] == [QUALIFICATION_IMAGE, *QUALIFICATION_COMMAND]
    assert result["metadata"]["workload"]["gpu"]["deviceRequestArgs"] == [
        "--gpus",
        "2",
    ]


@pytest.mark.asyncio
async def test_gpu_request_survives_workflow_history_json_round_trip(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    launcher = _RecordingLauncher()
    activities = _activities(paths, launcher=launcher)

    wire = json.loads(json.dumps({"request": _payload(paths)}))
    await activities.workload_run(wire)

    assert launcher.validated is not None
    assert launcher.validated.request.resources.gpu == WorkloadGpuRequest(count="all")


@pytest.mark.asyncio
async def test_gpu_request_is_denied_when_workflow_docker_is_disabled(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    activities = _activities(
        paths,
        launcher=_FailingLauncher(),
        registry=_FailingRegistry(),
        mode="disabled",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run({"request": _payload(paths)})

    assert exc_info.value.type == "docker_workflows_disabled"
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_gpu_request_is_denied_when_profiles_mode_is_selected(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    activities = _activities(
        paths,
        launcher=_FailingLauncher(),
        registry=_FailingRegistry(),
        mode="profiles",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run({"request": _payload(paths)})

    assert exc_info.value.type == "docker_workflow_mode_forbidden"
    assert "profiles" in str(exc_info.value)
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_malformed_gpu_request_is_rejected_at_the_dispatch_boundary(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    launcher = _RecordingLauncher()
    activities = _activities(paths, launcher=launcher)

    with pytest.raises(ValidationError):
        await activities.workload_run(
            {"request": _payload(paths, resources={"gpu": {"vendor": "amd"}})}
        )

    assert launcher.validated is None


@pytest.mark.asyncio
async def test_cpu_only_request_dispatch_is_unchanged(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    launcher = _RecordingLauncher()
    activities = _activities(paths, launcher=launcher)

    result = await activities.workload_run(
        {"request": _payload(paths, resources={"cpu": "2", "memory": "2g"})}
    )

    assert launcher.validated is not None
    assert launcher.validated.request.resources.gpu is None
    assert result["status"] == "succeeded"
