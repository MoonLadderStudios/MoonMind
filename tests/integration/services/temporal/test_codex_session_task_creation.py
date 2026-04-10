from __future__ import annotations

import asyncio
import json
import runpy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.managed_session_models import LaunchCodexManagedSessionRequest
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
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
        assert body["payload"]["task"]["skill"]["name"] == "pr-resolver"
        assert body["payload"]["task"]["inputs"]["pr"] == "1337"
    finally:
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        server_thread.join(timeout=2)
