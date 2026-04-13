from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
    _default_command_runner,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer


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
    monkeypatch.setenv("MOONMIND_URL", "http://api:5000")
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
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
                "metadata": {"vendorThreadId": "vendor-thread-1"},
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

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.container_id == "ctr-1"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"
    assert commands[0] == ("docker", "rm", "-f", "mm-codex-session-sess-1")
    run_command = commands[1]
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
    assert "python3" in run_command
    assert "moonmind.workflows.temporal.runtime.codex_session_runtime" in run_command
    stored = session_store.load("sess-1")
    assert stored is not None
    assert stored.task_run_id == "task-1"
    assert stored.container_id == "ctr-1"
    assert stored.runtime_id == "codex_cli"
    session_supervisor.start.assert_awaited_once()


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
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_URL": "http://api:5000"},
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

    run_command = commands[1]
    assert "--network" in run_command
    assert "local-network" in run_command


@pytest.mark.asyncio
async def test_controller_replaces_blank_request_moonmind_url(
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
        moonmind_url="http://api:5000",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    await controller.launch_session(request)

    run_command = commands[1]
    assert "MOONMIND_URL=http://api:5000" in run_command


@pytest.mark.asyncio
async def test_controller_launch_normalizes_created_paths_for_container_user(
    monkeypatch: pytest.MonkeyPatch,
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
    assert all(follow_symlinks is False for _path, _uid, _gid, follow_symlinks in chown_calls)
    assert commands[1][:2] == ("docker", "run")


@pytest.mark.asyncio
async def test_controller_launch_clones_workspace_before_starting_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="mm:task-1",
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
            "codex/session-fix",
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

    assert commands[0][:2] == ("git", "clone")
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
        "codex/session-fix",
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
async def test_controller_launch_redacts_github_token_from_command_failures(
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
            return (
                1,
                "",
                "docker run rejected GITHUB_TOKEN=ghp_inline_secret_token_12345678901234567890",
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

    message = str(exc_info.value)
    assert "ghp_inline_secret_token_12345678901234567890" not in message
    assert "[REDACTED]" in message


@pytest.mark.asyncio
async def test_controller_launch_creates_target_branch_when_remote_branch_missing(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="mm:task-1",
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
            "codex/session-fix",
        ):
            return 128, "", "fatal: couldn't find remote ref codex/session-fix"
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
        "codex/session-fix",
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
        taskRunId="mm:task-1",
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
        taskRunId="mm:task-1",
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
async def test_controller_launch_reclones_invalid_workspace_before_target_checkout(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / "mm:task-1" / "repo"
    workspace_path.mkdir(parents=True, exist_ok=True)
    stale_file = workspace_path / "stale.txt"
    stale_file.write_text("stale")
    request = LaunchCodexManagedSessionRequest(
        taskRunId="mm:task-1",
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
        taskRunId="mm:task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            [f"stdout={json.dumps('invalid\njson')}", f"stderr: {json.dumps('stderr\nline')}"],
            ["invalid\njson", "stderr\nline"],
        ),
        (
            "[1, 2, 3]",
            "stderr\nline",
            "returned a list payload instead of a JSON object",
            [
                f"stdout={json.dumps('[1, 2, 3]')}",
                f"stderr: {json.dumps('stderr\nline')}",
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
            taskRunId="task-1",
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
    assert emitted_kinds == ["turn_started", "turn_completed"]
    assert emitted_metadata == [
        {"action": "send_turn", "reason": "Operator follow-up"},
        {
            "action": "send_turn",
            "assistantText": "OK",
            "reason": "Operator follow-up",
        },
    ]


@pytest.mark.asyncio
async def test_controller_session_status_emits_session_resumed_event(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
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

    emitted_call = session_supervisor.emit_session_event.call_args
    assert emitted_call.kwargs["kind"] == "session_resumed"
    assert emitted_call.kwargs["active_turn_id"] == "turn-fresh"
    assert emitted_call.kwargs["metadata"] == {"action": "resume_session"}
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
async def test_controller_duplicate_terminate_rejects_stale_locator(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            taskRunId="task-1",
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
        taskRunId="task-1",
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
            taskRunId="task-1",
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
        if command[:3] == ("docker", "inspect", "-f"):
            return 0, "ctr-1\n", ""
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
    assert commands == [("docker", "inspect", "-f", "{{.Id}}", "ctr-1")]


def test_controller_launch_duplicate_match_requires_epoch_and_thread(
    tmp_path: Path,
) -> None:
    base_request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
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
        taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
        )
    )

    assert summary.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert summary.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert publication.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"


@pytest.mark.asyncio
async def test_controller_send_turn_tolerates_event_publication_failure(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
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
async def test_controller_clear_session_rejects_stale_durable_locator(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
        taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-1",
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
            taskRunId="task-2",
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
            taskRunId="task-1",
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
async def test_controller_session_status_persists_returned_session_identity(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
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
            taskRunId="task-1",
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
async def test_controller_interrupt_turn_preserves_failed_runtime_result(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
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
        taskRunId="mm:c8a52a0a-66ec-4796-91a4-76568c17159a",
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

    run_command = commands[1]
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
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath=str(workspace_root / "task-1" / ".moonmind" / "codex-home"),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
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

    run_command = commands[1]
    assert (
        "type=volume,src=codex_auth_volume,dst=/home/app/.codex-auth" in run_command
    )
    assert (
        f"type=volume,src=codex_auth_volume,dst={request.codex_home_path}"
        not in run_command
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
        taskRunId="task-1",
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
