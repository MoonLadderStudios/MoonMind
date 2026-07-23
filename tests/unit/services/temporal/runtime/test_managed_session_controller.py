from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    ManagedGitHubCredentialDescriptor,
    ManagedSessionEnsureDockerSidecarRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
    ManagedSessionReapResult,
    _default_command_runner,
    _parse_docker_timestamp,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer


@pytest.fixture(autouse=True)
def _clear_managed_session_docker_policy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONMIND_WORKFLOW_DOCKER_MODE", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_MODE", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS", raising=False)


class _LocalArtifactStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(
        self, *, job_id: str, artifact_name: str, data: bytes
    ) -> tuple[Path, str]:
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref: str) -> Path:
        return self._root / ref

def _workspace_git_command(workspace_path: str | Path, *args: str) -> tuple[str, ...]:
    resolved_workspace = str(Path(workspace_path).resolve())
    return (
        "git",
        "-c",
        f"safe.directory={resolved_workspace}",
        "-C",
        resolved_workspace,
        *args,
    )


def test_controller_session_end_cleanup_removes_moonmind_skill_projections(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "agent_jobs" / "job-1" / "repo"
    active = tmp_path / "agent_jobs" / "job-1" / "runtime" / "skills_active" / "snap"
    active.mkdir(parents=True)
    (active / "_manifest.json").write_text('{"snapshot_id": "snap"}\n', encoding="utf-8")
    agents_projection = workspace / ".agents" / "skills"
    gemini_projection = workspace / ".gemini" / "skills"
    agents_projection.parent.mkdir(parents=True)
    gemini_projection.parent.mkdir(parents=True)
    agents_projection.symlink_to(active)
    gemini_projection.symlink_to(active)
    record = CodexManagedSessionRecord(
        sessionId="sess-cleanup",
        sessionEpoch=1,
        agentRunId="task-1",
        containerId="ctr-1",
        threadId="thread-1",
        runtimeId="codex_cli",
        imageRef="img",
        controlUrl="docker-exec://ctr-1",
        status="ready",
        workspacePath=str(workspace),
        sessionWorkspacePath=str(tmp_path / "session"),
        artifactSpoolPath=str(tmp_path / "artifacts"),
        startedAt="2026-04-06T12:00:00Z",
    )

    DockerCodexManagedSessionController._cleanup_skill_projections_for_session(record)

    assert not agents_projection.exists()
    assert not agents_projection.is_symlink()
    assert not gemini_projection.exists()
    assert not gemini_projection.is_symlink()
    assert (workspace / ".agents").is_dir()

@pytest.mark.asyncio
async def test_default_command_runner_clears_supplemental_groups_when_uid_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    class _FakeProcess:
        returncode = 0

        async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(
        *_command: str,
        **kwargs: object,
    ) -> _FakeProcess:
        captured_kwargs.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    await _default_command_runner(("id",), run_as_uid=1000)

    assert captured_kwargs["user"] == 1000
    assert captured_kwargs["extra_groups"] == []
    assert "group" not in captured_kwargs

@pytest.mark.asyncio
async def test_controller_launches_container_and_returns_typed_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_PYTHON_TEST_IMAGE", raising=False)
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        workflowId="wf-task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        turnCompletionTimeoutSeconds=1800,
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
                "metadata": {
                    "vendorThreadId": "vendor-thread-1",
                    "model": "gpt-5.4",
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        moonmind_url="http://api:8000",
        session_store=session_store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.container_id == "ctr-1"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"
    assert commands[0] == ("docker", "rm", "-f", "mm-codex-session-sess-1")
    assert commands[1] == ("docker", "rm", "-f", "moonmind-session-sess-1-agent")
    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert "--init" in run_command
    assert "--name" in run_command
    assert "--user" in run_command
    assert "1000:1000" in run_command
    assert "--mount" in run_command
    assert "-v" not in run_command
    assert "--network" in run_command
    assert "local-network" in run_command
    assert request.image_ref in run_command
    assert (
        "MOONMIND_SESSION_TURN_COMPLETION_TIMEOUT_SECONDS=1800" in run_command
    )
    assert "MOONMIND_TASK_WORKFLOW_ID=wf-task-1" in run_command
    assert "MOONMIND_AGENT_RUN_ID=task-1" in run_command
    assert "MOONMIND_RUNTIME_ID=codex_cli" in run_command
    assert not any(
        item.startswith("MOONMIND_PYTHON_TEST_IMAGE=") for item in run_command
    )
    assert "MOONMIND_CONTAINER_JOBS_MCP_URL=http://api:8000/mcp" in run_command
    assert "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND=managed_runtime" in run_command
    assert "MOONMIND_CONTAINER_JOBS_RUNTIME_ID=codex_cli" in run_command
    assert "MOONMIND_CONTAINER_JOBS_SESSION_ID=sess-1" in run_command
    assert not any(item.startswith("DOCKER_HOST=") for item in run_command)
    assert not any(item.startswith("SYSTEM_DOCKER_HOST=") for item in run_command)
    assert "python3" in run_command
    assert "moonmind.workflows.temporal.runtime.codex_session_runtime" in run_command
    stored = session_store.load("sess-1")
    assert stored is not None
    assert stored.agent_run_id == "task-1"
    assert stored.container_id == "ctr-1"
    assert stored.runtime_id == "codex_cli"
    container_jobs = stored.metadata["capabilities"]["containerJobs"]
    assert container_jobs == {
        "available": True,
        "transport": "moonmind-mcp",
        "backendKind": "docker-engine",
        "workspace": {
            "kind": "managed_runtime",
            "runtimeId": "codex_cli",
            "agentRunId": "task-1",
            "relativePath": "repo",
        },
        "tools": [
            "container.submit",
            "container.status",
            "container.logs",
            "container.artifacts",
            "container.cancel",
        ],
    }
    session_supervisor.start.assert_awaited_once()
    assert [
        call.kwargs["kind"]
        for call in session_supervisor.emit_session_event.call_args_list
    ] == ["session_started", "runtime_status", "model_status"]


@pytest.mark.asyncio
async def test_launch_session_injects_generic_managed_agent_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Managed Codex sessions receive the generic MM-861 env vars, set
    authoritatively over caller-supplied passthrough values (every managed
    agent session honors the same contract as ``ManagedRuntimeLauncher``)."""

    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            # Spoofed/passthrough values that MUST be overridden authoritatively.
            "CI": "0",
            "MOONMIND_REPO_DIR": "/should/be/overwritten",
        },
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
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    expected_run_root = str(Path(request.artifact_spool_path).parent)
    assert f"MOONMIND_REPO_DIR={request.workspace_path}" in run_command
    assert f"MOONMIND_RUN_ROOT={expected_run_root}" in run_command
    assert f"MOONMIND_ARTIFACTS_DIR={request.artifact_spool_path}" in run_command
    assert "CI=1" in run_command
    # Authoritative: the spoofed passthrough values are not propagated.
    assert "CI=0" not in run_command
    assert "MOONMIND_REPO_DIR=/should/be/overwritten" not in run_command


@pytest.mark.asyncio
async def test_controller_checks_out_target_branch_for_existing_git_workspace_without_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 1000,
    )
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "task-1" / "repo"
    workspace_path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    (workspace_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature/publish"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
    )
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={"targetBranch": "feature/publish"},
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
    )

    await controller._ensure_workspace_paths(request)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=workspace_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "feature/publish"


@pytest.mark.asyncio
async def test_mm866_docker_enabled_session_launches_agent_with_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "profiles",
        },
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
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name.endswith("-docker"):
                return 0, "sidecar-ctr\n", ""
            if name.endswith("-agent"):
                return 0, "agent-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "volume" in command:
            if "inspect" in command:
                return 1, "", "No such volume"
            return 0, command[-1] + "\n", ""
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            return 0, '"27.0.0"\n', ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "agent-ctr",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://moonmind-session-sess-1-agent",
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

    assert handle.session_state.container_id == "agent-ctr"
    volume_create_commands = [
        command for command in commands if command[:3] == ("docker", "volume", "create")
    ]
    assert {command[-1] for command in volume_create_commands} == {
        "moonmind-session-sess-1-docker-socket",
        "moonmind-session-sess-1-docker-graph",
    }
    for command in volume_create_commands:
        labels = {
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--label"
        }
        role = (
            "docker-socket"
            if command[-1].endswith("-docker-socket")
            else "docker-graph"
        )
        assert "moonmind.session_id=sess-1" in labels
        assert "moonmind.kind=session-docker-sidecar-volume" in labels
        assert f"moonmind.volume_role={role}" in labels
        assert "moonmind.agent_run_id=task-1" in labels
        assert "moonmind.session_epoch=1" in labels
    assert any(
        command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-docker" in command
        for command in commands
    )
    assert (
        "docker",
        "exec",
        "-e",
        "DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock",
        "sidecar-ctr",
        "docker",
        "volume",
        "create",
        "--driver",
        "local",
        "--opt",
        "type=none",
        "--opt",
        "o=bind",
        "--opt",
        f"device={workspace_root}",
        "agent_workspaces",
    ) in commands
    agent_run = next(
        command
        for command in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    assert (
        "type=volume,src=agent_workspaces,"
        f"dst={workspace_root}" in agent_run
    )
    assert "--privileged" not in agent_run
    assert "moonmind.session_id=sess-1" in agent_run
    assert "moonmind.session_epoch=1" in agent_run
    assert "moonmind.agent_run_id=task-1" in agent_run
    assert "moonmind.workload_mode=docker-sidecar" in agent_run
    assert (
        "DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock" in agent_run
    )
    assert "MOONMIND_DOCKER_ACTIVATION_COMMAND=true" in agent_run
    assert "SYSTEM_DOCKER_HOST=" not in " ".join(agent_run)
    assert (
        "type=volume,src=moonmind-session-sess-1-docker-socket,"
        "dst=/var/run/moonmind-docker" in agent_run
    )

@pytest.mark.asyncio
async def test_launch_session_recreates_sidecar_after_name_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "profiles",
        },
    )
    commands: list[tuple[str, ...]] = []
    sidecar_run_attempts = 0
    sidecar_name = "moonmind-session-sess-1-docker"
    redacted_sidecar_name = "moonmind-session-sess-[REDACTED]-docker"

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        nonlocal sidecar_run_attempts
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name == sidecar_name:
                sidecar_run_attempts += 1
                if sidecar_run_attempts == 1:
                    return (
                        125,
                        "",
                        'docker: Error response from daemon: Conflict. '
                        f'The container name "/{redacted_sidecar_name}" '
                        "is already in use "
                        'by container "old-sidecar". You have to remove '
                        "(or rename) that container to be able to reuse that name.",
                    )
                return 0, "sidecar-ctr\n", ""
            if name.endswith("-agent"):
                return 0, "agent-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            return 0, '"27.0.0"\n', ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "agent-ctr",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://moonmind-session-sess-1-agent",
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
    assert sidecar_run_attempts == 2
    assert commands.count(("docker", "rm", "-f", sidecar_name)) >= 2

@pytest.mark.asyncio
async def test_launch_session_cleans_up_sidecar_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "profiles",
        },
    )
    commands: list[tuple[str, ...]] = []
    sidecar_name = "moonmind-session-sess-1-docker"
    agent_name = "moonmind-session-sess-1-agent"

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name == sidecar_name:
                return 0, "sidecar-ctr\n", ""
            if name == agent_name:
                raise asyncio.CancelledError()
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            return 0, '"27.0.0"\n', ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(asyncio.CancelledError):
        await controller.launch_session(request)

    assert commands.count(("docker", "rm", "-f", agent_name)) >= 1
    assert commands.count(("docker", "rm", "-f", sidecar_name)) >= 2
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-socket",
    ) in commands
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-graph",
    ) in commands

@pytest.mark.asyncio
async def test_mm866_explicit_docker_denial_does_not_inherit_unrestricted_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "unrestricted",
            "DOCKER_HOST": "tcp://docker-proxy:2375",
        },
        dockerCapability={
            "allowed": False,
            "activation": "denied",
            "state": "not_allowed",
        },
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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        docker_host="tcp://docker-proxy:2375",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    run_command = next(command for command in commands if command[:2] == ("docker", "run"))
    rendered = " ".join(run_command)
    assert "DOCKER_HOST=" not in rendered
    assert "SYSTEM_DOCKER_HOST=" not in rendered
    assert not any(
        command[:3] == ("docker", "network", "connect") for command in commands
    )

@pytest.mark.asyncio
async def test_controller_removes_named_container_when_docker_run_returns_blank(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
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
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "\n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="blank container id"):
        await controller.launch_session(request)

    container_name = "mm-codex-session-sess-1"
    assert commands.count(("docker", "rm", "-f", container_name)) == 2


def test_mm693_capability_metadata_ignores_non_mapping_existing_value() -> None:
    merged = DockerCodexManagedSessionController._merge_capability_metadata(
        {"capabilities": "v1", "other": "kept"},
        {"capabilities": {"docker": {"available": True}}},
    )

    assert merged == {
        "capabilities": {"docker": {"available": True}},
        "other": "kept",
    }

    unchanged = DockerCodexManagedSessionController._merge_capability_metadata(
        {"capabilities": "v1"},
        {},
    )
    assert unchanged == {"capabilities": "v1"}

    with_metadata = DockerCodexManagedSessionController._merge_capability_metadata(
        {"capabilities": {"docker": {"available": False}}, "original": "kept"},
        {
            "capabilities": {"docker": {"available": True}},
            "vendorThreadId": "thread-2",
        },
    )
    assert with_metadata == {
        "capabilities": {"docker": {"available": True}},
        "original": "kept",
        "vendorThreadId": "thread-2",
    }


@pytest.mark.asyncio
async def test_mm693_zero_interval_docker_capability_probe_yields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(tmp_path / "repo"),
        sessionWorkspacePath=str(tmp_path / "session"),
        artifactSpoolPath=str(tmp_path / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        dockerCapability={
            "allowed": True,
            "activation": "on_demand",
            "mode": "sidecar-dind",
            "composeSupport": False,
            "timeoutSeconds": 0.1,
            "intervalSeconds": 0,
        },
    )
    monotonic_values = [0.0, 0.0, 1.0]
    monotonic_index = 0
    sleeps: list[float] = []
    original_sleep = asyncio.sleep

    def _monotonic() -> float:
        nonlocal monotonic_index
        value = monotonic_values[min(monotonic_index, len(monotonic_values) - 1)]
        monotonic_index += 1
        return value

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        await original_sleep(0)

    monkeypatch.setattr(time, "monotonic", _monotonic)
    monkeypatch.setattr(asyncio, "sleep", _sleep)

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if (
            command[:3] == ("docker", "exec", "-e")
            and command[4:6] == ("ctr-1", "docker")
        ):
            return 1, "", "docker daemon unavailable"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path),
        command_runner=_fake_runner,
    )

    status = await controller._evaluate_docker_capability(
        container_id="ctr-1",
        request=request,
    )

    assert sleeps == [0]
    assert status["capabilities"]["docker"]["available"] is False


@pytest.mark.asyncio
async def test_mm866_ensure_docker_sidecar_starts_sidecar_on_demand(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="agent-ctr",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://agent-ctr",
            status="ready",
            workspacePath=str(workspace_root / "task-1" / "repo"),
            sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
            artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
            metadata={
                "dockerSidecarEnabled": True,
                "dockerActivation": "on_demand",
                "capabilities": {
                    "docker": {
                        "allowed": True,
                        "activation": "on_demand",
                        "state": "not_started",
                    }
                },
            },
            startedAt=datetime.now(tz=UTC),
        )
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "inspect", "-f"):
            target = command[-1]
            if target == "agent-ctr":
                return 0, "true\n", ""
            if target == "moonmind-session-sess-1-docker":
                return 1, "", "No such container"
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            return 0, "sidecar-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "volume" in command:
            if "inspect" in command:
                return 1, "", "No such volume"
            return 0, command[-1] + "\n", ""
        if command[:3] == ("docker", "exec", "-e"):
            return 0, '"27.0.0"\n', ""
        if command[:4] == ("docker", "exec", "agent-ctr", "docker"):
            return 0, "27.0.0\n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        session_store=session_store,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    response = await controller.ensure_docker_sidecar(
        ManagedSessionEnsureDockerSidecarRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="agent-ctr",
            threadId="logical-thread-1",
            reason="repo uses docker compose for tests",
        )
    )

    assert response.state == "ready"
    assert response.docker_host == "unix:///var/run/moonmind-docker/docker.sock"
    assert any(
        command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-docker" in command
        for command in commands
    )
    saved = session_store.load("sess-1")
    assert saved is not None
    assert saved.metadata["capabilities"]["docker"]["state"] == "ready"

@pytest.mark.asyncio
async def test_mm866_ensure_docker_sidecar_starts_existing_sidecar_before_ready(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="agent-ctr",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://agent-ctr",
            status="ready",
            workspacePath=str(workspace_root / "task-1" / "repo"),
            sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
            artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
            metadata={
                "dockerSidecarEnabled": True,
                "capabilities": {"docker": {"allowed": True}},
            },
            startedAt=datetime.now(tz=UTC),
        )
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "inspect", "-f"):
            return 0, "true\n", ""
        if command[:3] == ("docker", "start", "moonmind-session-sess-1-docker"):
            return 0, "moonmind-session-sess-1-docker\n", ""
        if command[:3] == ("docker", "exec", "-e") and "volume" in command:
            if "inspect" in command:
                return 0, "{}\n", ""
            return 0, command[-1] + "\n", ""
        if command[:3] == ("docker", "exec", "-e"):
            return 0, '"27.0.0"\n', ""
        if command[:4] == ("docker", "exec", "agent-ctr", "docker"):
            return 0, "27.0.0\n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        session_store=session_store,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    response = await controller.ensure_docker_sidecar(
        ManagedSessionEnsureDockerSidecarRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="agent-ctr",
            threadId="logical-thread-1",
        )
    )

    assert response.state == "ready"
    assert ("docker", "start", "moonmind-session-sess-1-docker") in commands
    remove_index = next(
        index
        for index, command in enumerate(commands)
        if "volume" in command and "rm" in command
    )
    create_index = next(
        index
        for index, command in enumerate(commands)
        if "volume" in command and "create" in command
    )
    assert remove_index < create_index
    assert any(
        command[:8]
        == (
            "docker",
            "exec",
            "-e",
            "DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock",
            "moonmind-session-sess-1-docker",
            "docker",
            "volume",
            "create",
        )
        and command[-1] == "agent_workspaces"
        for command in commands
    )


@pytest.mark.asyncio
async def test_controller_rejects_unmaterialized_rootless_sidecar_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_MANAGED_SESSION_DOCKER_MODE": "docker-sidecar-rootless"},
    )
    runner = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=runner,
    )

    with pytest.raises(RuntimeError, match="docker-sidecar-rootless"):
        await controller.launch_session(request)
    runner.assert_not_awaited()

@pytest.mark.asyncio
async def test_controller_rejects_unknown_managed_session_docker_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_MANAGED_SESSION_DOCKER_MODE": "docker-sidecarr"},
    )
    runner = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=runner,
    )

    with pytest.raises(
        RuntimeError,
        match="Unsupported MOONMIND_MANAGED_SESSION_DOCKER_MODE",
    ):
        await controller.launch_session(request)
    runner.assert_not_awaited()

@pytest.mark.asyncio
async def test_controller_record_keeps_auth_and_runtime_homes_out_of_artifact_refs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-auth-evidence",
        sessionId="sess-auth-evidence",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-auth-evidence" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-auth-evidence" / "session"),
        artifactSpoolPath=str(workspace_root / "task-auth-evidence" / "artifacts"),
        codexHomePath=str(
            workspace_root / "task-auth-evidence" / ".moonmind" / "codex-home"
        ),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-auth-evidence\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-auth-evidence",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-auth-evidence",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        session_store=session_store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    stored = session_store.load("sess-auth-evidence")
    assert stored is not None
    assert stored.published_artifact_refs() == ()
    record_payload = stored.model_dump(mode="json", by_alias=True)
    assert record_payload["artifactSpoolPath"].endswith(
        "task-auth-evidence/artifacts"
    )
    assert "MANAGED_AUTH_VOLUME_PATH" not in json.dumps(record_payload)
    assert ".codex-auth" not in json.dumps(record_payload)
    assert "codex-home" not in json.dumps(stored.published_artifact_refs())

@pytest.mark.asyncio
async def test_controller_uses_request_moonmind_url_for_docker_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_URL", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_URL": "http://api:8000"},
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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert "--network" in run_command
    assert "local-network" in run_command

@pytest.mark.asyncio
async def test_no_docker_session_ignores_unrestricted_docker_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("MOONMIND_DOCKER_PROXY_NETWORK", "docker-proxy-test")
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "unrestricted",
            "MOONMIND_MANAGED_SESSION_DOCKER_MODE": "no-docker",
        },
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
        if command[:3] == ("docker", "network", "connect"):
            return 0, "", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        docker_host="tcp://docker-proxy:2375",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert "docker-proxy-test" not in run_command
    assert not any(item.startswith("DOCKER_HOST=") for item in run_command)
    assert not any(item.startswith("SYSTEM_DOCKER_HOST=") for item in run_command)
    assert not any(command[:3] == ("docker", "network", "connect") for command in commands)

@pytest.mark.asyncio
async def test_mm784_request_unrestricted_mode_uses_sidecar_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("MOONMIND_DOCKER_PROXY_NETWORK", "docker-proxy-test")
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "unrestricted",
        },
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
        if command[:3] == ("docker", "volume", "rm"):
            return 1, "", "No such volume"
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name.endswith("-docker"):
                return 0, "sidecar-ctr\n", ""
            if name.endswith("-agent"):
                return 0, "agent-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            return 0, '"27.0.0"\n', ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "agent-ctr",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://moonmind-session-sess-1-agent",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        docker_host="tcp://docker-proxy:2375",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    agent_run = next(
        command
        for command in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    assert "DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock" in agent_run
    assert "DOCKER_HOST=tcp://docker-proxy:2375" not in agent_run
    assert not any(
        command[:3] == ("docker", "network", "connect") for command in commands
    )

@pytest.mark.asyncio
async def test_mm784_env_unrestricted_mode_uses_sidecar_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_MODE", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("MOONMIND_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("MOONMIND_WORKFLOW_DOCKER_MODE", "unrestricted")
    monkeypatch.setenv("MOONMIND_DOCKER_PROXY_NETWORK", "docker-proxy-test")
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_URL": "http://api:8000"},
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
        if command[:3] == ("docker", "volume", "rm"):
            return 1, "", "No such volume"
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[-1] + "\n", ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name.endswith("-docker"):
                return 0, "sidecar-ctr\n", ""
            if name.endswith("-agent"):
                return 0, "agent-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            return 0, '"27.0.0"\n', ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "agent-ctr",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://moonmind-session-sess-1-agent",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        docker_host="tcp://docker-proxy:2375",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    agent_run = next(
        command
        for command in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    assert "DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock" in agent_run
    assert "DOCKER_HOST=tcp://docker-proxy:2375" not in agent_run
    assert not any(
        command[:3] == ("docker", "network", "connect") for command in commands
    )

@pytest.mark.asyncio
async def test_controller_replaces_blank_request_moonmind_url(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_URL": "   "},
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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        moonmind_url="http://api:8000",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert "MOONMIND_URL=http://api:8000" in run_command

@pytest.mark.asyncio
async def test_controller_launch_normalizes_created_paths_for_container_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    commands: list[tuple[str, ...]] = []
    chown_calls: list[tuple[Path, int, int, bool]] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        chown_calls.append((Path(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )

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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    chowned_paths = {path for path, _uid, _gid, _follow_symlinks in chown_calls}
    assert {
        Path(request.workspace_path),
        Path(request.session_workspace_path),
        Path(request.artifact_spool_path),
    } <= chowned_paths
    assert all(uid == 1000 and gid == 1000 for _path, uid, gid, _follow in chown_calls)
    assert all(
        follow_symlinks is False
        for _path, _uid, _gid, follow_symlinks in chown_calls
    )
    assert any(command[:2] == ("docker", "run") for command in commands)

@pytest.mark.asyncio
async def test_controller_launch_clones_workspace_before_starting_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN", raising=False)
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "origin/dependabot/pip/requests-2.33.1",
            "targetBranch": "codex/session-fix",
        },
    )
    commands: list[tuple[str, ...]] = []
    chown_calls: list[tuple[Path, int, int, bool]] = []
    git_envs: list[dict[str, str] | None] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        chown_calls.append((Path(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[0] == "git":
            git_envs.append(env)
            assert (run_as_uid, run_as_gid) == (1000, 1000)
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("git", "clone"):
            Path(request.workspace_path).mkdir(parents=True, exist_ok=True)
            tracked_file = Path(request.workspace_path) / "README.md"
            tracked_file.write_text("content", encoding="utf-8")
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 1, "", "error: pathspec 'codex/session-fix' did not match any file(s) known to git"
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "-B",
            "codex/session-fix",
            "origin/codex/session-fix",
        ):
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "fetch",
            "origin",
            "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
        ):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload_input = json.loads(input_text or "{}")
            assert "workspaceSpec" not in payload_input
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    assert commands[0][:5] == (
        "git",
        "clone",
        "--branch",
        "dependabot/pip/requests-2.33.1",
        "--single-branch",
    )
    assert (
        "https://github.com/MoonLadderStudios/MoonMind.git" in commands[0]
    )
    assert request.workspace_path in commands[0]
    assert commands[1] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "codex/session-fix",
    )
    assert commands[2] == _workspace_git_command(
        request.workspace_path,
        "fetch",
        "origin",
        "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
    )
    assert commands[3] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "-B",
        "codex/session-fix",
        "origin/codex/session-fix",
    )
    assert git_envs == [
        {"LC_ALL": "C", "LANG": "C"},
        {"LC_ALL": "C", "LANG": "C"},
        {"LC_ALL": "C", "LANG": "C"},
        {"LC_ALL": "C", "LANG": "C"},
    ]
    assert Path(request.workspace_path).exists()
    assert Path(request.session_workspace_path).exists()
    assert Path(request.artifact_spool_path).exists()
    chowned_paths = {path for path, _uid, _gid, _follow_symlinks in chown_calls}
    assert Path(request.workspace_path).parent in chowned_paths
    assert Path(request.workspace_path) in chowned_paths
    assert Path(request.workspace_path, "README.md") in chowned_paths
    assert Path(request.session_workspace_path) in chowned_paths
    assert Path(request.artifact_spool_path) in chowned_paths

@pytest.mark.asyncio
async def test_controller_clone_uses_launch_scoped_github_token_for_git_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    token = "launch-secret-token-12345678901234567890"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"GITHUB_TOKEN": token},
        workspaceSpec={
            "repository": "g3-qrtr/crash_server_main",
            "startingBranch": "bugfix/kandy-3888-performance-blockers",
        },
    )
    git_envs: list[dict[str, str] | None] = []
    git_identities: list[tuple[int | None, int | None]] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        lambda *_args, **_kwargs: None,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            git_envs.append(env)
            git_identities.append((run_as_uid, run_as_gid))
        if command[:2] == ("git", "clone"):
            return (
                128,
                "",
                f"fatal: rejected GITHUB_TOKEN={token}",
            )
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await controller.launch_session(request)

    assert git_identities == [(1000, 1000)]
    assert len(git_envs) == 1
    git_env = git_envs[0]
    assert git_env is not None
    assert git_env["GITHUB_TOKEN"] == token
    assert git_env["GIT_TERMINAL_PROMPT"] == "0"
    assert git_env["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert git_env["GIT_CONFIG_VALUE_0"] == ""
    assert git_env["GIT_CONFIG_KEY_1"] == "credential.https://github.com.helper"
    assert 'password="$GITHUB_TOKEN"' in git_env["GIT_CONFIG_VALUE_1"]
    message = str(exc_info.value)
    assert token not in message
    assert "[REDACTED]" in message

@pytest.mark.asyncio
async def test_controller_clone_resolves_descriptor_for_git_without_container_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    token = "launch-secret-token-12345678901234567890"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        githubCredential=ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="MM320_GITHUB_TOKEN",
            required=True,
        ),
        workspaceSpec={"repository": "MoonLadderStudios/private-repo"},
    )
    git_envs: list[dict[str, str] | None] = []
    docker_commands: list[tuple[str, ...]] = []
    docker_envs: list[dict[str, str] | None] = []
    container_payloads: list[str | None] = []

    class _FakeGitHubAuthBrokers:
        def __init__(self) -> None:
            self.starts: list[dict[str, str]] = []
            self.stops: list[str] = []

        async def start(self, *, run_id: str, token: str, socket_path: str) -> None:
            self.starts.append(
                {"run_id": run_id, "token": token, "socket_path": socket_path}
            )

        async def stop(self, run_id: str) -> None:
            self.stops.append(run_id)

    github_auth_brokers = _FakeGitHubAuthBrokers()
    monkeypatch.setenv("MM320_GITHUB_TOKEN", token)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        lambda *_args, **_kwargs: None,
    )
    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        if command[0] == "git":
            git_envs.append(env)
            return 0, "", ""
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            docker_commands.append(command)
            docker_envs.append(env)
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            container_payloads.append(input_text)
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
        github_auth_brokers=github_auth_brokers,
    )

    handle = await controller.launch_session(request)

    assert git_envs
    assert git_envs[0] is not None
    assert git_envs[0]["GITHUB_TOKEN"] == token
    assert git_envs[0]["GIT_TERMINAL_PROMPT"] == "0"
    assert 'password="$GITHUB_TOKEN"' in git_envs[0]["GIT_CONFIG_VALUE_1"]
    assert github_auth_brokers.starts == [
        {
            "run_id": request.session_id,
            "token": token,
            "socket_path": DockerCodexManagedSessionController._build_github_socket_path(
                run_id=request.session_id,
                support_root=str(Path(request.session_workspace_path) / ".moonmind"),
            ),
        }
    ]
    socket_path = Path(github_auth_brokers.starts[0]["socket_path"])
    assert socket_path.name == "github.sock"
    assert len(str(socket_path).encode("utf-8")) < 100
    if socket_path.parent.parent.name == ".moonmind-gh":
        assert socket_path.parent.parent == workspace_root / ".moonmind-gh"
    else:
        assert socket_path.parent.parent == Path("/tmp") / "mm-gh"
    assert len(socket_path.parent.name) == 16
    assert request.session_workspace_path not in github_auth_brokers.starts[0]["socket_path"]
    docker_run_text = " ".join(docker_commands[0])
    assert token not in docker_run_text
    assert "GITHUB_TOKEN=" not in docker_run_text
    assert "GITHUB_TOKEN" in docker_commands[0]
    assert docker_envs[0] is not None
    assert docker_envs[0]["GITHUB_TOKEN"] == token
    assert "GIT_CONFIG_GLOBAL=" in docker_run_text
    assert "GIT_TERMINAL_PROMPT=0" in docker_run_text
    assert ".moonmind/bin" in docker_run_text
    assert container_payloads
    assert token not in str(container_payloads[0])
    assert "githubCredential" in str(container_payloads[0])
    assert '"environment": {"GITHUB_TOKEN"' not in str(container_payloads[0])
    assert "GIT_CONFIG_GLOBAL" in str(container_payloads[0])
    assert (Path(request.session_workspace_path) / ".moonmind" / "bin" / "gh").exists()
    assert (
        Path(request.session_workspace_path)
        / ".moonmind"
        / "bin"
        / "git-credential-moonmind"
    ).exists()
    git_config_text = (
        Path(request.session_workspace_path) / ".moonmind" / "gitconfig"
    ).read_text(encoding="utf-8")
    assert "moonmind-managed-git-config" in git_config_text
    assert "git-credential-moonmind" in git_config_text
    helper_text = (
        Path(request.session_workspace_path)
        / ".moonmind"
        / "bin"
        / "git-credential-moonmind"
    ).read_text(encoding="utf-8")
    gh_wrapper_text = (
        Path(request.session_workspace_path) / ".moonmind" / "bin" / "gh"
    ).read_text(encoding="utf-8")
    assert "from moonmind" not in helper_text
    assert "from moonmind" not in gh_wrapper_text

    await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId=handle.session_state.session_id,
            sessionEpoch=handle.session_state.session_epoch,
            containerId=handle.session_state.container_id,
            threadId=handle.session_state.thread_id,
            reason="test cleanup",
        )
    )
    assert github_auth_brokers.stops == [request.session_id]

def test_persist_brokered_github_config_preserves_container_visible_paths(
    tmp_path: Path,
) -> None:
    workspace_target = tmp_path / "workspace-target"
    workspace_target.mkdir()
    workspace_path = tmp_path / "workspace-link"
    workspace_path.symlink_to(workspace_target, target_is_directory=True)
    repo_git_config_path = workspace_path / ".git" / "config"
    repo_git_config_path.parent.mkdir()
    repo_git_config_path.write_text(
        "[core]\n\trepositoryformatversion = 0\n",
        encoding="utf-8",
    )
    existing_global_config = tmp_path / "existing.gitconfig"
    existing_global_config.write_text(
        "[user]\n\tname = Existing User\n",
        encoding="utf-8",
    )
    support_root = tmp_path / "session" / ".moonmind"
    session_environment = {
        "GIT_CONFIG_GLOBAL": str(existing_global_config),
    }

    touched_paths = DockerCodexManagedSessionController._persist_brokered_github_config(
        session_environment,
        workspace_path=str(workspace_path),
        support_root=support_root,
        github_socket_path="/tmp/github-auth.sock",
    )

    git_config_text = (support_root / "gitconfig").read_text(encoding="utf-8")
    assert f"\tpath = \"{existing_global_config}\"" in git_config_text
    assert f"\tdirectory = \"{workspace_path}\"" in git_config_text
    assert f"\tdirectory = \"{workspace_target}\"" not in git_config_text
    assert session_environment["GIT_CONFIG_GLOBAL"] == str(support_root / "gitconfig")
    assert session_environment["PATH"] == (
        f"{support_root / 'bin'}{os.pathsep}"
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    assert repo_git_config_path in touched_paths
    assert "# moonmind-credential-helper" in repo_git_config_path.read_text(
        encoding="utf-8"
    )
    gh_wrapper_text = (support_root / "bin" / "gh").read_text(encoding="utf-8")
    assert "real_gh_path" not in gh_wrapper_text
    assert "from moonmind" not in gh_wrapper_text
    assert "from moonmind" not in (
        support_root / "bin" / "git-credential-moonmind"
    ).read_text(encoding="utf-8")

@pytest.mark.asyncio
async def test_controller_reuses_resolved_git_environment_for_target_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True)
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        githubCredential=ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="MM320_GITHUB_TOKEN",
            required=True,
        ),
        workspaceSpec={
            "repository": "MoonLadderStudios/private-repo",
            "targetBranch": "feature/mm-320",
        },
    )
    token = "launch-secret-token-12345678901234567890"
    resolve_calls: list[ManagedGitHubCredentialDescriptor | None] = []
    git_envs: list[dict[str, str] | None] = []

    async def _fake_resolve(
        _environment: dict[str, str],
        *,
        github_credential: ManagedGitHubCredentialDescriptor | None = None,
    ) -> str:
        resolve_calls.append(github_credential)
        return token

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.resolve_github_token_for_launch",
        _fake_resolve,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        if command[0] != "git":
            raise AssertionError(f"unexpected command: {command}")
        git_envs.append(env)
        if command[-1] == "--is-inside-work-tree":
            return 0, "true\n", ""
        if command[-2:] == ("checkout", "feature/mm-320"):
            return 1, "", "pathspec 'feature/mm-320' did not match"
        if command[-3:] == (
            "fetch",
            "origin",
            "+refs/heads/feature/mm-320:refs/remotes/origin/feature/mm-320",
        ):
            return 0, "", ""
        if command[-4:] == (
            "checkout",
            "-B",
            "feature/mm-320",
            "origin/feature/mm-320",
        ):
            return 0, "", ""
        raise AssertionError(f"unexpected git command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller._ensure_workspace_paths(request)

    assert len(resolve_calls) == 1
    assert git_envs[0] == {"LC_ALL": "C", "LANG": "C"}
    assert len(git_envs) == 4
    for env in git_envs[1:]:
        assert env is not None
        assert env["GITHUB_TOKEN"] == token
        assert env["GIT_TERMINAL_PROMPT"] == "0"

@pytest.mark.asyncio
async def test_controller_required_github_descriptor_fails_before_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        githubCredential=ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="MM320_MISSING_GITHUB_TOKEN",
            required=True,
        ),
        workspaceSpec={"repository": "MoonLadderStudios/private-repo"},
    )

    monkeypatch.delenv("MM320_MISSING_GITHUB_TOKEN", raising=False)

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"unexpected command after missing credential: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="GitHub credential"):
        await controller.launch_session(request)

@pytest.mark.asyncio
async def test_controller_launch_redacts_github_token_from_command_failures(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"GITHUB_TOKEN": "ghp_inline_secret_token_12345678901234567890"},
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            assert "GITHUB_TOKEN=ghp_inline_secret_token_12345678901234567890" not in " ".join(
                command
            )
            assert env is not None
            assert env["GITHUB_TOKEN"] == "ghp_inline_secret_token_12345678901234567890"
            return (
                1,
                "",
                "docker run rejected GITHUB_TOKEN=ghp_inline_secret_token_12345678901234567890",
            )
        raise AssertionError(f"unexpected command: {command}")

    class _FakeGitHubAuthBrokers:
        async def start(self, *, run_id: str, token: str, socket_path: str) -> None:
            return None

        async def stop(self, run_id: str) -> None:
            return None

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
        github_auth_brokers=_FakeGitHubAuthBrokers(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await controller.launch_session(request)

    message = str(exc_info.value)
    assert "ghp_inline_secret_token_12345678901234567890" not in message
    assert "[REDACTED]" in message

@pytest.mark.asyncio
async def test_controller_launch_creates_target_branch_when_remote_branch_missing(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "dependabot/pip/requests-2.33.1",
            "targetBranch": "codex/session-fix",
        },
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
        if command[:2] == ("git", "clone"):
            Path(request.workspace_path).mkdir(parents=True, exist_ok=True)
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 1, "", "error: pathspec 'codex/session-fix' did not match any file(s) known to git"
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "-b",
            "codex/session-fix",
        ):
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "fetch",
            "origin",
            "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
        ):
            return (
                128,
                "",
                "fatal: couldn't find remote ref refs/heads/codex/session-fix",
            )
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    assert commands[2] == _workspace_git_command(
        request.workspace_path,
        "fetch",
        "origin",
        "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
    )
    assert commands[3] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "-b",
        "codex/session-fix",
    )

@pytest.mark.asyncio
async def test_controller_launch_reuses_existing_workspace_and_checks_out_target_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True, exist_ok=True)
    existing_git_ref = workspace_path / ".git" / "refs" / "heads" / "main"
    existing_git_ref.parent.mkdir(parents=True, exist_ok=True)
    existing_git_ref.write_text("abc123\n", encoding="utf-8")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "codex/session-fix",
        },
    )
    commands: list[tuple[str, ...]] = []
    git_identities: list[tuple[int | None, int | None]] = []
    chown_calls: list[tuple[Path, int, int, bool]] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )
    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        chown_calls.append((Path(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[0] == "git":
            chowned_paths = {path for path, _uid, _gid, _follow in chown_calls}
            assert workspace_path in chowned_paths
            assert workspace_path / ".git" in chowned_paths
            assert existing_git_ref in chowned_paths
            git_identities.append((run_as_uid, run_as_gid))
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command == _workspace_git_command(
            request.workspace_path,
            "rev-parse",
            "--is-inside-work-tree",
        ):
            return 0, "true\n", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    assert all(command[:2] != ("git", "clone") for command in commands)
    assert commands[0] == _workspace_git_command(
        request.workspace_path,
        "rev-parse",
        "--is-inside-work-tree",
    )
    assert commands[1] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "codex/session-fix",
    )
    chowned_paths = {path for path, _uid, _gid, _follow in chown_calls}
    assert workspace_path in chowned_paths
    assert workspace_path / ".git" in chowned_paths
    assert existing_git_ref in chowned_paths
    assert all(uid == 1000 and gid == 1000 for _path, uid, gid, _follow in chown_calls)
    assert all(follow is False for _path, _uid, _gid, follow in chown_calls)
    assert git_identities == [(1000, 1000), (1000, 1000)]


@pytest.mark.asyncio
async def test_controller_launch_fetches_branch_refspec_for_target_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 1000,
    )
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True, exist_ok=True)
    existing_git_ref = workspace_path / ".git" / "refs" / "heads" / "main"
    existing_git_ref.parent.mkdir(parents=True, exist_ok=True)
    existing_git_ref.write_text("abc123\n", encoding="utf-8")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "codex/session-fix",
        },
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
        if command == _workspace_git_command(
            request.workspace_path,
            "rev-parse",
            "--is-inside-work-tree",
        ):
            return 0, "true\n", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return (
                1,
                "",
                "error: pathspec 'codex/session-fix' did not match any file(s) known to git",
            )
        if command == _workspace_git_command(
            request.workspace_path,
            "fetch",
            "origin",
            "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
        ):
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "-B",
            "codex/session-fix",
            "origin/codex/session-fix",
        ):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    assert commands[0] == _workspace_git_command(
        request.workspace_path,
        "rev-parse",
        "--is-inside-work-tree",
    )
    assert commands[1] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "codex/session-fix",
    )
    assert commands[2] == _workspace_git_command(
        request.workspace_path,
        "fetch",
        "origin",
        "+refs/heads/codex/session-fix:refs/remotes/origin/codex/session-fix",
    )
    assert commands[3] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "-B",
        "codex/session-fix",
        "origin/codex/session-fix",
    )


@pytest.mark.asyncio
async def test_controller_launch_normalizes_support_paths_before_git_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    task_root = workspace_root / "mm:task-1"
    workspace_path = task_root / "repo"
    session_path = task_root / "session"
    artifacts_path = task_root / "artifacts"
    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / ".git").mkdir()
    session_path.mkdir()
    artifacts_path.mkdir()
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(session_path),
        artifactSpoolPath=str(artifacts_path),
        codexHomePath=str(task_root / ".moonmind" / "codex-home"),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "codex/session-fix",
        },
    )
    commands: list[tuple[str, ...]] = []
    chown_calls: list[Path] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        assert uid == 1000
        assert gid == 1000
        assert follow_symlinks is False
        chown_calls.append(Path(path))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[0] == "git":
            assert session_path in chown_calls
            assert artifacts_path in chown_calls
            assert task_root / ".moonmind" in chown_calls
            assert task_root / ".moonmind" / "codex-home" in chown_calls
        if command == _workspace_git_command(
            request.workspace_path,
            "rev-parse",
            "--is-inside-work-tree",
        ):
            return 0, "true\n", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 2, "", "fatal: checkout backend aborted"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="checkout backend aborted"):
        await controller.launch_session(request)

    assert all(command[:2] != ("docker", "run") for command in commands)
    assert session_path in chown_calls
    assert artifacts_path in chown_calls


@pytest.mark.asyncio
async def test_controller_normalizes_repo_artifacts_created_after_session_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    context_path = workspace_path / "artifacts" / "context"
    context_path.mkdir(parents=True)
    context_file = context_path / "rag-context.json"
    context_file.write_text("{}\n", encoding="utf-8")
    outside_path = tmp_path / "outside"
    outside_path.mkdir()
    (outside_path / "sensitive.txt").write_text("unchanged\n", encoding="utf-8")
    (workspace_path / "artifacts" / "outside-link").symlink_to(
        outside_path,
        target_is_directory=True,
    )
    fchown_calls: list[tuple[int, int, int]] = []
    chown_calls: list[tuple[str, int, int, int | None, bool]] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        chown_calls.append((str(path), uid, gid, dir_fd, follow_symlinks))

    def _fake_fchown(fd: int, uid: int, gid: int) -> None:
        fchown_calls.append((fd, uid, gid))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.fchown",
        _fake_fchown,
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
    )

    await controller.ensure_repo_artifacts_writable_by_runtime_user(
        str(workspace_path)
    )

    assert len(fchown_calls) == 1
    assert fchown_calls[0][1:] == (1000, 1000)
    assert {name for name, _uid, _gid, _dir_fd, _follow in chown_calls} == {
        "context",
        "outside-link",
        "rag-context.json",
    }
    assert all(
        uid == 1000 and gid == 1000
        for _name, uid, gid, _dir_fd, _follow in chown_calls
    )
    assert all(
        isinstance(dir_fd, int) and follow is False
        for _name, _uid, _gid, dir_fd, follow in chown_calls
    )


@pytest.mark.asyncio
async def test_controller_rejects_repo_artifacts_outside_workspace_root(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    outside_workspace = tmp_path / "outside" / "repo"
    (outside_workspace / "artifacts").mkdir(parents=True)
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
    )

    with pytest.raises(RuntimeError, match="within the configured workspace root"):
        await controller.ensure_repo_artifacts_writable_by_runtime_user(
            str(outside_workspace)
        )


@pytest.mark.asyncio
async def test_controller_rejects_repo_artifacts_symlink(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True)
    outside_artifacts = tmp_path / "outside-artifacts"
    outside_artifacts.mkdir()
    (workspace_path / "artifacts").symlink_to(
        outside_artifacts,
        target_is_directory=True,
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
    )

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        await controller.ensure_repo_artifacts_writable_by_runtime_user(
            str(workspace_path)
        )


@pytest.mark.asyncio
async def test_controller_rejects_repo_artifacts_symlink_swap_before_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    outside_artifacts = tmp_path / "outside-artifacts"
    outside_artifacts.mkdir()
    real_open = os.open

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _swap_then_open(path: str | Path, flags: int) -> int:
        artifacts_path.rmdir()
        artifacts_path.symlink_to(outside_artifacts, target_is_directory=True)
        return real_open(path, flags)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.open",
        _swap_then_open,
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
    )

    with pytest.raises(
        RuntimeError,
        match="could not be opened without following symlinks",
    ):
        await controller.ensure_repo_artifacts_writable_by_runtime_user(
            str(workspace_path)
        )


@pytest.mark.asyncio
async def test_controller_launch_reclones_invalid_workspace_before_target_checkout(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True, exist_ok=True)
    stale_file = workspace_path / "stale.txt"
    stale_file.write_text("stale")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "codex/session-fix",
        },
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
        if command == _workspace_git_command(
            request.workspace_path,
            "rev-parse",
            "--is-inside-work-tree",
        ):
            return 128, "", "fatal: not a git repository"
        if command[:2] == ("git", "clone"):
            assert not stale_file.exists()
            Path(request.workspace_path).mkdir(parents=True, exist_ok=True)
            return 0, "", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    assert commands[0] == _workspace_git_command(
        request.workspace_path,
        "rev-parse",
        "--is-inside-work-tree",
    )
    assert commands[1][:2] == ("git", "clone")
    assert commands[2] == _workspace_git_command(
        request.workspace_path,
        "checkout",
        "codex/session-fix",
    )

@pytest.mark.asyncio
async def test_controller_launch_trusts_workspace_git_commands_for_container_owned_repo(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True, exist_ok=True)
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_root / "mm:task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "codex/session-fix",
        },
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command == _workspace_git_command(
            request.workspace_path,
            "rev-parse",
            "--is-inside-work-tree",
        ):
            return 0, "true\n", ""
        if command == _workspace_git_command(
            request.workspace_path,
            "checkout",
            "codex/session-fix",
        ):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        if command[:2] == ("git", "-C"):
            return (
                128,
                "",
                f"fatal: detected dubious ownership in repository at '{request.workspace_path}'",
            )
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
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
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
    assert len(commands) == 1
    exec_command = commands[0]
    assert exec_command[:3] == ("docker", "exec", "-i")
    assert "-c" not in exec_command
    assert exec_command[-2:] == ("invoke", "send_turn")

@pytest.mark.asyncio
async def test_controller_send_turn_polls_session_status_until_completed() -> None:
    commands: list[tuple[str, ...]] = []
    session_status_calls = 0
    session_status_payloads: list[dict[str, object]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        nonlocal session_status_calls
        commands.append(command)
        if command[:3] == ("docker", "exec", "-i") and "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": "vendor-turn-1",
                },
                "turnId": "vendor-turn-1",
                "status": "running",
                "metadata": {},
            }
            return 0, json.dumps(payload), ""
        if command[:3] == ("docker", "exec", "-i") and "session_status" in command:
            session_status_payloads.append(json.loads(input_text or "{}"))
            session_status_calls += 1
            if session_status_calls == 1:
                payload = {
                    "sessionState": {
                        "sessionId": "sess-1",
                        "sessionEpoch": 1,
                        "containerId": "ctr-1",
                        "threadId": "logical-thread-1",
                        "activeTurnId": "vendor-turn-1",
                    },
                    "status": "busy",
                    "metadata": {
                        "lastTurnId": "vendor-turn-1",
                        "lastTurnStatus": "running",
                    },
                }
                return 0, json.dumps(payload), ""
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": None,
                },
                "status": "ready",
                "metadata": {
                    "lastTurnId": "vendor-turn-1",
                    "lastTurnStatus": "completed",
                    "lastAssistantText": "OK",
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        turn_poll_interval_seconds=0,
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
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata["assistantText"] == "OK"
    assert len(session_status_payloads) == 2
    for payload in session_status_payloads:
        assert payload["sessionId"] == "sess-1"
        assert payload["sessionEpoch"] == 1
        assert payload["containerId"] == "ctr-1"
        assert payload["threadId"] == "logical-thread-1"
        assert "instructions" not in payload
        assert "reason" not in payload
    assert any(command[-2:] == ("invoke", "send_turn") for command in commands)
    assert any(command[-2:] == ("invoke", "session_status") for command in commands)

@pytest.mark.asyncio
async def test_controller_send_turn_does_not_poll_session_status_for_terminal_failure() -> None:
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ("docker", "exec", "-i") and "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {"reason": "provider model deprecated"},
            }
            return 0, json.dumps(payload), ""
        if command[:3] == ("docker", "exec", "-i") and "session_status" in command:
            raise AssertionError("session_status should not be called for terminal failure")
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        turn_poll_interval_seconds=0,
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

    assert response.status == "failed"
    assert response.metadata["reason"] == "provider model deprecated"
    assert commands == [
        (
            "docker",
            "exec",
            "-i",
            "ctr-1",
            "python3",
            "-m",
            "moonmind.workflows.temporal.runtime.codex_session_runtime",
            "invoke",
            "send_turn",
        )
    ]

@pytest.mark.asyncio
async def test_controller_send_turn_recovers_from_blank_output_using_runtime_state(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    session_workspace = tmp_path / "work" / "session"
    session_workspace.mkdir(parents=True, exist_ok=True)
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath=str(session_workspace),
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    (session_workspace / ".moonmind-codex-session-state.json").write_text(
        json.dumps(
            {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "logicalThreadId": "logical-thread-1",
                "vendorThreadId": "vendor-thread-1",
                "containerId": "ctr-1",
                "activeTurnId": "vendor-turn-1",
                "lastControlAction": "send_turn",
                "lastTurnId": "vendor-turn-1",
                "lastTurnStatus": "running",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "send_turn" in command:
            return 0, "   \n", ""
        if command[:3] == ("docker", "exec", "-i") and "session_status" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": None,
                },
                "status": "ready",
                "metadata": {
                    "lastTurnId": "vendor-turn-1",
                    "lastTurnStatus": "completed",
                    "lastAssistantText": "Recovered OK",
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
        turn_poll_interval_seconds=0,
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
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata["assistantText"] == "Recovered OK"

@pytest.mark.asyncio
async def test_controller_send_turn_does_not_recover_stale_completed_turn_state(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    session_workspace = tmp_path / "work" / "session"
    session_workspace.mkdir(parents=True, exist_ok=True)
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath=str(session_workspace),
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    (session_workspace / ".moonmind-codex-session-state.json").write_text(
        json.dumps(
            {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "logicalThreadId": "logical-thread-1",
                "vendorThreadId": "vendor-thread-1",
                "containerId": "ctr-1",
                "activeTurnId": None,
                "lastControlAction": "send_turn",
                "lastTurnId": "vendor-turn-prior",
                "lastTurnStatus": "completed",
                "lastAssistantText": "stale output",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "send_turn" in command:
            return 0, "   \n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    with pytest.raises(RuntimeError, match="returned no JSON output"):
        await controller.send_turn(
            SendCodexManagedSessionTurnRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                instructions="Reply with exactly the word OK",
            )
        )

@pytest.mark.asyncio
async def test_controller_send_turn_times_out_when_session_never_reaches_terminal_state(
) -> None:
    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": "vendor-turn-1",
                },
                "turnId": "vendor-turn-1",
                "status": "running",
                "metadata": {},
            }
            return 0, json.dumps(payload), ""
        if command[:3] == ("docker", "exec", "-i") and "session_status" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": "vendor-turn-1",
                },
                "status": "busy",
                "metadata": {
                    "lastTurnId": "vendor-turn-1",
                    "lastTurnStatus": "running",
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        turn_poll_interval_seconds=0,
        turn_poll_timeout_seconds=0,
    )

    with pytest.raises(
        RuntimeError,
        match="timed out waiting for terminal managed-session turn status",
    ):
        await controller.send_turn(
            SendCodexManagedSessionTurnRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                instructions="Reply with exactly the word OK",
            )
        )

@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "stdout",
        "stderr",
        "expected_reason",
        "expected_detail_fragments",
        "unexpected_fragments",
    ),
    [
        (
            "   \n",
            "",
            "returned no JSON output",
            ["stdout was blank"],
            [],
        ),
        (
            "invalid\njson",
            "stderr\nline",
            "returned invalid JSON",
            [
                "stdout=" + json.dumps("invalid\njson"),
                "stderr: " + json.dumps("stderr\nline"),
            ],
            ["invalid\njson", "stderr\nline"],
        ),
        (
            "[1, 2, 3]",
            "stderr\nline",
            "returned a list payload instead of a JSON object",
            [
                "stdout=" + json.dumps("[1, 2, 3]"),
                "stderr: " + json.dumps("stderr\nline"),
            ],
            ["stderr\nline"],
        ),
    ],
)
async def test_controller_send_turn_rejects_malformed_transport_output(
    stdout: str,
    stderr: str,
    expected_reason: str,
    expected_detail_fragments: list[str],
    unexpected_fragments: list[str],
) -> None:
    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            return 0, stdout, stderr
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    with pytest.raises(RuntimeError, match=expected_reason) as exc_info:
        await controller.send_turn(
            SendCodexManagedSessionTurnRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                instructions="Reply with exactly the word OK",
            )
        )

    message = str(exc_info.value)
    assert "managed-session action send_turn" in message
    assert "session sess-1" in message
    assert "container ctr-1" in message
    for fragment in [expected_reason, *expected_detail_fragments]:
        assert fragment in message
    for fragment in unexpected_fragments:
        assert fragment not in message

@pytest.mark.asyncio
async def test_controller_send_turn_emits_follow_up_reason_in_session_events(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()
    session_supervisor.emit_session_event = Mock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
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
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
            reason="Operator follow-up",
        )
    )

    emitted_metadata = [
        call.kwargs.get("metadata")
        for call in session_supervisor.emit_session_event.call_args_list
    ]
    emitted_kinds = [
        call.kwargs.get("kind")
        for call in session_supervisor.emit_session_event.call_args_list
    ]
    assert emitted_kinds == [
        "user_message_submitted",
        "turn_started",
        "assistant_message",
        "assistant_message_completed",
        "turn_completed",
    ]
    assert emitted_metadata == [
        {
            "action": "send_turn",
            "messageLength": len("Reply with exactly the word OK"),
            "reason": "Operator follow-up",
        },
        {"action": "send_turn", "reason": "Operator follow-up"},
        {
            "action": "send_turn",
            "contentLength": len("OK"),
            "reason": "Operator follow-up",
        },
        {
            "action": "send_turn",
            "contentLength": len("OK"),
            "reason": "Operator follow-up",
        },
        {
            "action": "send_turn",
            "assistantMessageLength": len("OK"),
            "reason": "Operator follow-up",
        },
    ]


@pytest.mark.asyncio
async def test_controller_send_turn_maps_reliable_native_markers_and_turn_failure(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()
    session_supervisor.emit_session_event = Mock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {
                    "reason": "model provider rejected the request",
                    "failureClass": "permanent",
                    "observabilityEvents": [
                        {
                            "kind": "runtime_status",
                            "text": "Runtime reported a provider error.",
                            "metadata": {"status": "provider_error"},
                        },
                        {
                            "kind": "tool_call_started",
                            "text": "Tool call started.",
                            "metadata": {
                                "toolName": "shell",
                                "arguments": {"command": "pytest"},
                                "tags": ["test"],
                                "empty": {},
                                "blank": " ",
                            },
                        },
                        {
                            "kind": "unknown_native_marker",
                            "text": "Ignored marker.",
                        },
                    ],
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Run the requested check",
            reason="Operator follow-up",
        )
    )

    emitted = session_supervisor.emit_session_event.call_args_list
    emitted_kinds = [call.kwargs["kind"] for call in emitted]
    assert emitted_kinds == [
        "user_message_submitted",
        "turn_started",
        "runtime_status",
        "tool_call_started",
        "turn_failed",
    ]
    assert emitted[-1].kwargs["metadata"] == {
        "action": "send_turn",
        "reason": "Operator follow-up",
        "failureClass": "permanent",
        "error": "model provider rejected the request",
    }
    assert emitted[3].kwargs["metadata"] == {
        "toolName": "shell",
        "arguments": {"command": "pytest"},
        "tags": ["test"],
    }

@pytest.mark.asyncio
async def test_controller_session_status_emits_session_resumed_event(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            activeTurnId="turn-stale",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()
    session_supervisor.emit_session_event = Mock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "session_status" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": "turn-fresh",
                },
                "status": "ready",
                "imageRef": "img",
                "controlUrl": "docker-exec://ctr-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    await controller.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
        )
    )

    emitted_calls = session_supervisor.emit_session_event.call_args_list
    assert [call.kwargs["kind"] for call in emitted_calls] == [
        "session_resumed",
        "runtime_status",
    ]
    assert emitted_calls[0].kwargs["active_turn_id"] == "turn-fresh"
    assert emitted_calls[0].kwargs["metadata"] == {"action": "resume_session"}
    assert emitted_calls[1].kwargs["metadata"] == {
        "status": "resumed",
        "action": "resume_session",
    }
    refreshed = store.load("sess-1")
    assert refreshed is not None
    assert refreshed.active_turn_id == "turn-fresh"

@pytest.mark.asyncio
async def test_controller_steer_turn_emits_normalized_session_annotation(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()
    session_supervisor.emit_session_event = Mock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "steer_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": "turn-1",
                },
                "turnId": "turn-1",
                "status": "running",
                "metadata": {},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    await controller.steer_turn(
        SteerCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            turnId="turn-1",
            instructions="Revise the plan",
            metadata={"reason": "operator_steer", "action": "override_attempt"},
        )
    )

    emitted_call = session_supervisor.emit_session_event.call_args
    assert emitted_call.kwargs["kind"] == "system_annotation"
    assert emitted_call.kwargs["turn_id"] == "turn-1"
    assert emitted_call.kwargs["metadata"] == {
        "action": "steer_turn",
        "reason": "operator_steer",
    }

@pytest.mark.asyncio
async def test_controller_clear_and_terminate_preserve_container_boundary(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    commands: list[tuple[str, ...]] = []
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()

    async def _publish_reset_artifacts(
        *,
        previous_record: CodexManagedSessionRecord,
        record: CodexManagedSessionRecord,
        action: str,
        reason: str | None,
    ):
        assert previous_record.session_epoch == 1
        assert previous_record.thread_id == "logical-thread-1"
        assert record.session_epoch == 2
        assert record.thread_id == "logical-thread-2"
        assert action == "clear_session"
        assert reason is None
        return await store.update(
            record.session_id,
            latest_control_event_ref="sess-1/session.control_event.epoch-2.json",
            latest_reset_boundary_ref="sess-1/session.reset_boundary.epoch-2.json",
        )

    session_supervisor.publish_reset_artifacts.side_effect = _publish_reset_artifacts

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
        session_store=store,
        session_supervisor=session_supervisor,
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
    stored = store.load("sess-1")
    assert stored is not None
    assert stored.session_epoch == 2
    assert stored.thread_id == "logical-thread-2"
    assert stored.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert stored.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    session_supervisor.publish_reset_artifacts.assert_awaited_once()
    publish_kwargs = session_supervisor.publish_reset_artifacts.await_args.kwargs
    assert publish_kwargs["previous_record"].session_epoch == 1
    assert publish_kwargs["previous_record"].thread_id == "logical-thread-1"
    assert publish_kwargs["record"].session_epoch == 2
    assert publish_kwargs["record"].thread_id == "logical-thread-2"
    assert terminated.status == "terminated"
    emitted_call = session_supervisor.emit_session_event.call_args
    assert emitted_call.kwargs["kind"] == "session_terminated"
    assert emitted_call.kwargs["metadata"] == {
        "action": "terminate_session",
        "reason": None,
    }
    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")

@pytest.mark.asyncio
async def test_controller_duplicate_terminate_retries_container_cleanup(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="terminated",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "transient docker cleanup failure"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=_fake_runner,
    )

    handle = await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )

    assert handle.status == "terminated"
    assert commands == [("docker", "rm", "-f", "ctr-1")]

@pytest.mark.asyncio
async def test_controller_terminate_removes_session_docker_sidecar_resources(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    workspace_root = tmp_path / "agent_jobs"
    session_workspace = workspace_root / "task-1" / "session"
    docker_config = session_workspace / ".docker" / "config.json"
    docker_config.parent.mkdir(parents=True)
    docker_config.write_text(
        '{"auths":{"ghcr.io":{"auth":"secret"}}}\n',
        encoding="utf-8",
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="moonmind-session-sess-1-agent-id",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://moonmind-session-sess-1-agent",
            status="ready",
            workspacePath=str(workspace_root / "task-1" / "repo"),
            sessionWorkspacePath=str(session_workspace),
            artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
            metadata={"dockerSidecarEnabled": True},
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        session_store=store,
        command_runner=_fake_runner,
    )

    await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="moonmind-session-sess-1-agent-id",
            threadId="thread-1",
            reason="test cleanup",
        )
    )

    assert ("docker", "rm", "-f", "moonmind-session-sess-1-agent-id") in commands
    assert ("docker", "rm", "-f", "moonmind-session-sess-1-docker") in commands
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-graph",
    ) in commands
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-socket",
    ) in commands
    assert not docker_config.exists()

@pytest.mark.asyncio
async def test_controller_terminate_without_store_removes_deterministic_sidecar(
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path / "agent_jobs"),
        command_runner=_fake_runner,
    )

    handle = await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="agent-ctr",
            threadId="logical-thread-1",
        )
    )

    assert handle.status == "terminated"
    assert ("docker", "rm", "-f", "agent-ctr") in commands
    assert ("docker", "rm", "-f", "moonmind-session-sess-1-docker") in commands
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-graph",
    ) in commands
    assert (
        "docker",
        "volume",
        "rm",
        "-f",
        "moonmind-session-sess-1-docker-socket",
    ) in commands

@pytest.mark.asyncio
async def test_controller_duplicate_terminate_rejects_stale_locator(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-2",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="terminated",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    runner = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=runner,
    )

    with pytest.raises(
        RuntimeError,
        match="sessionEpoch does not match the durable managed session record",
    ):
        await controller.terminate_session(
            TerminateCodexManagedSessionRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
            )
        )

    runner.assert_not_awaited()

@pytest.mark.asyncio
async def test_controller_duplicate_launch_reuses_existing_live_record(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/tmp/codex-home",
        imageRef="img",
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        commands.append(command)
        if command == ("docker", "inspect", "-f", "{{.Id}}", "ctr-1"):
            return 0, "ctr-1\n", ""
        if command == ("docker", "inspect", "-f", "{{.Image}}", "ctr-1"):
            return 0, "sha256:current\n", ""
        if command == ("docker", "image", "inspect", "-f", "{{.Id}}", "img"):
            return 0, "sha256:current\n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.container_id == "ctr-1"
    assert commands == [
        ("docker", "inspect", "-f", "{{.Id}}", "ctr-1"),
        ("docker", "inspect", "-f", "{{.Image}}", "ctr-1"),
        ("docker", "image", "inspect", "-f", "{{.Id}}", "img"),
    ]


@pytest.mark.asyncio
async def test_controller_duplicate_launch_recreates_stale_mutable_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(tmp_path / "agent_jobs" / "task-1" / "repo"),
        sessionWorkspacePath=str(tmp_path / "agent_jobs" / "task-1" / "session"),
        artifactSpoolPath=str(tmp_path / "agent_jobs" / "task-1" / "artifacts"),
        codexHomePath="/tmp/codex-home",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-old",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef=request.image_ref,
            controlUrl="docker-exec://ctr-old",
            status="ready",
            workspacePath=request.workspace_path,
            sessionWorkspacePath=request.session_workspace_path,
            artifactSpoolPath=request.artifact_spool_path,
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        if command == ("docker", "inspect", "-f", "{{.Id}}", "ctr-old"):
            return 0, "ctr-old\n", ""
        if command == ("docker", "inspect", "-f", "{{.Image}}", "ctr-old"):
            return 0, "sha256:stale\n", ""
        if command == (
            "docker",
            "image",
            "inspect",
            "-f",
            "{{.Id}}",
            request.image_ref,
        ):
            return 0, "sha256:current\n", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path / "agent_jobs"),
        session_store=store,
        command_runner=runner,
    )
    relaunch = AsyncMock(side_effect=RuntimeError("stale image relaunch"))
    monkeypatch.setattr(controller, "_ensure_workspace_paths", relaunch)

    with pytest.raises(RuntimeError, match="stale image relaunch"):
        await controller.launch_session(request)

    relaunch.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_container_image_inspection_only_treats_not_found_as_stale(
    tmp_path: Path,
) -> None:
    image_result = (1, "", "Error: No such image: runtime:latest")

    async def runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        if command == ("docker", "inspect", "-f", "{{.Image}}", "ctr-1"):
            return 0, "sha256:current\n", ""
        if command == (
            "docker",
            "image",
            "inspect",
            "-f",
            "{{.Id}}",
            "runtime:latest",
        ):
            return image_result
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path / "agent_jobs"),
        session_store=ManagedSessionStore(tmp_path / "session-store"),
        command_runner=runner,
    )

    assert not await controller._container_uses_current_image(
        container_id="ctr-1",
        image_ref="runtime:latest",
    )

    image_result = (1, "", "Error response from daemon: API unavailable")
    with pytest.raises(
        RuntimeError,
        match="failed to inspect configured managed session image:.*API unavailable",
    ):
        await controller._container_uses_current_image(
            container_id="ctr-1",
            image_ref="runtime:latest",
        )


def test_controller_launch_duplicate_match_requires_epoch_and_thread(
    tmp_path: Path,
) -> None:
    base_request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        sessionEpoch=1,
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/tmp/codex-home",
        imageRef="img",
    )
    record = CodexManagedSessionRecord(
        sessionId="sess-1",
        sessionEpoch=1,
        agentRunId="task-1",
        containerId="ctr-1",
        threadId="logical-thread-1",
        runtimeId="codex_cli",
        imageRef="img",
        controlUrl="docker-exec://ctr-1",
        status="ready",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        startedAt="2026-04-06T12:00:00Z",
    )

    assert DockerCodexManagedSessionController._request_matches_record(
        base_request,
        record,
    )
    assert not DockerCodexManagedSessionController._request_matches_record(
        base_request.model_copy(update={"session_epoch": 2}),
        record,
    )
    assert not DockerCodexManagedSessionController._request_matches_record(
        base_request.model_copy(update={"thread_id": "logical-thread-2"}),
        record,
    )

@pytest.mark.asyncio
async def test_controller_duplicate_clear_returns_existing_advanced_epoch(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-2",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            latestResetBoundaryRef="sess-1/session.reset_boundary.epoch-2.json",
            startedAt="2026-04-06T12:00:00Z",
        )
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
        session_store=store,
        command_runner=_fake_runner,
    )

    handle = await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
        )
    )

    assert handle.status == "ready"
    assert handle.session_state.session_epoch == 2
    assert handle.session_state.thread_id == "logical-thread-2"

@pytest.mark.asyncio
async def test_controller_clear_session_publishes_durable_reset_artifacts(
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []
    store = ManagedSessionStore(tmp_path / "session-store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )

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
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=supervisor,
        command_runner=_fake_runner,
    )

    cleared = await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
            reason="reset stale context",
        )
    )
    stored = store.load("sess-1")
    assert stored is not None

    assert cleared.session_state.session_epoch == 2
    assert stored.thread_id == "logical-thread-2"
    assert stored.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert stored.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    control_payload = json.loads(
        artifact_storage.resolve_storage_path(
            stored.latest_control_event_ref
        ).read_text(encoding="utf-8")
    )
    assert control_payload["reason"] == "reset stale context"

    summary = await controller.fetch_session_summary(
        FetchCodexManagedSessionSummaryRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
        )
    )
    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
            agentRunId="task-1",
        )
    )

    assert summary.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert summary.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert publication.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"


@pytest.mark.asyncio
async def test_controller_clear_session_request_id_returns_published_duplicate(
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []
    store = ManagedSessionStore(tmp_path / "session-store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "clear_session" in command:
            return (
                0,
                json.dumps(
                    {
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
                ),
                "",
            )
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=supervisor,
        command_runner=_fake_runner,
    )

    request = CodexManagedSessionClearRequest(
        sessionId="sess-1",
        sessionEpoch=1,
        containerId="ctr-1",
        threadId="logical-thread-1",
        newThreadId="logical-thread-2",
        requestId="clear-request-1",
        reason="reset stale context",
    )
    first = await controller.clear_session(request)
    duplicate = await controller.clear_session(request)

    assert first.session_state.session_epoch == 2
    assert duplicate.session_state.session_epoch == 2
    assert duplicate.metadata["idempotentReplay"] is True
    assert duplicate.metadata["requestId"] == "clear-request-1"
    assert len([command for command in commands if "clear_session" in command]) == 1
    stored = store.load("sess-1")
    assert stored is not None
    assert stored.metadata["lastClearRequest"]["status"] == "completed"
    assert stored.metadata["lastClearRequest"]["requestId"] == "clear-request-1"


@pytest.mark.asyncio
async def test_controller_send_turn_tolerates_event_publication_failure(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()
    session_supervisor.emit_session_event = Mock(
        side_effect=RuntimeError("publisher unavailable")
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
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
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"


@pytest.mark.asyncio
async def test_controller_send_turn_records_empty_assistant_metadata(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ctr-1",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = Mock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {
                    "reason": (
                        "codex app-server turn/completed produced no assistant output"
                    ),
                    "failureClass": "transient",
                    "failureCause": "app_server_protocol_empty_turn",
                    "retryRecommendedAction": "clear_session",
                },
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Inspect the workspace",
        )
    )

    assert response.status == "failed"
    stored = store.load("sess-1")
    assert stored is not None
    empty_turn = stored.metadata["emptyAssistantTurn"]
    assert empty_turn["failureCause"] == "app_server_protocol_empty_turn"
    assert empty_turn["consecutiveCount"] == 1
    session_supervisor.emit_session_event.assert_any_call(
        record=stored,
        text=(
            "Codex app-server completed a turn without assistant output; "
            "session clear is recommended."
        ),
        kind="empty_assistant_turn_detected",
        turn_id="vendor-turn-1",
        active_turn_id=None,
        metadata={
            "action": "send_turn",
            "failureCause": "app_server_protocol_empty_turn",
            "retryRecommendedAction": "clear_session",
            "reason": "codex app-server turn/completed produced no assistant output",
            "consecutiveCount": 1,
        },
    )


@pytest.mark.asyncio
async def test_controller_clear_session_preserves_retrieval_metadata_in_durable_outputs(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            metadata={
                "latestContextPackRef": "artifacts/context/rag-context-abc123.json",
                "retrievedContextArtifactPath": "artifacts/context/rag-context-abc123.json",
                "retrievedContextTransport": "direct",
                "retrievedContextItemCount": 2,
                "retrievalDurabilityAuthority": "artifact_ref",
                "sessionContinuityCacheStatus": "advisory_only",
            },
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
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
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=supervisor,
        command_runner=_fake_runner,
    )

    await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
            reason="reset stale context",
        )
    )

    stored = store.load("sess-1")
    assert stored is not None
    assert stored.metadata["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"

    summary = await controller.fetch_session_summary(
        FetchCodexManagedSessionSummaryRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
        )
    )
    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
            agentRunId="task-1",
        )
    )

    assert summary.metadata["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"
    assert publication.metadata["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"

    boundary_payload = json.loads(
        artifact_storage.resolve_storage_path(stored.latest_reset_boundary_ref).read_text(encoding="utf-8")
    )
    assert boundary_payload["metadata"]["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"


@pytest.mark.asyncio
async def test_controller_clear_session_rejects_stale_durable_locator(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )
    runner = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=runner,
    )

    with pytest.raises(
        RuntimeError,
        match="sessionEpoch does not match the durable managed session record",
    ):
        await controller.clear_session(
            CodexManagedSessionClearRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                newThreadId="logical-thread-2",
            )
        )

    runner.assert_not_awaited()

@pytest.mark.asyncio
async def test_controller_summary_and_publication_read_from_durable_record(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            stdoutArtifactRef="sess-1/stdout.log",
            stderrArtifactRef="sess-1/stderr.log",
            diagnosticsRef="sess-1/diagnostics.json",
            observabilityEventsRef="sess-1/observability.events.jsonl",
            latestSummaryRef="sess-1/session.summary.json",
            latestCheckpointRef="sess-1/session.step_checkpoint.json",
            latestControlEventRef="sess-1/session.control_event.epoch-2.json",
            latestResetBoundaryRef="sess-1/session.reset_boundary.epoch-2.json",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=AsyncMock(),
    )

    summary = await controller.fetch_session_summary(
        FetchCodexManagedSessionSummaryRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
        )
    )
    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
            agentRunId="task-1",
        )
    )

    assert summary.latest_summary_ref == "sess-1/session.summary.json"
    assert summary.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert summary.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert summary.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert summary.metadata["stdoutArtifactRef"] == "sess-1/stdout.log"
    assert summary.metadata["observabilityEventsRef"] == "sess-1/observability.events.jsonl"
    assert publication.published_artifact_refs == (
        "sess-1/stdout.log",
        "sess-1/stderr.log",
        "sess-1/diagnostics.json",
        "sess-1/observability.events.jsonl",
        "sess-1/session.summary.json",
        "sess-1/session.step_checkpoint.json",
        "sess-1/session.control_event.epoch-2.json",
        "sess-1/session.reset_boundary.epoch-2.json",
    )
    assert publication.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert publication.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert publication.metadata["observabilityEventsRef"] == "sess-1/observability.events.jsonl"

@pytest.mark.asyncio
async def test_controller_publication_uses_snapshot_without_stopping_supervision(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = AsyncMock()
    published_record = CodexManagedSessionRecord(
        sessionId="sess-1",
        sessionEpoch=2,
        agentRunId="task-1",
        containerId="ctr-1",
        threadId="thread-2",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl="docker-exec://mm-codex-session-sess-1",
        status="ready",
        workspacePath="/work/agent_jobs/task-1/repo",
        sessionWorkspacePath="/work/agent_jobs/task-1/session",
        artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
        stdoutArtifactRef="sess-1/stdout.log",
        stderrArtifactRef="sess-1/stderr.log",
        diagnosticsRef="sess-1/diagnostics.json",
        observabilityEventsRef="sess-1/observability.events.jsonl",
        latestSummaryRef="sess-1/session.summary.json",
        latestCheckpointRef="sess-1/session.step_checkpoint.json",
        startedAt="2026-04-06T12:00:00Z",
    )
    session_supervisor.publish_snapshot.return_value = published_record
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=AsyncMock(),
    )

    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
            agentRunId="task-1",
        )
    )

    session_supervisor.publish_snapshot.assert_awaited_once_with("sess-1")
    session_supervisor.finalize.assert_not_called()
    assert publication.published_artifact_refs == (
        "sess-1/stdout.log",
        "sess-1/stderr.log",
        "sess-1/diagnostics.json",
        "sess-1/observability.events.jsonl",
        "sess-1/session.summary.json",
        "sess-1/session.step_checkpoint.json",
    )
    assert publication.metadata["observabilityEventsRef"] == "sess-1/observability.events.jsonl"

@pytest.mark.asyncio
async def test_controller_reconcile_reattaches_or_degrades_active_sessions(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-ok",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-ok",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-missing",
            sessionEpoch=1,
            agentRunId="task-2",
            containerId="ctr-missing",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://missing",
            status="busy",
            workspacePath="/work/repo2",
            sessionWorkspacePath="/work/session2",
            artifactSpoolPath="/work/artifacts2",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = AsyncMock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "inspect", "-f"):
            container_id = command[-1]
            if container_id == "ctr-ok":
                return 0, "true\n", ""
            return 1, "", "No such container"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    reconciled = await controller.reconcile()

    assert sorted(record.session_id for record in reconciled) == ["sess-missing", "sess-ok"]
    session_supervisor.start.assert_awaited_once()
    assert store.load("sess-ok").status == "ready"
    degraded = store.load("sess-missing")
    assert degraded is not None
    assert degraded.status == "degraded"
    assert degraded.error_message == "managed session container is missing during reconcile"

@pytest.mark.asyncio
async def test_controller_reconcile_degrades_when_container_inspect_fails(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-ok",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-ok",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "inspect", "-f"):
            return 1, "", "docker daemon unavailable"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=_fake_runner,
    )

    reconciled = await controller.reconcile()

    assert [record.session_id for record in reconciled] == ["sess-ok"]
    degraded = store.load("sess-ok")
    assert degraded is not None
    assert degraded.status == "degraded"
    assert degraded.error_message == (
        "failed to inspect managed session container ctr-ok: "
        "docker daemon unavailable"
    )


@pytest.mark.asyncio
async def test_controller_reconcile_marks_terminal_owner_session_terminated(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-terminal-owner",
            sessionEpoch=1,
            agentRunId="task-terminal",
            containerId="ctr-terminal",
            threadId="thread-terminal",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://terminal",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _owner_workflow_status(workflow_id: str) -> str:
        assert workflow_id == "task-terminal"
        return "TERMINATED"

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"terminal owner should skip docker inspect: {command}")

    session_supervisor = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
        owner_workflow_status_resolver=_owner_workflow_status,
    )

    reconciled = await controller.reconcile()

    assert [record.session_id for record in reconciled] == ["sess-terminal-owner"]
    updated = store.load("sess-terminal-owner")
    assert updated is not None
    assert updated.status == "terminated"
    assert updated.active_turn_id is None
    assert updated.metadata["ownerWorkflowId"] == "task-terminal"
    assert updated.metadata["ownerWorkflowStatus"] == "TERMINATED"
    assert updated.metadata["terminationSource"] == "managed_session_reconcile"
    assert updated.error_message == (
        "managed session owner workflow is terminal during reconcile: TERMINATED"
    )
    session_supervisor.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_terminal_owner_lookup_handles_bad_status_value(
    tmp_path: Path,
) -> None:
    record = CodexManagedSessionRecord(
        sessionId="sess-bad-status",
        sessionEpoch=1,
        agentRunId="task-bad-status",
        containerId="ctr-bad-status",
        threadId="thread-bad-status",
        runtimeId="codex_cli",
        imageRef="img",
        controlUrl="docker-exec://bad-status",
        status="ready",
        workspacePath="/work/repo",
        sessionWorkspacePath="/work/session",
        artifactSpoolPath="/work/artifacts",
        startedAt="2026-04-06T12:00:00Z",
    )

    class _BadStatus:
        @property
        def name(self) -> str:
            raise RuntimeError("unexpected status shape")

    async def _owner_workflow_status(_workflow_id: str) -> object:
        return _BadStatus()

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=ManagedSessionStore(tmp_path / "session-store"),
        owner_workflow_status_resolver=_owner_workflow_status,
    )

    assert await controller._terminal_owner_workflow_status(record) is None


@pytest.mark.asyncio
async def test_controller_reconcile_termination_allows_nullable_record_metadata(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    record = CodexManagedSessionRecord(
        sessionId="sess-null-metadata",
        sessionEpoch=1,
        agentRunId="task-null-metadata",
        containerId="ctr-null-metadata",
        threadId="thread-null-metadata",
        runtimeId="codex_cli",
        imageRef="img",
        controlUrl="docker-exec://null-metadata",
        status="ready",
        workspacePath="/work/repo",
        sessionWorkspacePath="/work/session",
        artifactSpoolPath="/work/artifacts",
        startedAt="2026-04-06T12:00:00Z",
    )
    store.save(record)
    record.metadata = None

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
    )

    updated = await controller._mark_session_terminated_by_reconcile(
        record,
        reason="owner workflow completed",
        metadata={"ownerWorkflowStatus": "COMPLETED"},
    )

    assert updated.status == "terminated"
    assert updated.metadata["ownerWorkflowStatus"] == "COMPLETED"
    assert updated.metadata["terminationSource"] == "managed_session_reconcile"


def _reap_active_record(session_id: str) -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId="task-1",
        containerId=f"ctr-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="img",
        controlUrl=f"docker-exec://{session_id}",
        status="ready",
        workspacePath="/work/repo",
        sessionWorkspacePath="/work/session",
        artifactSpoolPath="/work/artifacts",
        startedAt="2026-04-06T12:00:00Z",
    )


def test_parse_docker_timestamp_handles_nano_zulu_and_zero_time() -> None:
    parsed = _parse_docker_timestamp("2026-06-17T05:20:54.273688912Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026 and parsed.month == 6 and parsed.minute == 20
    # Docker's zero-value timestamp is treated as unknown.
    assert _parse_docker_timestamp("0001-01-01T00:00:00Z") is None
    assert _parse_docker_timestamp("") is None
    assert _parse_docker_timestamp("not-a-time") is None


@pytest.mark.asyncio
async def test_controller_reaps_orphan_session_containers_and_skips_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS", "0")
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(_reap_active_record("sess-active"))

    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:2] == ("docker", "ps"):
            return 0, "c-active\nc-orphan-agent\nc-orphan-docker\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return (
                0,
                "c-active|sess-active|managed-session|2020-01-01T00:00:00Z\n"
                "c-orphan-agent|sess-orphan|managed-session|2020-01-01T00:00:00Z\n"
                "c-orphan-docker|sess-orphan|session-docker-sidecar"
                "|2020-01-01T00:00:00Z\n",
                "",
            )
        if command[:3] == ("docker", "rm", "-f"):
            if command[-1].startswith("moonmind-session-"):
                return 1, "", "No such container"
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert isinstance(result, ManagedSessionReapResult)
    assert result.scanned_containers == 3
    assert result.reaped_session_ids == ("sess-orphan",)
    assert result.reaped_containers == 2
    assert result.skipped_active == 1
    assert result.skipped_recent == 0
    removed = {cmd[-1] for cmd in commands if cmd[:3] == ("docker", "rm", "-f")}
    assert "c-orphan-agent" in removed
    assert "c-orphan-docker" in removed
    assert "c-active" not in removed
    # Sidecar volumes for the orphan are cleaned up.
    volume_removals = {
        cmd[-1] for cmd in commands if cmd[:4] == ("docker", "volume", "rm", "-f")
    }
    assert "moonmind-session-sess-orphan-docker-graph" in volume_removals
    assert "moonmind-session-sess-orphan-docker-socket" in volume_removals


@pytest.mark.asyncio
async def test_controller_reap_protects_terminal_record_until_owner_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    store = ManagedSessionStore(tmp_path / "session-store")
    running_owner = _reap_active_record("sess-running-owner")
    running_owner.agent_run_id = "workflow-running"
    running_owner.status = "failed"
    terminal_owner = _reap_active_record("sess-terminal-owner")
    terminal_owner.agent_run_id = "workflow-completed"
    terminal_owner.status = "failed"
    store.save(running_owner)
    store.save(terminal_owner)

    async def _owner_workflow_status(workflow_id: str) -> str:
        return {
            "workflow-running": "RUNNING",
            "workflow-completed": "COMPLETED",
        }[workflow_id]

    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:2] == ("docker", "ps"):
            return 0, "c-running\nc-completed\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return (
                0,
                "c-running|sess-running-owner|managed-session"
                "|2020-01-01T00:00:00Z\n"
                "c-completed|sess-terminal-owner|managed-session"
                "|2020-01-01T00:00:00Z\n",
                "",
            )
        if command[:3] == ("docker", "rm", "-f"):
            if command[-1].startswith("moonmind-session-"):
                return 1, "", "No such container"
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
        owner_workflow_status_resolver=_owner_workflow_status,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.reaped_session_ids == ("sess-terminal-owner",)
    assert result.reaped_containers == 1
    assert result.skipped_active == 1
    removed = {cmd[-1] for cmd in commands if cmd[:3] == ("docker", "rm", "-f")}
    assert "c-completed" in removed
    assert "c-running" not in removed


@pytest.mark.asyncio
async def test_controller_reap_protects_terminal_record_when_owner_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    store = ManagedSessionStore(tmp_path / "session-store")
    record = _reap_active_record("sess-lookup-failed")
    record.status = "failed"
    store.save(record)

    async def _owner_workflow_status(_workflow_id: str) -> str:
        raise RuntimeError("Temporal unavailable")

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:2] == ("docker", "ps"):
            return 0, "c-failed\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return (
                0,
                "c-failed|sess-lookup-failed|managed-session"
                "|2020-01-01T00:00:00Z\n",
                "",
            )
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"protected session must not be removed: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
        owner_workflow_status_resolver=_owner_workflow_status,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.skipped_active == 1
    assert result.reaped_containers == 0
    assert result.reaped_session_ids == ()


@pytest.mark.asyncio
async def test_controller_reap_limits_owner_lookups_to_sessions_with_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    store = ManagedSessionStore(tmp_path / "session-store")
    volume_owner = _reap_active_record("sess-volume-owner")
    volume_owner.agent_run_id = "workflow-volume-owner"
    volume_owner.status = "failed"
    retained_without_resources = _reap_active_record("sess-retained")
    retained_without_resources.agent_run_id = "workflow-retained"
    retained_without_resources.status = "failed"
    store.save(volume_owner)
    store.save(retained_without_resources)

    owner_lookups: list[str] = []

    async def _owner_workflow_status(workflow_id: str) -> str:
        owner_lookups.append(workflow_id)
        if workflow_id == "workflow-retained":
            raise AssertionError("resource-free records must not query Temporal")
        return "RUNNING"

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:2] == ("docker", "ps") and command != ("docker", "ps", "-q"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "moonmind-session-sess-volume-owner-docker-graph\n", ""
        if command[:3] == ("docker", "volume", "inspect"):
            return (
                0,
                json.dumps(
                    [
                        {
                            "Name": "moonmind-session-sess-volume-owner-docker-graph",
                            "CreatedAt": "2020-01-01T00:00:00Z",
                            "Labels": {
                                "moonmind.session_id": "sess-volume-owner",
                                "moonmind.volume_role": "docker-graph",
                                "moonmind.kind": "session-docker-sidecar-volume",
                            },
                        }
                    ]
                ),
                "",
            )
        if command == ("docker", "ps", "-q"):
            return 0, "", ""
        raise AssertionError(f"protected volume must not be removed: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
        owner_workflow_status_resolver=_owner_workflow_status,
    )

    result = await controller.reap_orphan_session_containers()

    assert owner_lookups == ["workflow-volume-owner"]
    assert result.scanned_containers == 0
    assert result.scanned_volumes == 1
    assert result.reaped_volumes == 0
    assert result.skipped_active_volumes == 1


@pytest.mark.asyncio
async def test_controller_reaps_stale_ready_session_after_max_age(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS", "3600")
    store = ManagedSessionStore(tmp_path / "session-store")
    old_started_at = "2020-01-01T00:00:00Z"
    recent = datetime.now(tz=UTC).isoformat()
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-stale",
            sessionEpoch=1,
            agentRunId="task-stale",
            containerId="ctr-sess-stale",
            threadId="thread-stale",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://stale",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt=old_started_at,
        )
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-recent",
            sessionEpoch=1,
            agentRunId="task-recent",
            containerId="ctr-sess-recent",
            threadId="thread-recent",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://recent",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt=recent,
        )
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-busy",
            sessionEpoch=1,
            agentRunId="task-busy",
            containerId="ctr-sess-busy",
            threadId="thread-busy",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://busy",
            status="busy",
            activeTurnId="turn-busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt=old_started_at,
        )
    )

    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:2] == ("docker", "ps"):
            return 0, "c-stale\nc-recent\nc-busy\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return (
                0,
                "c-stale|sess-stale|managed-session|2020-01-01T00:00:00Z\n"
                f"c-recent|sess-recent|managed-session|{recent}\n"
                "c-busy|sess-busy|managed-session|2020-01-01T00:00:00Z\n",
                "",
            )
        if command[:3] == ("docker", "rm", "-f"):
            if command[-1].startswith("moonmind-session-"):
                return 1, "", "No such container"
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.forced_stale == 1
    assert result.reaped_session_ids == ("sess-stale",)
    assert result.reaped_containers == 1
    assert result.skipped_active == 2
    removed = {cmd[-1] for cmd in commands if cmd[:3] == ("docker", "rm", "-f")}
    assert "c-stale" in removed
    assert "c-recent" not in removed
    assert "c-busy" not in removed
    stale_record = store.load("sess-stale")
    assert stale_record is not None
    assert stale_record.status == "terminated"
    assert stale_record.metadata["reapReason"] == "stale_active_session_max_age"
    assert store.load("sess-recent").status == "ready"
    assert store.load("sess-busy").status == "busy"


def test_controller_stale_ready_ids_skip_missing_age_and_accept_naive_age(
    tmp_path: Path,
) -> None:
    missing_age = _reap_active_record("sess-missing-age")
    missing_age.started_at = None
    missing_age.updated_at = None
    naive_age = _reap_active_record("sess-naive-age")
    naive_age.started_at = datetime(2020, 1, 1, 0, 0, 0)

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path / "agent_jobs"),
    )

    stale = controller._stale_active_session_ids(
        active_records={
            missing_age.session_id: missing_age,
            naive_age.session_id: naive_age,
        },
        by_session={},
        max_age_seconds=3600,
        now=datetime.now(tz=UTC),
    )

    assert stale == {"sess-naive-age"}


@pytest.mark.asyncio
async def test_controller_reap_continues_when_stale_session_termination_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS", "3600")
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(_reap_active_record("sess-fail"))
    store.save(_reap_active_record("sess-ok"))
    original_update = store.update

    async def _update(session_id: str, **kwargs: object) -> CodexManagedSessionRecord:
        if session_id == "sess-fail":
            raise RuntimeError("database unavailable")
        return await original_update(session_id, **kwargs)

    store.update = _update
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:2] == ("docker", "ps"):
            return 0, "c-fail\nc-ok\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return (
                0,
                "c-fail|sess-fail|managed-session|2020-01-01T00:00:00Z\n"
                "c-ok|sess-ok|managed-session|2020-01-01T00:00:00Z\n",
                "",
            )
        if command[:3] == ("docker", "rm", "-f"):
            if command[-1].startswith("moonmind-session-"):
                return 1, "", "No such container"
            return 0, "", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.forced_stale == 1
    assert result.reaped_session_ids == ("sess-ok",)
    assert result.reaped_containers == 1
    removed = {cmd[-1] for cmd in commands if cmd[:3] == ("docker", "rm", "-f")}
    assert "c-ok" in removed
    assert "c-fail" not in removed
    assert store.load("sess-fail").status == "ready"
    assert store.load("sess-ok").status == "terminated"


@pytest.mark.asyncio
async def test_controller_reap_skips_orphans_within_grace_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    store = ManagedSessionStore(tmp_path / "session-store")
    recent = datetime.now(tz=UTC).isoformat()

    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:2] == ("docker", "ps"):
            return 0, "c-new\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return 0, f"c-new|sess-new|managed-session|{recent}\n", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.skipped_recent == 1
    assert result.reaped_containers == 0
    assert result.reaped_session_ids == ()
    assert not any(cmd[:3] == ("docker", "rm", "-f") for cmd in commands)


@pytest.mark.asyncio
async def test_mm870_controller_reaps_orphan_sidecar_volumes_and_skips_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS", "0")
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(_reap_active_record("sess-active"))
    recent = datetime.now(tz=UTC).isoformat()
    volume_names = [
        "moonmind-session-sess-active-docker-graph",
        "moonmind-session-sess-mounted-docker-socket",
        "moonmind-session-sess-orphan-docker-graph",
        "moonmind-session-legacy-orphan-docker-socket",
        "moonmind-session-sess-recent-docker-graph",
    ]
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:4] == (
            "docker",
            "ps",
            "-aq",
            "--filter",
        ):
            return 0, "", ""
        if command[:4] == ("docker", "volume", "ls", "--format"):
            return 0, "\n".join([*volume_names, "unrelated-volume"]) + "\n", ""
        if command[:3] == ("docker", "volume", "inspect"):
            return (
                0,
                json.dumps(
                    [
                        {
                            "Name": "moonmind-session-sess-active-docker-graph",
                            "CreatedAt": "2020-01-01T00:00:00Z",
                            "Labels": {
                                "moonmind.session_id": "sess-active",
                                "moonmind.volume_role": "docker-graph",
                                "moonmind.kind": "session-docker-sidecar-volume",
                            },
                        },
                        {
                            "Name": "moonmind-session-sess-mounted-docker-socket",
                            "CreatedAt": "2020-01-01T00:00:00Z",
                            "Labels": {
                                "moonmind.session_id": "sess-mounted",
                                "moonmind.volume_role": "docker-socket",
                                "moonmind.kind": "session-docker-sidecar-volume",
                            },
                        },
                        {
                            "Name": "moonmind-session-sess-orphan-docker-graph",
                            "CreatedAt": "2020-01-01T00:00:00Z",
                            "Labels": {
                                "moonmind.session_id": "sess-orphan",
                                "moonmind.volume_role": "docker-graph",
                                "moonmind.kind": "session-docker-sidecar-volume",
                            },
                        },
                        {
                            "Name": "moonmind-session-legacy-orphan-docker-socket",
                            "CreatedAt": "2020-01-01T00:00:00Z",
                        },
                        {
                            "Name": "moonmind-session-sess-recent-docker-graph",
                            "CreatedAt": recent,
                            "Labels": {
                                "moonmind.session_id": "sess-recent",
                                "moonmind.volume_role": "docker-graph",
                                "moonmind.kind": "session-docker-sidecar-volume",
                            },
                        },
                    ]
                ),
                "",
            )
        if command == ("docker", "ps", "-q"):
            return 0, "running-1\n", ""
        if command[:3] == ("docker", "inspect", "--format"):
            return 0, "moonmind-session-sess-mounted-docker-socket\n", ""
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.scanned_containers == 0
    assert result.scanned_volumes == 5
    assert result.reaped_volumes == 2
    assert result.skipped_active_volumes == 2
    assert result.skipped_recent_volumes == 1
    removed = {
        cmd[-1] for cmd in commands if cmd[:4] == ("docker", "volume", "rm", "-f")
    }
    assert removed == {
        "moonmind-session-sess-orphan-docker-graph",
        "moonmind-session-legacy-orphan-docker-socket",
    }


@pytest.mark.asyncio
async def test_controller_reap_disabled_via_env_is_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", "0")
    store = ManagedSessionStore(tmp_path / "session-store")

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"reap must not call docker when disabled: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.disabled is True
    assert result.reaped_containers == 0


@pytest.mark.asyncio
async def test_controller_reap_without_store_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_REAP_ENABLED", raising=False)

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"reap must not call docker without a store: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=None,
        command_runner=_fake_runner,
    )

    result = await controller.reap_orphan_session_containers()

    assert result.disabled is True


@pytest.mark.asyncio
async def test_controller_session_status_persists_returned_session_identity(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "session_status" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-2",
                    "threadId": "thread-2",
                },
                "status": "ready",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    await controller.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
        )
    )

    updated = store.load("sess-1")
    assert updated is not None
    assert updated.session_epoch == 2
    assert updated.container_id == "ctr-2"
    assert updated.thread_id == "thread-2"

@pytest.mark.asyncio
async def test_controller_send_turn_skips_missing_durable_record(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-1",
                    "threadId": "thread-2",
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
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert store.load("sess-1") is None

@pytest.mark.asyncio
async def test_controller_send_turn_persists_failed_turn_status(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {"reason": "turn execution failed"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    updated = store.load("sess-1")
    assert response.status == "failed"
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error_message == "turn execution failed"

@pytest.mark.asyncio
async def test_controller_send_turn_bounds_persisted_last_assistant_text(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    session_supervisor = AsyncMock(spec=ManagedSessionSupervisor)
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "exec", "-i") and "invoke" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "completed",
                "metadata": {"assistantText": "x" * 12000},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Write a long report",
        )
    )

    updated = store.load("sess-1")
    assert response.status == "completed"
    assert updated is not None
    assert updated.metadata["lastAssistantTextTruncated"] is True
    assert updated.metadata["lastAssistantTextOriginalChars"] == 8190
    assert len(str(updated.metadata["lastAssistantText"]).encode("utf-8")) <= 4096
    emitted_kinds = [
        call.kwargs.get("kind")
        for call in session_supervisor.emit_session_event.call_args_list
    ]
    emitted_metadata = [
        call.kwargs.get("metadata")
        for call in session_supervisor.emit_session_event.call_args_list
    ]
    assert emitted_kinds == [
        "user_message_submitted",
        "turn_started",
        "assistant_message",
        "assistant_message_completed",
        "turn_completed",
    ]
    assert emitted_metadata[2]["contentLength"] == 8190
    assert emitted_metadata[3]["contentLength"] == 8190
    assert emitted_metadata[4]["assistantMessageLength"] == 8190
    assert all("assistantText" not in metadata for metadata in emitted_metadata)

@pytest.mark.asyncio
async def test_controller_interrupt_turn_preserves_failed_runtime_result(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            agentRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "interrupt_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {"reason": "turn-id mismatch"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.interrupt_turn(
        InterruptCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            turnId="vendor-turn-1",
        )
    )

    updated = store.load("sess-1")
    assert response.status == "failed"
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error_message == "turn-id mismatch"

@pytest.mark.asyncio
async def test_controller_launch_retries_ready_probe_errors(tmp_path: Path) -> None:
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
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
@pytest.mark.parametrize(
    "ready_stderr",
    [
        "Error response from daemon: container ctr-1 is not running",
        "Error response from daemon: No such container: ctr-1",
    ],
)
async def test_controller_launch_reports_terminal_container_logs_during_ready_probe(
    tmp_path: Path,
    ready_stderr: str,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
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
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 1, "", ready_stderr
        if command[:3] == ("docker", "logs", "--tail"):
            return (
                0,
                '{"error":"MOONMIND_SESSION_WORKSPACE_STATE_PATH must be writable",'
                '"ready":false}\n',
                "",
            )
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
        ready_poll_attempts=3,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await controller.launch_session(request)

    message = str(exc_info.value)
    assert "exited before ready" in message
    assert "MOONMIND_SESSION_WORKSPACE_STATE_PATH must be writable" in message
    assert ("docker", "logs", "--tail", "40", "ctr-1") in commands
    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")

@pytest.mark.asyncio
async def test_controller_launch_uses_mount_syntax_for_colon_scoped_paths(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        agentRunId="mm:c8a52a0a-66ec-4796-91a4-76568c17159a",
        sessionId="sess-mm-c8a52a0a-66ec-4796-91a4-76568c17159a-codex_cli",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "mm:c8a52a0a-66ec-4796-91a4-76568c17159a" / "repo"),
        sessionWorkspacePath=str(workspace_root / "mm:c8a52a0a-66ec-4796-91a4-76568c17159a" / "session"),
        artifactSpoolPath=str(workspace_root / "mm:c8a52a0a-66ec-4796-91a4-76568c17159a" / "artifacts"),
        codexHomePath=str(workspace_root / "mm:c8a52a0a-66ec-4796-91a4-76568c17159a" / ".moonmind" / "codex-home"),
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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-mm-c8a52a0a-66ec-4796-91a4-76568c17159a-codex_cli",
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

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert "-v" not in run_command
    assert "--user" in run_command
    assert "1000:1000" in run_command
    assert "--mount" in run_command
    assert not any(
        "type=volume,src=codex_auth_volume," in arg for arg in run_command
    )

@pytest.mark.asyncio
async def test_controller_launch_mounts_auth_volume_at_separate_managed_auth_path() -> None:
    workspace_root = Path("/tmp/agent_jobs")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath=str(workspace_root / "task-1" / ".moonmind" / "codex-home"),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={
            "MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth",
            "CODEX_HOME": "/home/app/.codex",
            "CODEX_CONFIG_HOME": "/home/app/.codex",
            "CODEX_CONFIG_PATH": "/home/app/.codex/config.toml",
        },
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
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    run_command = next(
        command for command in commands if command[:2] == ("docker", "run")
    )
    assert (
        "type=volume,src=codex_auth_volume,dst=/home/app/.codex-auth" in run_command
    )
    assert (
        f"type=volume,src=codex_auth_volume,dst={request.codex_home_path}"
        not in run_command
    )
    run_env = {
        value.split("=", 1)[0]: value.split("=", 1)[1]
        for index, value in enumerate(run_command)
        if index > 0 and run_command[index - 1] == "-e" and "=" in value
    }
    assert run_env["CODEX_HOME"] == request.codex_home_path
    assert run_env["CODEX_CONFIG_HOME"] == request.codex_home_path
    from pathlib import PurePosixPath

    assert run_env["CODEX_CONFIG_PATH"] == str(
        PurePosixPath(request.codex_home_path) / "config.toml"
    )


@pytest.mark.asyncio
async def test_controller_launch_normalizes_materialized_codex_home_for_container_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    codex_home_path = workspace_root / "task-1" / ".moonmind" / "codex-home"
    codex_home_path.mkdir(parents=True, exist_ok=True)
    config_path = codex_home_path / "config.toml"
    config_path.write_text("model = 'qwen/qwen3.6-plus'\n", encoding="utf-8")
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath=str(codex_home_path),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    chown_calls: list[tuple[Path, int, int, bool]] = []

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.geteuid",
        lambda: 0,
    )

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        chown_calls.append((Path(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller.os.chown",
        _fake_chown,
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del input_text, env
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
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

    await controller.launch_session(request)

    chowned_paths = {path for path, _uid, _gid, _follow_symlinks in chown_calls}
    assert codex_home_path.parent in chowned_paths
    assert codex_home_path in chowned_paths
    assert config_path in chowned_paths
    assert all(uid == 1000 and gid == 1000 for _path, uid, gid, _follow in chown_calls)

@pytest.mark.asyncio
async def test_controller_launch_cleans_up_container_when_handshake_fails() -> None:
    request = LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
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
    request = LaunchCodexManagedSessionRequest.model_construct(
        agent_run_id="task-1",
        workflow_id=None,
        session_id="sess-1",
        session_epoch=1,
        thread_id="logical-thread-1",
        workspace_path="/tmp/agent_jobs/task-1/repo",
        session_workspace_path="/tmp/agent_jobs/task-1/session",
        artifact_spool_path="/tmp/agent_jobs/task-1/artifacts",
        codex_home_path="/home/app/.codex",
        image_ref="ghcr.io/moonladderstudios/moonmind:latest",
        turn_completion_timeout_seconds=3600,
        environment={"MOONMIND_SESSION_WORKSPACE_PATH": "/tmp/override"},
        workspace_spec={},
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
@pytest.mark.asyncio
async def test_wait_for_turn_streams_typed_observations_before_terminal(
    tmp_path: Path,
) -> None:
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(tmp_path),
        turn_poll_interval_seconds=0,
    )
    responses = iter(
        [
            {
                "sessionState": {
                    "sessionId": "session-3418",
                    "sessionEpoch": 2,
                    "containerId": "container-1",
                    "threadId": "thread-2",
                    "activeTurnId": "turn-7",
                },
                "status": "busy",
                "metadata": {
                    "lastTurnId": "turn-7",
                    "lastTurnStatus": "running",
                    "observabilityEvents": [
                        {
                            "kind": "tool_call_started",
                            "turnId": "turn-7",
                            "metadata": {"sourceEventId": "event-1"},
                        }
                    ],
                },
            },
            {
                "sessionState": {
                    "sessionId": "session-3418",
                    "sessionEpoch": 2,
                    "containerId": "container-1",
                    "threadId": "thread-2",
                },
                "status": "ready",
                "metadata": {
                    "lastTurnId": "turn-7",
                    "lastTurnStatus": "completed",
                },
            },
        ]
    )

    async def invoke_json(**_kwargs: Any) -> dict[str, Any]:
        return next(responses)

    streamed: list[tuple[list[Any], str, CodexManagedSessionLocator]] = []

    async def sink(
        observations: list[Any],
        turn_id: str,
        locator: CodexManagedSessionLocator,
    ) -> None:
        streamed.append((observations, turn_id, locator))

    controller._invoke_json = invoke_json
    request = SendCodexManagedSessionTurnRequest(
        sessionId="session-3418",
        sessionEpoch=2,
        containerId="container-1",
        threadId="thread-2",
        instructions="work",
    )
    initial = CodexManagedSessionTurnResponse(
        sessionState={
            "sessionId": "session-3418",
            "sessionEpoch": 2,
            "containerId": "container-1",
            "threadId": "thread-2",
            "activeTurnId": "turn-7",
        },
        turnId="turn-7",
        status="running",
    )

    result = await controller._wait_for_terminal_turn_response(
        request=request,
        initial_response=initial,
        observation_sink=sink,
    )

    assert result.status == "completed"
    assert streamed[0][0][0]["metadata"]["sourceEventId"] == "event-1"
    assert streamed[0][1] == "turn-7"
    assert streamed[0][2].session_epoch == 2


def test_active_session_observations_merges_authoritative_intervention_journal() -> None:
    authority = {
        "sourceEventId": "control-1",
        "actorId": "operator-1",
        "idempotencyKey": "request-1",
        "expectedSessionId": "session-3418",
        "expectedSessionEpoch": 2,
        "expectedTurnId": "turn-7",
        "outcome": "completed",
        "auditRef": "artifact://interventions/request-1",
    }
    observations = DockerCodexManagedSessionController._active_session_observations(
        {
            "observabilityEvents": [
                {"kind": "tool_call_started", "metadata": {"sourceEventId": "tool-1"}},
                {"kind": "intervention_completed", "metadata": authority},
            ],
            "interventionJournal": [
                {
                    "kind": "intervention_accepted",
                    "metadata": {**authority, "outcome": "accepted"},
                },
                {"kind": "intervention_completed", "metadata": authority},
                {
                    "kind": "approval_requested",
                    "metadata": {**authority, "sourceEventId": "approval-1", "outcome": "requested"},
                },
            ],
        }
    )

    assert [item["kind"] for item in observations] == [
        "tool_call_started",
        "intervention_completed",
        "intervention_accepted",
        "approval_requested",
    ]
    assert observations[-1]["metadata"]["auditRef"] == "artifact://interventions/request-1"
