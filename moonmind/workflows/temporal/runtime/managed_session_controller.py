"""Docker-backed controller for transitional Codex managed sessions."""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    ManagedSessionRecordStatus,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)

from .managed_session_store import ManagedSessionStore
from .managed_session_supervisor import ManagedSessionSupervisor


_RUNTIME_MODULE = "moonmind.workflows.temporal.runtime.codex_session_runtime"
_CONTAINER_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_.-]+")
_RESERVED_SESSION_ENV_PREFIX = "MOONMIND_SESSION_"
_SEND_TURN_INLINE_SCRIPT = """
import json
import importlib
from pathlib import Path
import sys
import time
from collections.abc import Mapping

runtime_mod = importlib.import_module(
    "moonmind.workflows.temporal.runtime.codex_session_runtime"
)


request = runtime_mod.SendCodexManagedSessionTurnRequest.model_validate(
    json.loads(sys.stdin.read() or "{}")
)
runtime = runtime_mod._runtime_from_environment()
try:
    state = runtime._validate_locator(request)
    client = runtime._app_server_client()
    client._initialize_result = client.request(
        "initialize",
        {
            "clientInfo": {
                "name": "MoonMind",
                "version": "phase4",
            },
            "capabilities": {
                "experimentalApi": True,
            },
        },
    )

    sessions_root = Path(runtime._codex_home_path) / "sessions"
    thread_path = None
    if sessions_root.is_dir():
        matches = sorted(sessions_root.rglob(f"*{state.vendor_thread_id}.jsonl"))
        if matches:
            thread_path = str(matches[-1])
    resume_params = {"threadId": state.vendor_thread_id}
    if thread_path:
        resume_params["path"] = thread_path
    try:
        resumed = client.request("thread/resume", resume_params)
        resumed_thread = resumed.get("thread")
    except RuntimeError as exc:
        message = str(exc)
        if "no rollout found" not in message and "thread not found" not in message:
            raise
        started_thread = client.request(
            "thread/start",
            {"cwd": str(runtime._workspace_path)},
        )
        resumed_thread = started_thread.get("thread")
    if not isinstance(resumed_thread, Mapping):
        raise RuntimeError("codex app-server thread/resume did not return a thread")
    vendor_thread_id = str(resumed_thread.get("id") or "").strip()
    if not vendor_thread_id:
        raise RuntimeError("codex app-server thread/resume returned a blank thread id")
    state.vendor_thread_id = vendor_thread_id

    started = client.request(
        "turn/start",
        {
            "threadId": vendor_thread_id,
            "input": [{"type": "text", "text": request.instructions}],
        },
    )
    turn_payload = started.get("turn")
    if not isinstance(turn_payload, Mapping):
        raise RuntimeError("codex app-server turn/start did not return a turn")
    vendor_turn_id = str(turn_payload.get("id") or "").strip()
    if not vendor_turn_id:
        raise RuntimeError("codex app-server turn/start returned a blank turn id")

    state.active_turn_id = vendor_turn_id
    state.last_control_action = "send_turn"
    state.last_control_at = time.time()
    runtime._save_state(state)
    runtime._append_spool("stdout", f"turn started: {vendor_turn_id}\\n")

    try:
        client.wait_for_notification(
            "turn/completed",
            predicate=lambda message: (
                isinstance(message.get("params"), Mapping)
                and message["params"].get("threadId") == vendor_thread_id
            ),
            timeout_seconds=runtime._turn_completion_timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(
            "timed out waiting for codex app-server turn/completed notification "
            f"after {runtime._turn_completion_timeout_seconds} seconds"
        ) from exc

    thread_payload = client.request("thread/read", {"threadId": vendor_thread_id})
    assistant_text = runtime._extract_assistant_text(thread_payload)

    state.active_turn_id = None
    state.last_assistant_text = assistant_text or None
    runtime._save_state(state)
    if assistant_text:
        runtime._append_spool("stdout", f"assistant: {assistant_text}\\n")

    response = runtime_mod.CodexManagedSessionTurnResponse(
        sessionState=runtime._session_state(state),
        turnId=vendor_turn_id,
        status="completed",
        metadata={"assistantText": assistant_text},
    )
    sys.stdout.write(response.model_dump_json(by_alias=True, exclude_none=True) + "\\n")
    sys.stdout.flush()
finally:
    runtime.close()
""".strip()


class CommandRunner(Protocol):
    async def __call__(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        pass


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


def _normalize_absolute_posix_path(value: str, *, field_name: str) -> PurePosixPath:
    normalized = PurePosixPath(posixpath.normpath(value))
    if not normalized.is_absolute():
        raise RuntimeError(f"{field_name} must be an absolute path: {value}")
    return normalized


class DockerCodexManagedSessionController:
    """Launch and control managed Codex session containers via Docker CLI."""

    def __init__(
        self,
        *,
        workspace_volume_name: str,
        codex_volume_name: str,
        workspace_root: str,
        session_store: ManagedSessionStore | None = None,
        session_supervisor: ManagedSessionSupervisor | Any | None = None,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        ready_poll_interval_seconds: float = 1.0,
        ready_poll_attempts: int = 30,
        command_runner: CommandRunner = _default_command_runner,
    ) -> None:
        self._workspace_volume_name = workspace_volume_name
        self._codex_volume_name = codex_volume_name
        self._workspace_root = workspace_root
        self._session_store = session_store
        self._session_supervisor = session_supervisor
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

    def _validate_workspace_path(self, value: str, *, field_name: str) -> None:
        workspace_root = _normalize_absolute_posix_path(
            self._workspace_root,
            field_name="workspace_root",
        )
        candidate = _normalize_absolute_posix_path(value, field_name=field_name)
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise RuntimeError(
                f"{field_name} must stay within workspace_root {workspace_root}: {candidate}"
            ) from exc

    def _validate_launch_request(self, request: LaunchCodexManagedSessionRequest) -> None:
        self._validate_workspace_path(request.workspace_path, field_name="workspacePath")
        self._validate_workspace_path(
            request.session_workspace_path,
            field_name="sessionWorkspacePath",
        )
        self._validate_workspace_path(
            request.artifact_spool_path,
            field_name="artifactSpoolPath",
        )
        _normalize_absolute_posix_path(
            request.codex_home_path,
            field_name="codexHomePath",
        )
        reserved_keys = sorted(
            key
            for key in request.environment
            if key.startswith(_RESERVED_SESSION_ENV_PREFIX)
        )
        if reserved_keys:
            raise RuntimeError(
                "launch_session environment cannot override reserved session keys: "
                + ", ".join(reserved_keys)
            )

    @staticmethod
    def _volume_mount(volume_name: str, target_path: str) -> str:
        return f"type=volume,src={volume_name},dst={target_path}"

    @staticmethod
    def _record_status_from_handle_status(status: str) -> ManagedSessionRecordStatus:
        normalized = str(status or "").strip().lower()
        if normalized in {"launching", "ready", "busy", "terminating", "terminated", "failed"}:
            return normalized
        if normalized in {"clearing", "interrupted"}:
            return "ready"
        return "ready"

    @staticmethod
    def _record_status_from_turn_status(status: str) -> ManagedSessionRecordStatus:
        normalized = str(status or "").strip().lower()
        if normalized in {"accepted", "running"}:
            return "busy"
        if normalized in {"completed", "interrupted"}:
            return "ready"
        if normalized == "failed":
            return "failed"
        return "busy"

    @staticmethod
    def _turn_error_message(response: CodexManagedSessionTurnResponse) -> str | None:
        if response.status != "failed":
            return None
        for key in ("errorMessage", "reason", "error"):
            value = response.metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _locator_from_session_state(
        session_state,
    ) -> CodexManagedSessionLocator:
        return CodexManagedSessionLocator(
            sessionId=session_state.session_id,
            sessionEpoch=session_state.session_epoch,
            containerId=session_state.container_id,
            threadId=session_state.thread_id,
        )

    def _record_from_launch(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        handle: CodexManagedSessionHandle,
    ) -> CodexManagedSessionRecord:
        now = datetime.now(tz=UTC)
        return CodexManagedSessionRecord(
            sessionId=request.session_id,
            sessionEpoch=handle.session_state.session_epoch,
            taskRunId=request.task_run_id,
            containerId=handle.session_state.container_id,
            threadId=handle.session_state.thread_id,
            activeTurnId=handle.session_state.active_turn_id,
            runtimeId="codex_cli",
            imageRef=handle.image_ref or request.image_ref,
            controlUrl=handle.control_url or f"docker-exec://{handle.session_state.container_id}",
            status=self._record_status_from_handle_status(handle.status),
            workspacePath=request.workspace_path,
            sessionWorkspacePath=request.session_workspace_path,
            artifactSpoolPath=request.artifact_spool_path,
            startedAt=now,
            updatedAt=now,
        )

    @staticmethod
    def _matches_locator(
        record: CodexManagedSessionRecord,
        locator: CodexManagedSessionLocator,
    ) -> None:
        if record.session_epoch != locator.session_epoch:
            raise RuntimeError("sessionEpoch does not match the durable managed session record")
        if record.container_id != locator.container_id:
            raise RuntimeError("containerId does not match the durable managed session record")
        if record.thread_id != locator.thread_id:
            raise RuntimeError("threadId does not match the durable managed session record")

    def _require_record(
        self,
        locator: CodexManagedSessionLocator,
    ) -> CodexManagedSessionRecord | None:
        if self._session_store is None:
            return None
        record = self._session_store.load(locator.session_id)
        if record is None:
            raise RuntimeError(f"managed session record not found: {locator.session_id}")
        self._matches_locator(record, locator)
        return record

    async def _remove_container(
        self,
        container_identifier: str,
        *,
        ignore_failure: bool,
    ) -> None:
        try:
            await self._run((self._docker_binary, "rm", "-f", container_identifier))
        except RuntimeError:
            if not ignore_failure:
                raise

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

    async def _run_host_command(
        self,
        command: Sequence[str],
    ) -> tuple[str, str]:
        returncode, stdout, stderr = await self._command_runner(tuple(command))
        if returncode != 0:
            raise RuntimeError(
                f"{' '.join(command)} failed with exit code {returncode}: {stderr.strip() or stdout.strip()}"
            )
        return stdout, stderr

    async def _ensure_workspace_paths(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> None:
        workspace_path = Path(request.workspace_path)
        session_workspace_path = Path(request.session_workspace_path)
        artifact_spool_path = Path(request.artifact_spool_path)

        session_workspace_path.mkdir(parents=True, exist_ok=True)
        artifact_spool_path.mkdir(parents=True, exist_ok=True)

        if workspace_path.exists():
            return

        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        repository = str(
            request.workspace_spec.get("repository")
            or request.workspace_spec.get("repo")
            or ""
        ).strip()
        if not repository:
            workspace_path.mkdir(parents=True, exist_ok=True)
            return

        from .launcher import ManagedRuntimeLauncher

        source = ManagedRuntimeLauncher._resolve_repository_source(repository)
        branch = str(
            request.workspace_spec.get("startingBranch")
            or request.workspace_spec.get("branch")
            or ""
        ).strip()

        clone_command = ["git", "clone"]
        if branch:
            clone_command.extend(["--branch", branch, "--single-branch"])
        clone_command.extend([source, str(workspace_path)])
        await self._run_host_command(clone_command)

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

    async def _invoke_inline_python_json(
        self,
        *,
        container_id: str,
        program: str,
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
        command.extend([container_id, "python3", "-c", program])
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
        last_error: Exception | None = None
        for _attempt in range(self._ready_poll_attempts):
            try:
                stdout, _stderr = await self._run(command)
                payload = json.loads(stdout.strip() or "{}")
            except (RuntimeError, json.JSONDecodeError) as exc:
                last_error = exc
            else:
                if payload.get("ready") is True:
                    return
            if self._ready_poll_interval_seconds > 0:
                await asyncio.sleep(self._ready_poll_interval_seconds)
        details = f": {last_error}" if last_error is not None else ""
        raise RuntimeError(
            f"managed session container {container_id} did not become ready{details}"
        )

    async def _persist_handle_transition(
        self,
        *,
        locator: CodexManagedSessionLocator,
        status: str,
    ) -> None:
        if self._session_store is None:
            return
        if self._session_store.load(locator.session_id) is None:
            return
        await self._session_store.update(
            locator.session_id,
            session_epoch=locator.session_epoch,
            container_id=locator.container_id,
            thread_id=locator.thread_id,
            status=self._record_status_from_handle_status(status),
            updated_at=datetime.now(tz=UTC),
            error_message=None,
        )

    async def _emit_session_event(
        self,
        *,
        record: CodexManagedSessionRecord,
        text: str,
        kind: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self._session_supervisor is None:
            return
        self._session_supervisor.emit_session_event(
            record=record,
            text=text,
            kind=kind,
            turn_id=turn_id,
            active_turn_id=active_turn_id,
            metadata=dict(metadata or {}),
        )

    async def _container_exists(self, container_id: str) -> bool:
        returncode, stdout, stderr = await self._command_runner(
            (
                self._docker_binary,
                "inspect",
                "-f",
                "{{.Id}}",
                container_id,
            ),
            env=self._docker_env(),
        )
        if returncode == 0:
            return True
        error_output = f"{stdout}\n{stderr}".lower()
        if "no such object" in error_output or "no such container" in error_output:
            return False
        details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        raise RuntimeError(
            f"failed to inspect managed session container {container_id}: {details}"
        )

    async def launch_session(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        self._validate_launch_request(request)
        await self._ensure_workspace_paths(request)
        container_name = self._container_name(request.session_id)
        await self._remove_container(container_name, ignore_failure=True)
        run_command = [
            self._docker_binary,
            "run",
            "-d",
            "--name",
            container_name,
            "--mount",
            self._volume_mount(self._workspace_volume_name, self._workspace_root),
            "--mount",
            self._volume_mount(self._codex_volume_name, request.codex_home_path),
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
        try:
            await self._wait_ready(container_id=container_id)
            container_payload = request.model_dump(
                by_alias=True,
                exclude={"workspace_spec"},
            )
            payload = await self._invoke_json(
                container_id=container_id,
                action="launch_session",
                payload=container_payload,
                extra_env={"MOONMIND_SESSION_CONTAINER_ID": container_id},
            )
        except Exception:
            await self._remove_container(container_id, ignore_failure=True)
            raise
        handle = CodexManagedSessionHandle.model_validate(payload)
        if self._session_store is not None:
            record = self._record_from_launch(request=request, handle=handle)
            self._session_store.save(record)
            if self._session_supervisor is not None:
                await self._session_supervisor.start(record)
                await self._emit_session_event(
                    record=record,
                    kind="session_started",
                    text=(
                        f"Session started. Epoch {record.session_epoch} "
                        f"thread {record.thread_id}."
                    ),
                    metadata={"action": "start_session"},
                )
        return handle

    async def session_status(
        self,
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="session_status",
            payload=request.model_dump(by_alias=True),
        )
        handle = CodexManagedSessionHandle.model_validate(payload)
        await self._persist_handle_transition(
            locator=self._locator_from_session_state(handle.session_state),
            status=handle.status,
        )
        return handle

    async def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_inline_python_json(
            container_id=request.container_id,
            program=_SEND_TURN_INLINE_SCRIPT,
            payload=request.model_dump(by_alias=True),
        )
        response = CodexManagedSessionTurnResponse.model_validate(payload)
        if (
            self._session_store is not None
            and self._session_store.load(request.session_id) is not None
        ):
            await self._session_store.update(
                request.session_id,
                session_epoch=response.session_state.session_epoch,
                container_id=response.session_state.container_id,
                thread_id=response.session_state.thread_id,
                active_turn_id=response.session_state.active_turn_id,
                status=self._record_status_from_turn_status(response.status),
                updated_at=datetime.now(tz=UTC),
                error_message=self._turn_error_message(response),
            )
            if self._session_supervisor is not None:
                record = self._session_store.load(request.session_id)
                if record is not None:
                    await self._emit_session_event(
                        record=record,
                        kind="turn_started",
                        text=f"Turn started: {response.turn_id}.",
                        turn_id=response.turn_id,
                        active_turn_id=response.turn_id,
                        metadata={"action": "send_turn"},
                    )
                    if response.status == "completed":
                        await self._emit_session_event(
                            record=record,
                            kind="turn_completed",
                            text=f"Turn completed: {response.turn_id}.",
                            turn_id=response.turn_id,
                            active_turn_id=record.active_turn_id,
                            metadata={
                                "action": "send_turn",
                                "assistantText": response.metadata.get("assistantText"),
                            },
                        )
        return response

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
        response = CodexManagedSessionTurnResponse.model_validate(payload)
        if (
            self._session_store is not None
            and self._session_store.load(request.session_id) is not None
        ):
            await self._session_store.update(
                request.session_id,
                session_epoch=response.session_state.session_epoch,
                container_id=response.session_state.container_id,
                thread_id=response.session_state.thread_id,
                active_turn_id=response.session_state.active_turn_id,
                status=self._record_status_from_turn_status(response.status),
                updated_at=datetime.now(tz=UTC),
                error_message=self._turn_error_message(response),
            )
            if self._session_supervisor is not None and response.status == "interrupted":
                record = self._session_store.load(request.session_id)
                if record is not None:
                    await self._emit_session_event(
                        record=record,
                        kind="turn_interrupted",
                        text=f"Turn interrupted: {response.turn_id}.",
                        turn_id=response.turn_id,
                        metadata={
                            "action": "interrupt_turn",
                            "reason": response.metadata.get("reason"),
                        },
                    )
        return response

    async def clear_session(
        self,
        request: CodexManagedSessionClearRequest,
    ) -> CodexManagedSessionHandle:
        session_store = self._session_store
        previous_record = None
        if session_store is not None:
            previous_record = session_store.load(request.session_id)
            if previous_record is not None:
                self._matches_locator(previous_record, request)
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="clear_session",
            payload=request.model_dump(by_alias=True),
        )
        handle = CodexManagedSessionHandle.model_validate(payload)
        if previous_record is not None:
            assert session_store is not None
            updated_record = await session_store.update(
                request.session_id,
                session_epoch=handle.session_state.session_epoch,
                container_id=handle.session_state.container_id,
                thread_id=handle.session_state.thread_id,
                active_turn_id=handle.session_state.active_turn_id,
                image_ref=handle.image_ref or previous_record.image_ref,
                control_url=handle.control_url or previous_record.control_url,
                status=self._record_status_from_handle_status(handle.status),
                updated_at=datetime.now(tz=UTC),
                error_message=None,
            )
            if self._session_supervisor is not None:
                await self._session_supervisor.publish_reset_artifacts(
                    previous_record=previous_record,
                    record=updated_record,
                    action="clear_session",
                    reason=request.reason,
                )
        else:
            await self._persist_handle_transition(
                locator=self._locator_from_session_state(handle.session_state),
                status=handle.status,
            )
        return handle

    async def terminate_session(
        self,
        request: TerminateCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        await self._remove_container(request.container_id, ignore_failure=True)
        handle = CodexManagedSessionHandle(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
                "activeTurnId": None,
            },
            status="terminated",
        )
        if self._session_store is not None:
            if self._session_supervisor is not None:
                record = self._session_store.load(request.session_id)
                if record is not None:
                    await self._session_store.update(
                        request.session_id,
                        active_turn_id=None,
                    )
                    refreshed = self._session_store.load(request.session_id) or record
                    await self._emit_session_event(
                        record=refreshed,
                        kind="system_annotation",
                        text=f"Session terminated: {request.session_id}.",
                        metadata={"action": "terminate_session"},
                    )
                await self._session_supervisor.finalize(
                    request.session_id,
                    status="terminated",
                )
            elif self._session_store.load(request.session_id) is not None:
                await self._session_store.update(
                    request.session_id,
                    status="terminated",
                    active_turn_id=None,
                    updated_at=datetime.now(tz=UTC),
                )
        return handle

    async def fetch_session_summary(
        self,
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        record = self._require_record(request)
        if record is None:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="fetch_session_summary",
                payload=request.model_dump(by_alias=True),
            )
            return CodexManagedSessionSummary.model_validate(payload)
        return CodexManagedSessionSummary(
            sessionState=record.session_state(),
            latestSummaryRef=record.latest_summary_ref,
            latestCheckpointRef=record.latest_checkpoint_ref,
            latestControlEventRef=record.latest_control_event_ref,
            latestResetBoundaryRef=record.latest_reset_boundary_ref,
            metadata={
                "status": record.status,
                "stdoutArtifactRef": record.stdout_artifact_ref,
                "stderrArtifactRef": record.stderr_artifact_ref,
                "diagnosticsRef": record.diagnostics_ref,
                "errorMessage": record.error_message,
            },
        )

    async def publish_session_artifacts(
        self,
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        record = self._require_record(request)
        if record is None:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="publish_session_artifacts",
                payload=request.model_dump(by_alias=True),
            )
            return CodexManagedSessionArtifactsPublication.model_validate(payload)
        if (
            self._session_supervisor is not None
            and not record.published_artifact_refs()
        ):
            record = await self._session_supervisor.publish_snapshot(request.session_id)
        return CodexManagedSessionArtifactsPublication(
            sessionState=record.session_state(),
            publishedArtifactRefs=record.published_artifact_refs(),
            latestSummaryRef=record.latest_summary_ref,
            latestCheckpointRef=record.latest_checkpoint_ref,
            latestControlEventRef=record.latest_control_event_ref,
            latestResetBoundaryRef=record.latest_reset_boundary_ref,
            metadata={
                **dict(request.metadata),
                "status": record.status,
                "stdoutArtifactRef": record.stdout_artifact_ref,
                "stderrArtifactRef": record.stderr_artifact_ref,
                "diagnosticsRef": record.diagnostics_ref,
            },
        )

    async def reconcile(self) -> list[CodexManagedSessionRecord]:
        if self._session_store is None:
            return []
        reconciled: list[CodexManagedSessionRecord] = []
        for record in self._session_store.list_active():
            if await self._container_exists(record.container_id):
                if self._session_supervisor is not None:
                    await self._session_supervisor.start(record)
                reconciled.append(record)
                continue
            updated = await self._session_store.update(
                record.session_id,
                status="degraded",
                error_message="managed session container is missing during reconcile",
                updated_at=datetime.now(tz=UTC),
            )
            reconciled.append(updated)
        return reconciled
