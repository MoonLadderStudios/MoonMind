from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.managed_session_models import LaunchCodexManagedSessionRequest
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
    _MANAGED_SESSION_CONTAINER_GID,
    _MANAGED_SESSION_CONTAINER_UID,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


class _CreateTaskHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(body.decode("utf-8")),
            }
        )
        payload = json.dumps({"taskId": "mm:child-task-1", "status": "queued"}).encode(
            "utf-8"
        )
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _load_batch_pr_resolver_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[4]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-pr-resolver"
            / "bin"
            / "batch_pr_resolver.py"
        )
    )


def _run_command_env(command: tuple[str, ...]) -> dict[str, str]:
    env: dict[str, str] = {}
    index = 0
    while index < len(command):
        if command[index] == "-e" and index + 1 < len(command):
            assignment = command[index + 1]
            if "=" in assignment:
                key, value = assignment.split("=", 1)
                env[key] = value
            index += 2
            continue
        index += 1
    return env


async def _run_checked(
    *command: str,
    cwd: Path | None = None,
    run_as_uid: int | None = None,
    run_as_gid: int | None = None,
) -> None:
    kwargs: dict[str, Any] = {}
    if os.name == "posix" and os.geteuid() == 0:
        if run_as_uid is not None or run_as_gid is not None:
            kwargs["extra_groups"] = []
        if run_as_uid is not None:
            kwargs["user"] = run_as_uid
        if run_as_gid is not None:
            kwargs["group"] = run_as_gid
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise AssertionError(
            f"{' '.join(command)} failed with exit code {process.returncode}: "
            f"{stderr.decode(errors='replace') or stdout.decode(errors='replace')}"
        )


def _make_world_readable(path: Path) -> None:
    directory_bits = (
        stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
        | stat.S_IRUSR
        | stat.S_IRGRP
        | stat.S_IROTH
    )
    file_bits = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    for root, dirnames, filenames in os.walk(path):
        root_path = Path(root)
        root_path.chmod(root_path.stat().st_mode | directory_bits)
        for dirname in dirnames:
            child = root_path / dirname
            child.chmod(child.stat().st_mode | directory_bits)
        for filename in filenames:
            child = root_path / filename
            child.chmod(child.stat().st_mode | file_bits)


def _make_tmp_path_accessible(path: Path) -> None:
    temp_root = Path("/tmp").resolve()
    for directory in (path, *path.parents):
        resolved = directory.resolve()
        if resolved == resolved.parent:
            break
        if temp_root not in (resolved, *resolved.parents):
            break
        directory.chmod(
            directory.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
            | stat.S_IRUSR
            | stat.S_IRGRP
            | stat.S_IROTH
        )


def _chown_tree(path: Path, *, uid: int, gid: int) -> None:
    for root, dirnames, filenames in os.walk(path):
        root_path = Path(root)
        os.chown(root_path, uid, gid)
        for dirname in dirnames:
            os.chown(root_path / dirname, uid, gid)
        for filename in filenames:
            os.chown(root_path / filename, uid, gid)


async def _create_source_repo(path: Path) -> None:
    path.mkdir(parents=True)
    await _run_checked("git", "init", "--initial-branch", "main", cwd=path)
    await _run_checked("git", "config", "user.name", "MoonMind Test", cwd=path)
    await _run_checked(
        "git",
        "config",
        "user.email",
        "moonmind-test@example.invalid",
        cwd=path,
    )
    (path / "README.md").write_text("source\n", encoding="utf-8")
    await _run_checked("git", "add", "README.md", cwd=path)
    await _run_checked("git", "commit", "-m", "Initial commit", cwd=path)
    _make_world_readable(path)


def _expected_child_idempotency_key(
    *,
    batch_scope: str,
    repo: str,
    pr_number: int | str,
    branch: str,
) -> str:
    canonical = json.dumps(
        {
            "scope": batch_scope,
            "repo": repo,
            "pr": str(pr_number),
            "branch": branch,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"batch-pr-resolver:pr:{pr_number}:sha256:{digest}"


@pytest.mark.asyncio
async def test_codex_session_launch_environment_can_create_child_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _CreateTaskHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CreateTaskHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    moonmind_url = f"http://127.0.0.1:{server.server_port}"

    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-parent",
        sessionId="sess-parent:codex_cli",
        threadId="thread-parent",
        workspacePath=str(workspace_root / "task-parent" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-parent" / "session"),
        artifactSpoolPath=str(workspace_root / "task-parent" / "artifacts"),
        codexHomePath=str(workspace_root / "task-parent" / ".moonmind" / "codex-home"),
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
            return 0, "ctr-session-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-session-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://ctr-session-1",
                "metadata": {"vendorThreadId": "vendor-thread-1"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        network_name="local-network",
        moonmind_url=moonmind_url,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    try:
        await controller.launch_session(request)
        run_command = next(
            command for command in commands if command[:2] == ("docker", "run")
        )
        run_env = _run_command_env(run_command)

        assert ("--network", "local-network") == (
            run_command[run_command.index("--network")],
            run_command[run_command.index("--network") + 1],
        )
        assert run_env["MOONMIND_URL"] == moonmind_url

        module = _load_batch_pr_resolver_module()
        JobSubmission = module["JobSubmission"]
        RuntimeSelection = module["RuntimeSelection"]
        build_queue_request = module["_build_queue_request"]
        submit_jobs = module["_submit_jobs"]
        submission = JobSubmission(
            queue_request=build_queue_request(
                "MoonLadderStudios/MoonMind",
                pr_number=1337,
                branch="codex/session-child-task",
                runtime=RuntimeSelection(
                    mode="codex_cli",
                    model="gpt-5.4",
                    effort="high",
                    provider_profile="codex_default",
                ),
                merge_method="squash",
                max_iterations=3,
                priority=0,
                max_attempts=3,
                batch_scope="task-parent",
            ),
            pr_number=1337,
            branch="codex/session-child-task",
        )

        monkeypatch.setenv("MOONMIND_URL", run_env["MOONMIND_URL"])
        monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
        monkeypatch.delenv("MOONMIND_WORKER_TOKEN_FILE", raising=False)

        created, errors = await submit_jobs([submission])

        assert errors == []
        assert created == [
            {
                "pr": 1337,
                "branch": "codex/session-child-task",
                "jobId": "mm:child-task-1",
            }
        ]
        assert len(_CreateTaskHandler.requests) == 1
        captured = _CreateTaskHandler.requests[0]
        assert captured["path"] == "/api/executions"
        body = captured["body"]
        assert body["type"] == "task"
        assert body["payload"]["targetRuntime"] == "codex_cli"
        assert body["payload"]["idempotencyKey"] == _expected_child_idempotency_key(
            batch_scope="task-parent",
            repo="MoonLadderStudios/MoonMind",
            pr_number=1337,
            branch="codex/session-child-task",
        )
        assert body["payload"]["task"]["skill"]["name"] == "pr-resolver"
        assert body["payload"]["task"]["inputs"]["pr"] == "1337"
    finally:
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        server_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_codex_session_launch_command_uses_workspace_and_explicit_auth_target(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-parent",
        sessionId="sess-parent:codex_cli",
        threadId="thread-parent",
        workspacePath=str(workspace_root / "task-parent" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-parent" / "session"),
        artifactSpoolPath=str(workspace_root / "task-parent" / "artifacts"),
        codexHomePath=str(workspace_root / "task-parent" / ".moonmind" / "codex-home"),
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
        del input_text, env
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-session-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "ctr-session-1",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://ctr-session-1",
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
    run_env = _run_command_env(run_command)

    assert f"type=volume,src=agent_workspaces,dst={workspace_root}" in run_command
    assert "type=volume,src=codex_auth_volume,dst=/home/app/.codex-auth" in run_command
    assert (
        f"type=volume,src=codex_auth_volume,dst={request.codex_home_path}"
        not in run_command
    )
    assert run_env["MOONMIND_SESSION_WORKSPACE_PATH"] == request.workspace_path
    assert (
        run_env["MOONMIND_SESSION_WORKSPACE_STATE_PATH"]
        == request.session_workspace_path
    )
    assert (
        run_env["MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"]
        == request.artifact_spool_path
    )
    assert run_env["MOONMIND_SESSION_CODEX_HOME_PATH"] == request.codex_home_path


@pytest.mark.asyncio
async def test_codex_session_workspace_git_metadata_is_managed_user_writable(
    tmp_path: Path,
) -> None:
    if os.name != "posix" or os.geteuid() != 0:
        pytest.skip("managed-session UID/GID permission integration requires root")

    _make_tmp_path_accessible(tmp_path)
    source_repo = tmp_path / "source-repo"
    await _create_source_repo(source_repo)
    _chown_tree(
        source_repo,
        uid=_MANAGED_SESSION_CONTAINER_UID,
        gid=_MANAGED_SESSION_CONTAINER_GID,
    )

    workspace_root = tmp_path / "agent_jobs"
    workspace_root.mkdir()
    workspace_root.chmod(0o755)
    target_branch = "feature/owned-ref"
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-parent",
        sessionId="sess-parent:codex_cli",
        threadId="thread-parent",
        workspacePath=str(workspace_root / "task-parent" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-parent" / "session"),
        artifactSpoolPath=str(workspace_root / "task-parent" / "artifacts"),
        codexHomePath=str(workspace_root / "task-parent" / ".moonmind" / "codex-home"),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        workspaceSpec={
            "repository": str(source_repo),
            "startingBranch": "main",
            "targetBranch": target_branch,
        },
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        ready_poll_interval_seconds=0,
    )

    await controller._ensure_workspace_paths(request)

    workspace_path = Path(request.workspace_path)
    branch_ref = workspace_path / ".git" / "refs" / "heads" / "feature" / "owned-ref"
    branch_log = workspace_path / ".git" / "logs" / "refs" / "heads" / "feature" / "owned-ref"
    assert branch_ref.exists()
    assert branch_log.exists()
    assert (branch_ref.stat().st_uid, branch_ref.stat().st_gid) == (
        _MANAGED_SESSION_CONTAINER_UID,
        _MANAGED_SESSION_CONTAINER_GID,
    )
    assert (branch_log.stat().st_uid, branch_log.stat().st_gid) == (
        _MANAGED_SESSION_CONTAINER_UID,
        _MANAGED_SESSION_CONTAINER_GID,
    )

    await _run_checked(
        "bash",
        "-lc",
        (
            "printf 'managed update\\n' >> README.md && "
            "git add README.md && "
            "git -c user.name='MoonMind Test' "
            "-c user.email='moonmind-test@example.invalid' "
            "commit -m 'Managed user commit'"
        ),
        cwd=workspace_path,
        run_as_uid=_MANAGED_SESSION_CONTAINER_UID,
        run_as_gid=_MANAGED_SESSION_CONTAINER_GID,
    )
