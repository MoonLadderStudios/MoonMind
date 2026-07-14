"""Backend workspace visibility + redaction coverage (MoonMind#3255)."""

from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)
from moonmind.workloads.container_workspace import (
    CONTAINER_WORKSPACE_NOT_VISIBLE,
    ContainerWorkspaceError,
)

JOB_ID = "container-job:" + "b" * 32
# Artifact workspaces are owner-scoped, so the resolved source lives under
# ``<root>/<principal>/<artifactRef>``.
OWNER_ID = "user-1"
OWNER = {"principalId": OWNER_ID, "principalType": "user"}


def _request(root_marker: str = "art") -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "owner": OWNER,
            "request": {
                "idempotencyKey": "k",
                "source": {"source": "workflow"},
                "spec": {
                    "image": "python:3.13",
                    "workspaceRef": {
                        "kind": "artifact-workspace",
                        "artifactRef": root_marker,
                    },
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )


def _backend(tmp_path, runner) -> DockerContainerJobBackend:
    return DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        probe_image="busybox:stable",
    )


@pytest.mark.asyncio
async def test_resolve_workspace_returns_opaque_handle_not_host_path(tmp_path) -> None:
    (tmp_path / OWNER_ID / "art" / "repo").mkdir(parents=True)

    async def runner(args):  # pragma: no cover - resolve issues no commands
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    result = await backend.resolve_workspace(_request())
    ref = result.resolved_workspace_ref
    assert ref.startswith("container-workspace://")
    # The resolved daemon-visible source must never leak through the boundary.
    assert str(tmp_path) not in ref
    assert "repo" not in ref


@pytest.mark.asyncio
async def test_probe_uses_small_image_and_removes_marker(tmp_path) -> None:
    workspace = tmp_path / OWNER_ID / "art" / "repo"
    workspace.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        # Emulate the daemon reading the workspace marker and echoing it back.
        assert args[0] == "run"
        markers = list(workspace.glob(".mm-container-visibility-*"))
        assert markers, "probe must write a workspace marker before running"
        content = markers[0].read_text(encoding="utf-8")
        # Emulate the container writing the artifacts marker.
        artifacts_dir = next(
            (tmp_path / ".container-job-scratch").rglob("artifacts")
        )
        (artifacts_dir / markers[0].name).write_text(content, encoding="utf-8")
        return 0, content.encode(), b""

    backend = _backend(tmp_path, runner)
    result = await backend.probe_workspace(_request())
    assert result.resolved_workspace_ref.startswith("container-workspace://")
    # The probe used a small probe image, not the requested large image.
    assert any(cmd[0] == "run" and "busybox:stable" in cmd for cmd in commands)
    assert not any("python:3.13" in " ".join(cmd) for cmd in commands)
    # The job-owned marker was removed safely.
    assert not list(workspace.glob(".mm-container-visibility-*"))


@pytest.mark.asyncio
async def test_probe_failure_is_workspace_not_visible(tmp_path) -> None:
    (tmp_path / OWNER_ID / "art" / "repo").mkdir(parents=True)

    async def runner(args):
        return 1, b"", b"cannot mount source"

    backend = _backend(tmp_path, runner)
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        await backend.probe_workspace(_request())
    assert excinfo.value.code == CONTAINER_WORKSPACE_NOT_VISIBLE


@pytest.mark.asyncio
async def test_probe_failure_message_does_not_leak_host_path(tmp_path) -> None:
    # A real dockerd mount failure stderr contains the resolved bind ``src=``
    # host path; it must never reach the caller-visible classification message
    # (AC10), because that string enters Temporal history and ordinary logs.
    workspace = tmp_path / OWNER_ID / "art" / "repo"
    workspace.mkdir(parents=True)
    leaked = str(workspace)

    async def runner(args):
        return (
            1,
            b"",
            f"docker: Error response from daemon: invalid mount config: "
            f"bind source path does not exist: src={leaked}".encode(),
        )

    backend = _backend(tmp_path, runner)
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        await backend.probe_workspace(_request())
    assert excinfo.value.code == CONTAINER_WORKSPACE_NOT_VISIBLE
    message = str(excinfo.value)
    assert leaked not in message
    assert str(tmp_path) not in message


@pytest.mark.asyncio
async def test_activity_boundary_workspace_not_visible_does_not_leak_host_path(
    tmp_path,
) -> None:
    from temporalio.exceptions import ApplicationError

    workspace = tmp_path / OWNER_ID / "art" / "repo"
    workspace.mkdir(parents=True)
    leaked = str(workspace)

    async def runner(args):
        return 1, b"", f"cannot mount src={leaked}".encode()

    backend = _backend(tmp_path, runner)
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    payload = _request().model_dump(mode="json", by_alias=True)
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_probe_workspace(payload)
    assert excinfo.value.type == CONTAINER_WORKSPACE_NOT_VISIBLE
    assert excinfo.value.non_retryable is True
    # The ApplicationError message becomes the workflow terminal message and
    # enters Temporal history; it must carry no resolved host source.
    assert leaked not in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)


@pytest.mark.asyncio
async def test_activity_boundary_maps_workspace_error_to_application_error(
    tmp_path,
) -> None:
    from temporalio.exceptions import ApplicationError

    async def runner(args):
        return 1, b"", b"unreadable"

    backend = _backend(tmp_path, runner)
    # Missing workspace directory -> resolve raises workspace_not_found.
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    payload = _request(root_marker="does-not-exist").model_dump(
        mode="json", by_alias=True
    )
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_resolve_workspace(payload)
    assert excinfo.value.type == "workspace_not_found"
    assert excinfo.value.non_retryable is True
