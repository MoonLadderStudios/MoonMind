"""Docker-backed controller for transitional Codex managed sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import shutil
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
from moonmind.utils.logging import SecretRedactor, scrub_github_tokens

from .managed_session_store import ManagedSessionStore
from .managed_session_supervisor import ManagedSessionSupervisor


_RUNTIME_MODULE = "moonmind.workflows.temporal.runtime.codex_session_runtime"
_CONTAINER_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_.-]+")
_RESERVED_SESSION_ENV_PREFIX = "MOONMIND_SESSION_"
_MANAGED_SESSION_CONTAINER_UID = 1000
_MANAGED_SESSION_CONTAINER_GID = 1000
_MANAGED_SESSION_CONTAINER_USER = (
    f"{_MANAGED_SESSION_CONTAINER_UID}:{_MANAGED_SESSION_CONTAINER_GID}"
)
_SENSITIVE_ENV_KEY_PATTERN = re.compile(
    r"(?i)(?:token|secret|password|key|credential|auth)"
)
_GIT_COMMAND_LOCALE = {"LC_ALL": "C", "LANG": "C"}
logger = logging.getLogger(__name__)


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


def _is_sensitive_env_key(key: str) -> bool:
    return bool(_SENSITIVE_ENV_KEY_PATTERN.search(key))


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

    def _is_within_workspace_root(self, path: Path) -> bool:
        workspace_root = Path(self._workspace_root).expanduser().resolve()
        candidate = path.expanduser().resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return False
        return True

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
        auth_volume_path = str(
            request.environment.get("MANAGED_AUTH_VOLUME_PATH") or ""
        ).strip()
        if auth_volume_path:
            normalized_auth_volume_path = _normalize_absolute_posix_path(
                auth_volume_path,
                field_name="environment.MANAGED_AUTH_VOLUME_PATH",
            )
            normalized_codex_home_path = _normalize_absolute_posix_path(
                request.codex_home_path,
                field_name="codexHomePath",
            )
            if normalized_auth_volume_path == normalized_codex_home_path:
                raise RuntimeError(
                    "environment.MANAGED_AUTH_VOLUME_PATH must not equal codexHomePath"
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

    @staticmethod
    def _command_secrets(
        command: Sequence[str],
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> list[str]:
        secrets: list[str] = []

        def _append_assignment(assignment: str) -> None:
            if "=" not in assignment:
                return
            key, value = assignment.split("=", 1)
            if _is_sensitive_env_key(key) and value:
                secrets.append(value)

        for index, part in enumerate(command):
            if part in {"-e", "--env"} and index + 1 < len(command):
                _append_assignment(command[index + 1])
                continue
            if part.startswith("--env="):
                _append_assignment(part[len("--env="):])

        for key, value in (extra_env or {}).items():
            if _is_sensitive_env_key(str(key)) and value:
                secrets.append(str(value))

        return secrets

    @classmethod
    def _scrub_command_failure(
        cls,
        command: Sequence[str],
        detail: str,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        redactor = SecretRedactor.from_environ(
            placeholder="[REDACTED]",
            extra_secrets=cls._command_secrets(command, extra_env=extra_env),
        )
        rendered_command = scrub_github_tokens(redactor.scrub(" ".join(command)))
        rendered_detail = scrub_github_tokens(redactor.scrub(detail))
        return rendered_command, rendered_detail

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
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                stderr.strip() or stdout.strip(),
                extra_env=extra_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: {rendered_detail}"
            )
        return stdout, stderr

    async def _run_host_command(
        self,
        command: Sequence[str],
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        env = None
        if extra_env:
            env = {str(key): str(value) for key, value in extra_env.items()}
        returncode, stdout, stderr = await self._command_runner(tuple(command), env=env)
        if returncode != 0:
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                stderr.strip() or stdout.strip(),
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: {rendered_detail}"
            )
        return stdout, stderr

    async def _run_git_host_command(
        self,
        command: Sequence[str],
    ) -> tuple[str, str]:
        return await self._run_host_command(command, extra_env=_GIT_COMMAND_LOCALE)

    @staticmethod
    def _workspace_git_command(
        workspace_path: Path,
        *args: str,
    ) -> list[str]:
        resolved_workspace = str(workspace_path.resolve())
        return [
            "git",
            "-c",
            f"safe.directory={resolved_workspace}",
            "-C",
            resolved_workspace,
            *args,
        ]

    async def _git_command_result(
        self,
        command: Sequence[str],
    ) -> tuple[int, str, str]:
        return await self._command_runner(
            tuple(command),
            env=dict(_GIT_COMMAND_LOCALE),
        )

    async def _workspace_is_git_repository(self, *, workspace_path: Path) -> bool:
        returncode, stdout, _stderr = await self._git_command_result(
            self._workspace_git_command(
                workspace_path,
                "rev-parse",
                "--is-inside-work-tree",
            )
        )
        return returncode == 0 and stdout.strip() == "true"

    async def _remove_workspace_path(self, *, workspace_path: Path) -> None:
        if not workspace_path.exists():
            return
        if workspace_path.is_dir():
            shutil.rmtree(workspace_path)
            return
        workspace_path.unlink()

    async def _clone_workspace(
        self,
        *,
        workspace_path: Path,
        request: LaunchCodexManagedSessionRequest,
        repository: str,
    ) -> None:
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        await self._remove_workspace_path(workspace_path=workspace_path)

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
        await self._run_git_host_command(clone_command)
        self._normalize_container_path_ownership((workspace_path,))

    @staticmethod
    def _branch_missing_checkout_failure(detail: str) -> bool:
        normalized = detail.lower()
        return (
            "did not match any file(s) known to git" in normalized
            or "pathspec" in normalized
        )

    @staticmethod
    def _remote_branch_missing_failure(detail: str) -> bool:
        normalized = detail.lower()
        return (
            "couldn't find remote ref" in normalized
            or "remote ref does not exist" in normalized
        )

    async def _ensure_workspace_paths(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> None:
        workspace_path = Path(request.workspace_path)
        session_workspace_path = Path(request.session_workspace_path)
        artifact_spool_path = Path(request.artifact_spool_path)
        created_paths: list[Path] = []

        if not session_workspace_path.exists():
            session_workspace_path.mkdir(parents=True, exist_ok=True)
            created_paths.append(session_workspace_path)
        if not artifact_spool_path.exists():
            artifact_spool_path.mkdir(parents=True, exist_ok=True)
            created_paths.append(artifact_spool_path)

        repository = str(
            request.workspace_spec.get("repository")
            or request.workspace_spec.get("repo")
            or ""
        ).strip()
        if workspace_path.exists():
            self._collect_managed_support_paths(
                request=request,
                owned_paths=created_paths,
            )
            if repository:
                if not await self._workspace_is_git_repository(workspace_path=workspace_path):
                    await self._clone_workspace(
                        workspace_path=workspace_path,
                        request=request,
                        repository=repository,
                    )
                await self._ensure_target_branch(
                    workspace_path=workspace_path,
                    request=request,
                )
            self._normalize_container_path_ownership(created_paths)
            return

        if not repository:
            workspace_path.parent.mkdir(parents=True, exist_ok=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
            created_paths.append(workspace_path)
            self._collect_managed_support_paths(
                request=request,
                owned_paths=created_paths,
            )
            self._normalize_container_path_ownership(created_paths)
            return

        await self._clone_workspace(
            workspace_path=workspace_path,
            request=request,
            repository=repository,
        )
        await self._ensure_target_branch(
            workspace_path=workspace_path,
            request=request,
        )
        self._collect_managed_support_paths(
            request=request,
            owned_paths=created_paths,
        )
        self._normalize_container_path_ownership(created_paths)

    def _collect_managed_support_paths(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        owned_paths: list[Path],
    ) -> None:
        codex_home_path = Path(request.codex_home_path)
        if not self._is_within_workspace_root(codex_home_path):
            return

        runtime_support_path = codex_home_path.parent
        if not runtime_support_path.exists():
            runtime_support_path.mkdir(parents=True, exist_ok=True)
        owned_paths.append(runtime_support_path)

        if not codex_home_path.exists():
            codex_home_path.mkdir(parents=True, exist_ok=True)
        owned_paths.append(codex_home_path)

    async def _ensure_target_branch(
        self,
        *,
        workspace_path: Path,
        request: LaunchCodexManagedSessionRequest,
    ) -> None:
        target_branch = str(request.workspace_spec.get("targetBranch") or "").strip()
        if not target_branch:
            return

        checkout_command = tuple(
            self._workspace_git_command(
                workspace_path,
                "checkout",
                target_branch,
            )
        )
        returncode, stdout, stderr = await self._git_command_result(checkout_command)
        if returncode == 0:
            return

        failure_detail = stderr or stdout
        if not self._branch_missing_checkout_failure(failure_detail):
            raise RuntimeError(
                f"{' '.join(checkout_command)} failed with exit code {returncode}: "
                f"{stderr.strip() or stdout.strip()}"
            )

        fetch_command = self._workspace_git_command(
            workspace_path,
            "fetch",
            "origin",
            target_branch,
        )
        fetch_returncode, fetch_stdout, fetch_stderr = await self._git_command_result(
            fetch_command
        )
        if fetch_returncode == 0:
            await self._run_git_host_command(
                self._workspace_git_command(
                    workspace_path,
                    "checkout",
                    "-B",
                    target_branch,
                    f"origin/{target_branch}",
                )
            )
            return

        fetch_detail = fetch_stderr or fetch_stdout
        if not self._remote_branch_missing_failure(fetch_detail):
            raise RuntimeError(
                f"{' '.join(fetch_command)} failed with exit code {fetch_returncode}: "
                f"{fetch_stderr.strip() or fetch_stdout.strip()}"
            )

        await self._run_git_host_command(
            self._workspace_git_command(
                workspace_path,
                "checkout",
                "-b",
                target_branch,
            )
        )

    @staticmethod
    def _normalize_container_path_ownership(paths: Sequence[Path]) -> None:
        geteuid = getattr(os, "geteuid", None)
        if os.name != "posix" or not callable(geteuid) or geteuid() != 0:
            return
        for path in paths:
            if not path.exists():
                continue
            DockerCodexManagedSessionController._chown_path(path)
            for root, dirnames, filenames in os.walk(path):
                root_path = Path(root)
                for dirname in dirnames:
                    DockerCodexManagedSessionController._chown_path(root_path / dirname)
                for filename in filenames:
                    DockerCodexManagedSessionController._chown_path(root_path / filename)

    @staticmethod
    def _chown_path(path: Path) -> None:
        os.chown(
            path,
            _MANAGED_SESSION_CONTAINER_UID,
            _MANAGED_SESSION_CONTAINER_GID,
            follow_symlinks=False,
        )

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
        session_id = str(payload.get("sessionId") or "").strip()
        target_label = (
            f"managed-session action {action} for session {session_id} "
            f"in container {container_id}"
            if session_id
            else f"managed-session action {action} in container {container_id}"
        )

        response_text = stdout.strip()
        def _raise_invalid_json_response(
            reason: str,
            detail: str,
            *,
            from_exc: Exception | None = None,
        ) -> None:
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                detail,
                extra_env=extra_env,
            )
            message = (
                f"{target_label} {reason} via {rendered_command}: {rendered_detail}"
            )
            if from_exc is not None:
                raise RuntimeError(message) from from_exc
            raise RuntimeError(message)

        if not response_text:
            _raise_invalid_json_response(
                "returned no JSON output",
                "stdout was blank",
            )

        try:
            response_payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            _raise_invalid_json_response(
                "returned invalid JSON",
                f"stdout={response_text}",
                from_exc=exc,
            )

        if not isinstance(response_payload, dict):
            _raise_invalid_json_response(
                "returned a non-object JSON payload",
                f"stdout={response_text}",
            )

        return response_payload

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
        active_turn_id: str | None,
    ) -> CodexManagedSessionRecord | None:
        if self._session_store is None:
            return None
        if self._session_store.load(locator.session_id) is None:
            return None
        return await self._session_store.update(
            locator.session_id,
            session_epoch=locator.session_epoch,
            container_id=locator.container_id,
            thread_id=locator.thread_id,
            active_turn_id=active_turn_id,
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
        try:
            self._session_supervisor.emit_session_event(
                record=record,
                text=text,
                kind=kind,
                turn_id=turn_id,
                active_turn_id=active_turn_id,
                metadata=dict(metadata or {}),
            )
        except Exception:
            logger.warning(
                "Managed session event publication failed for session %s kind %s",
                record.session_id,
                kind,
                exc_info=True,
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
            "--user",
            _MANAGED_SESSION_CONTAINER_USER,
            "--mount",
            self._volume_mount(self._workspace_volume_name, self._workspace_root),
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
            "-e",
            "MOONMIND_SESSION_TURN_COMPLETION_TIMEOUT_SECONDS="
            f"{request.turn_completion_timeout_seconds}",
        ]
        auth_volume_path = str(
            request.environment.get("MANAGED_AUTH_VOLUME_PATH") or ""
        ).strip()
        if auth_volume_path:
            run_command.extend(
                [
                    "--mount",
                    self._volume_mount(self._codex_volume_name, auth_volume_path),
                ]
            )
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
        record = await self._persist_handle_transition(
            locator=self._locator_from_session_state(handle.session_state),
            status=handle.status,
            active_turn_id=handle.session_state.active_turn_id,
        )
        if record is not None:
            await self._emit_session_event(
                record=record,
                kind="session_resumed",
                text=(
                    f"Session resumed. Epoch {record.session_epoch} "
                    f"thread {record.thread_id}."
                ),
                active_turn_id=handle.session_state.active_turn_id,
                metadata={"action": "resume_session"},
            )
        return handle

    async def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="send_turn",
            payload=request.model_dump(by_alias=True),
        )
        response = CodexManagedSessionTurnResponse.model_validate(payload)
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                updated_record = await self._session_store.update(
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
                    await self._emit_session_event(
                        record=updated_record,
                        kind="turn_started",
                        text=f"Turn started: {response.turn_id}.",
                        turn_id=response.turn_id,
                        active_turn_id=response.turn_id,
                        metadata={
                            "action": "send_turn",
                            "reason": request.reason,
                        },
                    )
                    if response.status == "completed":
                        await self._emit_session_event(
                            record=updated_record,
                            kind="turn_completed",
                            text=f"Turn completed: {response.turn_id}.",
                            turn_id=response.turn_id,
                            active_turn_id=updated_record.active_turn_id,
                            metadata={
                                "action": "send_turn",
                                "assistantText": response.metadata.get("assistantText"),
                                "reason": request.reason,
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
        response = CodexManagedSessionTurnResponse.model_validate(payload)
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                updated_record = await self._session_store.update(
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
                    metadata = dict(request.metadata or {})
                    metadata["action"] = "steer_turn"
                    await self._emit_session_event(
                        record=updated_record,
                        kind="system_annotation",
                        text=f"Turn steered: {request.turn_id}.",
                        turn_id=request.turn_id,
                        active_turn_id=response.session_state.active_turn_id
                        or request.turn_id,
                        metadata=metadata,
                    )
        return response

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
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                updated_record = await self._session_store.update(
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
                    await self._emit_session_event(
                        record=updated_record,
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
                active_turn_id=handle.session_state.active_turn_id,
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
                        kind="session_terminated",
                        text=f"Session terminated: {request.session_id}.",
                        metadata={
                            "action": "terminate_session",
                            "reason": request.reason,
                        },
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
                "observabilityEventsRef": record.observability_events_ref,
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
                "observabilityEventsRef": record.observability_events_ref,
            },
        )

    async def reconcile(self) -> list[CodexManagedSessionRecord]:
        if self._session_store is None:
            return []
        reconciled: list[CodexManagedSessionRecord] = []
        for record in self._session_store.list_active():
            try:
                container_exists = await self._container_exists(record.container_id)
                if not container_exists:
                    updated = await self._session_store.update(
                        record.session_id,
                        status="degraded",
                        error_message=(
                            "managed session container is missing during reconcile"
                        ),
                        updated_at=datetime.now(tz=UTC),
                    )
                    reconciled.append(updated)
                    continue
                if self._session_supervisor is not None:
                    await self._session_supervisor.start(record)
                reconciled.append(record)
            except Exception as exc:
                logger.warning(
                    "Managed session reconcile degraded session %s after reattach failure",
                    record.session_id,
                    exc_info=True,
                )
                updated = await self._session_store.update(
                    record.session_id,
                    status="degraded",
                    error_message=str(exc),
                    updated_at=datetime.now(tz=UTC),
                )
                reconciled.append(updated)
        return reconciled
