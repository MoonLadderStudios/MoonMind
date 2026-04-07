"""Docker-backed controller for transitional Codex managed sessions."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)


_RUNTIME_MODULE = "moonmind.workflows.temporal.runtime.codex_session_runtime"
_CONTAINER_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_.-]+")

CommandRunner = Callable[
    [tuple[str, ...]],
    Awaitable[tuple[int, str, str]],
]


async def _default_command_runner(
    command: tuple[str, ...],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )
    stdout, stderr = await process.communicate(
        input_text.encode("utf-8") if input_text is not None else None
    )
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class DockerCodexManagedSessionController:
    """Launch and control managed Codex session containers via Docker CLI."""

    def __init__(
        self,
        *,
        workspace_volume_name: str,
        codex_volume_name: str,
        workspace_root: str,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        ready_poll_interval_seconds: float = 1.0,
        ready_poll_attempts: int = 30,
        command_runner: Callable[
            [tuple[str, ...]],
            Awaitable[tuple[int, str, str]],
        ] = _default_command_runner,
    ) -> None:
        self._workspace_volume_name = workspace_volume_name
        self._codex_volume_name = codex_volume_name
        self._workspace_root = workspace_root
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._ready_poll_interval_seconds = ready_poll_interval_seconds
        self._ready_poll_attempts = ready_poll_attempts
        self._command_runner = command_runner

    def _docker_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return env

    def _container_name(self, session_id: str) -> str:
        sanitized = _CONTAINER_NAME_SANITIZER.sub("-", session_id).strip("-")
        if not sanitized:
            sanitized = "managed-session"
        return f"mm-codex-session-{sanitized}"

    async def _run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        env = self._docker_env()
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            input_text=input_text,
            env=env,
        )
        if returncode != 0:
            raise RuntimeError(
                f"{' '.join(command)} failed with exit code {returncode}: {stderr.strip() or stdout.strip()}"
            )
        return stdout, stderr

    async def _invoke_json(
        self,
        *,
        container_id: str,
        action: str,
        payload: Mapping[str, Any],
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        command = [
            self._docker_binary,
            "exec",
            "-i",
        ]
        if extra_env:
            for key, value in extra_env.items():
                command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                container_id,
                "python3",
                "-m",
                _RUNTIME_MODULE,
                "invoke",
                action,
            ]
        )
        stdout, _stderr = await self._run(
            command,
            input_text=json.dumps(payload),
        )
        return json.loads(stdout.strip() or "{}")

    async def _wait_ready(self, *, container_id: str) -> None:
        command = (
            self._docker_binary,
            "exec",
            container_id,
            "python3",
            "-m",
            _RUNTIME_MODULE,
            "ready",
        )
        for attempt in range(self._ready_poll_attempts):
            stdout, _stderr = await self._run(command)
            payload = json.loads(stdout.strip() or "{}")
            if payload.get("ready") is True:
                return
            if self._ready_poll_interval_seconds > 0:
                await asyncio.sleep(self._ready_poll_interval_seconds)
        raise RuntimeError(
            f"managed session container {container_id} did not become ready"
        )

    async def launch_session(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        container_name = self._container_name(request.session_id)
        run_command = [
            self._docker_binary,
            "run",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{self._workspace_volume_name}:{self._workspace_root}",
            "-v",
            f"{self._codex_volume_name}:{request.codex_home_path}",
            "-e",
            f"MOONMIND_SESSION_WORKSPACE_PATH={request.workspace_path}",
            "-e",
            f"MOONMIND_SESSION_WORKSPACE_STATE_PATH={request.session_workspace_path}",
            "-e",
            f"MOONMIND_SESSION_ARTIFACT_SPOOL_PATH={request.artifact_spool_path}",
            "-e",
            f"MOONMIND_SESSION_CODEX_HOME_PATH={request.codex_home_path}",
            "-e",
            f"MOONMIND_SESSION_IMAGE_REF={request.image_ref}",
            "-e",
            f"MOONMIND_SESSION_CONTROL_URL=docker-exec://{container_name}",
        ]
        for key, value in sorted(request.environment.items()):
            run_command.extend(["-e", f"{key}={value}"])
        run_command.extend(
            [
                request.image_ref,
                "python3",
                "-m",
                _RUNTIME_MODULE,
                "serve",
            ]
        )
        stdout, _stderr = await self._run(run_command)
        container_id = stdout.strip()
        if not container_id:
            raise RuntimeError("docker run returned a blank container id")
        await self._wait_ready(container_id=container_id)
        payload = await self._invoke_json(
            container_id=container_id,
            action="launch_session",
            payload=request.model_dump(by_alias=True),
            extra_env={"MOONMIND_SESSION_CONTAINER_ID": container_id},
        )
        return CodexManagedSessionHandle.model_validate(payload)

    async def session_status(
        self,
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="session_status",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionHandle.model_validate(payload)

    async def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="send_turn",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionTurnResponse.model_validate(payload)

    async def steer_turn(
        self,
        request: SteerCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="steer_turn",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionTurnResponse.model_validate(payload)

    async def interrupt_turn(
        self,
        request: InterruptCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="interrupt_turn",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionTurnResponse.model_validate(payload)

    async def clear_session(
        self,
        request: CodexManagedSessionClearRequest,
    ) -> CodexManagedSessionHandle:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="clear_session",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionHandle.model_validate(payload)

    async def terminate_session(
        self,
        request: TerminateCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        await self._run((self._docker_binary, "rm", "-f", request.container_id))
        return CodexManagedSessionHandle(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
                "activeTurnId": None,
            },
            status="terminated",
        )

    async def fetch_session_summary(
        self,
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="fetch_session_summary",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionSummary.model_validate(payload)

    async def publish_session_artifacts(
        self,
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="publish_session_artifacts",
            payload=request.model_dump(by_alias=True),
        )
        return CodexManagedSessionArtifactsPublication.model_validate(payload)
