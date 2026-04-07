from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    LaunchCodexManagedSessionRequest,
    SendCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)


@pytest.mark.asyncio
async def test_controller_launches_container_and_returns_typed_handle(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                },
                "status": "ready",
                "imageRef": "ghcr.io/moonladderstudios/moonmind:latest",
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
                "metadata": {"vendorThreadId": "vendor-thread-1"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.container_id == "ctr-1"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"
    assert commands[0] == ("docker", "rm", "-f", "mm-codex-session-sess-1")
    run_command = commands[1]
    assert "--name" in run_command
    assert request.image_ref in run_command
    assert "python3" in run_command
    assert "moonmind.workflows.temporal.runtime.codex_session_runtime" in run_command


@pytest.mark.asyncio
async def test_controller_send_turn_executes_inside_container(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "completed",
                "metadata": {"assistantText": "OK"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.metadata["assistantText"] == "OK"
    exec_command = commands[0]
    assert exec_command[:3] == ("docker", "exec", "-i")
    assert "send_turn" in exec_command


@pytest.mark.asyncio
async def test_controller_clear_and_terminate_preserve_container_boundary() -> None:
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "clear_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-2",
                },
                "status": "ready",
                "imageRef": "ghcr.io/moonladderstudios/moonmind:latest",
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    cleared = await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
        )
    )
    terminated = await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
        )
    )

    assert cleared.session_state.session_epoch == 2
    assert terminated.status == "terminated"
    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")


@pytest.mark.asyncio
async def test_controller_launch_retries_ready_probe_errors(tmp_path: Path) -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    ready_attempts = 0

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        nonlocal ready_attempts
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            ready_attempts += 1
            if ready_attempts == 1:
                return 1, "", "container not ready"
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
                "metadata": {"vendorThreadId": "vendor-thread-1"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
        ready_poll_attempts=2,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert ready_attempts == 2


@pytest.mark.asyncio
async def test_controller_launch_cleans_up_container_when_handshake_fails() -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            return 1, "", "launch failed"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        await controller.launch_session(request)

    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")


@pytest.mark.asyncio
async def test_controller_launch_rejects_reserved_session_environment() -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_SESSION_WORKSPACE_PATH": "/tmp/override"},
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    with pytest.raises(RuntimeError, match="reserved session keys"):
        await controller.launch_session(request)
