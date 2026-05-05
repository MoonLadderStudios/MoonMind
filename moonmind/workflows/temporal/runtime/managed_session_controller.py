"""Docker-backed controller for transitional Codex managed sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import posixpath
import re
import shlex
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse

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
from moonmind.workflows.codex_session_timeouts import (
    DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
)
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_github_token_for_launch,
)
from moonmind.utils.logging import SecretRedactor, scrub_github_tokens
from moonmind.workflow_docker_mode import normalize_workflow_docker_mode

from .github_auth_broker import GitHubAuthBrokerManager
from .git_auth import build_github_token_git_environment
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
_SESSION_STATE_FILENAME = ".moonmind-codex-session-state.json"
_CONTAINER_LOG_EXCERPT_TAIL_LINES = 40
_CONTAINER_LOG_EXCERPT_MAX_CHARS = 2000
_LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES = 4 * 1024
logger = logging.getLogger(__name__)

def _last_assistant_text_metadata(value: str) -> dict[str, Any]:
    normalized = str(value or "").strip()
    if not normalized:
        return {}
    encoded = normalized.encode("utf-8")
    if len(encoded) <= _LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES:
        return {"lastAssistantText": normalized}
    truncated = encoded[:_LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return {
        "lastAssistantText": truncated,
        "lastAssistantTextTruncated": True,
        "lastAssistantTextOriginalChars": len(normalized),
    }

def _managed_session_docker_network(
    request_environment: Mapping[str, str] | None = None,
) -> str | None:
    """Return the Docker network managed session containers should join."""

    for env_key in (
        "MOONMIND_MANAGED_SESSION_DOCKER_NETWORK",
        "MOONMIND_DOCKER_NETWORK",
    ):
        raw_value = os.environ.get(env_key)
        if raw_value is None:
            continue
        value = raw_value.strip()
        if value.lower() in {"", "none", "disabled", "off"}:
            return None
        return value

    moonmind_url = ""
    if request_environment is not None:
        moonmind_url = str(request_environment.get("MOONMIND_URL") or "").strip()
    if not moonmind_url:
        moonmind_url = os.environ.get("MOONMIND_URL", "").strip()
    if moonmind_url:
        hostname = (urlparse(moonmind_url).hostname or "").strip().lower()
        if hostname in {"api", "moonmind-api", "moonmind-api-1"}:
            return "local-network"
    return None

class CommandRunner(Protocol):
    async def __call__(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        pass

async def _default_command_runner(
    command: tuple[str, ...],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    run_as_uid: int | None = None,
    run_as_gid: int | None = None,
) -> tuple[int, str, str]:
    subprocess_kwargs: dict[str, Any] = {}
    geteuid = getattr(os, "geteuid", None)
    if os.name == "posix" and callable(geteuid) and geteuid() == 0:
        if run_as_uid is not None or run_as_gid is not None:
            subprocess_kwargs["extra_groups"] = []
        if run_as_uid is not None:
            subprocess_kwargs["user"] = run_as_uid
        if run_as_gid is not None:
            subprocess_kwargs["group"] = run_as_gid
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
        **subprocess_kwargs,
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
        network_name: str | None = None,
        moonmind_url: str | None = None,
        session_store: ManagedSessionStore | None = None,
        session_supervisor: ManagedSessionSupervisor | Any | None = None,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        ready_poll_interval_seconds: float = 1.0,
        ready_poll_attempts: int = 30,
        turn_poll_interval_seconds: float = 1.0,
        turn_poll_timeout_seconds: float = (
            DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
        ),
        command_runner: CommandRunner = _default_command_runner,
        github_auth_brokers: GitHubAuthBrokerManager | Any | None = None,
    ) -> None:
        self._workspace_volume_name = workspace_volume_name
        self._codex_volume_name = codex_volume_name
        self._workspace_root = workspace_root
        self._network_name = str(network_name or "").strip() or None
        self._moonmind_url = str(moonmind_url or "").strip() or None
        self._session_store = session_store
        self._session_supervisor = session_supervisor
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._ready_poll_interval_seconds = ready_poll_interval_seconds
        self._ready_poll_attempts = ready_poll_attempts
        self._turn_poll_interval_seconds = turn_poll_interval_seconds
        self._turn_poll_timeout_seconds = turn_poll_timeout_seconds
        self._command_runner = command_runner
        self._github_auth_brokers = github_auth_brokers or GitHubAuthBrokerManager()

    @staticmethod
    def _managed_session_user_command_kwargs() -> dict[str, int]:
        geteuid = getattr(os, "geteuid", None)
        if os.name != "posix" or not callable(geteuid) or geteuid() != 0:
            return {}
        return {
            "run_as_uid": _MANAGED_SESSION_CONTAINER_UID,
            "run_as_gid": _MANAGED_SESSION_CONTAINER_GID,
        }

    def _docker_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return env

    def _apply_unrestricted_docker_session_environment(
        self,
        session_environment: dict[str, str],
    ) -> bool:
        raw_mode = session_environment.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        if raw_mode is None:
            raw_mode = os.environ.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        workflow_docker_mode = normalize_workflow_docker_mode(raw_mode)
        if workflow_docker_mode != "unrestricted":
            return False

        session_environment["MOONMIND_WORKFLOW_DOCKER_MODE"] = "unrestricted"
        if self._docker_host:
            session_environment.setdefault("DOCKER_HOST", self._docker_host)
            session_environment.setdefault("SYSTEM_DOCKER_HOST", self._docker_host)
        return True

    def _unrestricted_docker_proxy_network(
        self,
        *,
        session_environment: Mapping[str, str],
        docker_network: str | None,
    ) -> str | None:
        mode = normalize_workflow_docker_mode(
            session_environment.get("MOONMIND_WORKFLOW_DOCKER_MODE")
            or os.environ.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        )
        if mode != "unrestricted":
            return None
        docker_host = str(
            session_environment.get("DOCKER_HOST") or self._docker_host or ""
        ).strip()
        if "docker-proxy" not in docker_host:
            return None
        proxy_network = (
            os.environ.get("MOONMIND_DOCKER_PROXY_NETWORK")
            or os.environ.get("MOONMIND_DOCKER_PROXY_NETWORK_NAME")
            or "moonmind_docker-proxy-network"
        ).strip()
        if not proxy_network or proxy_network == docker_network:
            return None
        return proxy_network

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
    def _write_executable_script(path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    @staticmethod
    def _render_gh_wrapper_script(*, socket_path: str) -> str:
        return (
            "#!/usr/bin/env python3\n"
            "from moonmind.workflows.temporal.runtime.github_auth_broker "
            "import run_gh_wrapper\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            f"    raise SystemExit(run_gh_wrapper(socket_path={socket_path!r}))\n"
        )

    @staticmethod
    def _render_git_credential_helper_script(*, socket_path: str) -> str:
        return (
            "#!/usr/bin/env python3\n"
            "from moonmind.workflows.temporal.runtime.github_auth_broker "
            "import run_git_credential_helper\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            f"    raise SystemExit(run_git_credential_helper(socket_path={socket_path!r}))\n"
        )

    @staticmethod
    def _format_git_config_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @classmethod
    def _persist_brokered_github_config(
        cls,
        session_environment: dict[str, str],
        *,
        workspace_path: str,
        support_root: Path,
        github_socket_path: str,
    ) -> list[Path]:
        """Persist broker-backed git/gh config visible inside the session container."""

        bin_dir = support_root / "bin"
        git_config_path = support_root / "gitconfig"
        git_helper_path = bin_dir / "git-credential-moonmind"
        gh_wrapper_path = bin_dir / "gh"
        touched_paths: list[Path] = [
            support_root,
            bin_dir,
            git_config_path,
            git_helper_path,
        ]

        support_root.mkdir(parents=True, exist_ok=True)
        bin_dir.mkdir(parents=True, exist_ok=True)
        cls._write_executable_script(
            git_helper_path,
            cls._render_git_credential_helper_script(socket_path=github_socket_path),
        )
        touched_paths.append(gh_wrapper_path)
        cls._write_executable_script(
            gh_wrapper_path,
            cls._render_gh_wrapper_script(socket_path=github_socket_path),
        )

        git_config_lines = [
            "# moonmind-managed-git-config\n",
        ]
        existing_global_git_config = str(
            session_environment.get("GIT_CONFIG_GLOBAL") or ""
        ).strip()
        if (
            existing_global_git_config
            and Path(existing_global_git_config) != git_config_path
        ):
            git_config_lines.extend(
                [
                    "[include]\n",
                    (
                        "\tpath = "
                        f"{cls._format_git_config_value(existing_global_git_config)}\n"
                    ),
                ]
            )
        git_config_lines.extend(
            [
                "[safe]\n",
                f"\tdirectory = {cls._format_git_config_value(str(workspace_path))}\n",
                "[credential]\n",
                f"\thelper = !{shlex.quote(str(git_helper_path))}\n",
            ]
        )
        git_config_path.write_text("".join(git_config_lines), encoding="utf-8")
        git_config_path.chmod(0o600)

        existing_path = str(session_environment.get("PATH") or "").strip()
        system_paths = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        session_environment["PATH"] = (
            f"{bin_dir}{os.pathsep}{existing_path}"
            if existing_path
            else f"{bin_dir}{os.pathsep}{system_paths}"
        )
        session_environment["GIT_CONFIG_GLOBAL"] = str(git_config_path)
        session_environment.setdefault("GIT_TERMINAL_PROMPT", "0")

        repo_git_config_path = Path(workspace_path) / ".git" / "config"
        if repo_git_config_path.exists():
            marker = "# moonmind-credential-helper"
            existing_config = repo_git_config_path.read_text(encoding="utf-8")
            if marker not in existing_config:
                credential_section = (
                    f"\n{marker}\n"
                    "[credential]\n"
                    f"\thelper = !{shlex.quote(str(git_helper_path))}\n"
                )
                repo_git_config_path.write_text(
                    existing_config + credential_section,
                    encoding="utf-8",
                )
                touched_paths.append(repo_git_config_path)

        return touched_paths

    async def _configure_session_github_auth(
        self,
        request: LaunchCodexManagedSessionRequest,
        session_environment: dict[str, str],
    ) -> dict[str, str]:
        token = await resolve_github_token_for_launch(
            request.environment,
            github_credential=request.github_credential,
        )
        token = str(token or "").strip()
        if not token:
            return {}

        support_root = Path(request.session_workspace_path) / ".moonmind"
        socket_path = self._build_github_socket_path(
            run_id=request.session_id,
            support_root=str(support_root),
        )
        socket_dir = Path(socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)
        await self._github_auth_brokers.start(
            run_id=request.session_id,
            token=token,
            socket_path=socket_path,
        )
        touched_paths = self._persist_brokered_github_config(
            session_environment,
            workspace_path=request.workspace_path,
            support_root=support_root,
            github_socket_path=socket_path,
        )
        touched_paths.append(socket_dir)
        touched_paths.append(Path(socket_path))
        self._normalize_container_path_owners(touched_paths)
        # Codex shell tools can invoke nested `bash -lc` commands that bypass
        # the workspace-local gh wrapper. Bind the token through Docker's
        # inherited environment (`-e GITHUB_TOKEN`) so it is not rendered into
        # the docker command line or the launch payload.
        return {"GITHUB_TOKEN": token}

    @staticmethod
    def _build_github_socket_path(
        *,
        run_id: str,
        support_root: str | None,
        socket_root: str | None = None,
    ) -> str:
        """Keep broker sockets on a short path to avoid AF_UNIX length limits."""
        resolved_socket_root = Path(socket_root) if socket_root else Path("/tmp")
        if not resolved_socket_root.is_dir() and socket_root is None:
            resolved_socket_root = Path(tempfile.gettempdir())
        resolved_socket_root = (
            resolved_socket_root / "mm-gh"
            if socket_root is None
            else resolved_socket_root
        )
        material = run_id
        if support_root:
            material = f"{Path(support_root).resolve()}::{run_id}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return str(resolved_socket_root / digest / "github.sock")

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
    def _handle_status_from_record_status(
        status: ManagedSessionRecordStatus | str,
    ) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"launching", "ready", "busy", "terminating", "terminated"}:
            return normalized
        if normalized in {"degraded", "failed"}:
            return "failed"
        return "ready"

    @staticmethod
    def _request_matches_record(
        request: LaunchCodexManagedSessionRequest,
        record: CodexManagedSessionRecord,
    ) -> bool:
        return (
            request.task_run_id == record.task_run_id
            and request.session_id == record.session_id
            and request.session_epoch == record.session_epoch
            and request.thread_id == record.thread_id
            and request.workspace_path == record.workspace_path
            and request.session_workspace_path == record.session_workspace_path
            and request.artifact_spool_path == record.artifact_spool_path
            and (request.image_ref == record.image_ref)
        )

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
            metadata=dict(request.metadata),
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

    @staticmethod
    def _transport_output_snippet(text: str, *, max_chars: int = 500) -> str:
        return json.dumps(text[:max_chars], ensure_ascii=True)

    def _raise_transport_failure(
        self,
        command: Sequence[str],
        *,
        action: str,
        container_id: str,
        session_id: str | None = None,
        reason: str,
        stdout: str | None = None,
        stderr: str,
        extra_env: Mapping[str, str] | None = None,
        cause: Exception | None = None,
    ) -> None:
        target_label = (
            f"managed-session action {action} for session {session_id} "
            f"in container {container_id}"
            if session_id
            else f"managed-session action {action} in container {container_id}"
        )
        rendered_detail = (
            "stdout was blank"
            if stdout is None
            else f"stdout={self._transport_output_snippet(stdout)}"
        )
        stderr_text = stderr.strip()
        if stderr_text:
            rendered_detail += (
                f"; stderr: {self._transport_output_snippet(stderr_text)}"
            )
        rendered_command, scrubbed_detail = self._scrub_command_failure(
            command,
            rendered_detail,
            extra_env=extra_env,
        )
        error = RuntimeError(
            f"{target_label} {reason} via {rendered_command}: {scrubbed_detail}"
        )
        if cause is not None:
            raise error from cause
        raise error

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

    async def _connect_container_network(
        self,
        *,
        container_id: str,
        network_name: str,
    ) -> None:
        await self._run(
            (
                self._docker_binary,
                "network",
                "connect",
                network_name,
                container_id,
            )
        )

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
        run_as_managed_session_user: bool = False,
    ) -> tuple[str, str]:
        env = None
        if extra_env:
            env = {str(key): str(value) for key, value in extra_env.items()}
        command_kwargs = (
            self._managed_session_user_command_kwargs()
            if run_as_managed_session_user
            else {}
        )
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            env=env,
            **command_kwargs,
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

    @staticmethod
    async def _git_host_environment(
        request: LaunchCodexManagedSessionRequest | None = None,
    ) -> dict[str, str]:
        env = dict(_GIT_COMMAND_LOCALE)
        request_env = request.environment if request is not None else {}
        try:
            token = (
                await resolve_github_token_for_launch(
                    request_env,
                    github_credential=(
                        request.github_credential if request is not None else None
                    ),
                )
                if request is not None
                else None
            )
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        token = str(token or "").strip()
        if token:
            # Avoid depending on persistent per-user gh setup in the worker. The
            # clone happens before the managed agent container starts.
            return build_github_token_git_environment(
                token,
                base_env=env,
                terminal_prompt=str(request_env.get("GIT_TERMINAL_PROMPT") or "0"),
            )

        terminal_prompt = str(request_env.get("GIT_TERMINAL_PROMPT") or "").strip()
        if terminal_prompt:
            env["GIT_TERMINAL_PROMPT"] = terminal_prompt
        return env

    async def _run_git_host_command(
        self,
        command: Sequence[str],
        *,
        request: LaunchCodexManagedSessionRequest | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        if git_env is None:
            git_env = await self._git_host_environment(request)
        return await self._run_host_command(
            command,
            extra_env=git_env,
            run_as_managed_session_user=True,
        )

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
        *,
        request: LaunchCodexManagedSessionRequest | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        command_kwargs = self._managed_session_user_command_kwargs()
        if git_env is None:
            git_env = await self._git_host_environment(request)
        return await self._command_runner(
            tuple(command),
            env=git_env,
            **command_kwargs,
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
        git_env: Mapping[str, str],
    ) -> None:
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        await self._remove_workspace_path(workspace_path=workspace_path)
        self._normalize_container_path_owner(workspace_path.parent)

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
        await self._run_git_host_command(
            clone_command,
            request=request,
            git_env=git_env,
        )
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
        support_root_paths: list[Path] = [
            session_workspace_path,
            artifact_spool_path,
        ]
        recursive_support_paths: list[Path] = []

        session_workspace_path.mkdir(parents=True, exist_ok=True)
        artifact_spool_path.mkdir(parents=True, exist_ok=True)
        self._collect_managed_support_paths(
            request=request,
            owned_root_paths=support_root_paths,
            recursively_owned_paths=recursive_support_paths,
        )
        self._normalize_container_path_owners(support_root_paths)
        self._normalize_container_path_ownership(recursive_support_paths)

        repository = str(
            request.workspace_spec.get("repository")
            or request.workspace_spec.get("repo")
            or ""
        ).strip()
        git_env = (
            await self._git_host_environment(request)
            if repository
            else None
        )
        if workspace_path.exists():
            self._normalize_container_path_ownership([workspace_path])
            if repository:
                if not await self._workspace_is_git_repository(workspace_path=workspace_path):
                    await self._clone_workspace(
                        workspace_path=workspace_path,
                        request=request,
                        repository=repository,
                        git_env=git_env,
                    )
                await self._ensure_target_branch(
                    workspace_path=workspace_path,
                    request=request,
                    git_env=git_env,
                )
            return

        if not repository:
            workspace_path.parent.mkdir(parents=True, exist_ok=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
            self._normalize_container_path_ownership([workspace_path])
            return

        await self._clone_workspace(
            workspace_path=workspace_path,
            request=request,
            repository=repository,
            git_env=git_env,
        )
        await self._ensure_target_branch(
            workspace_path=workspace_path,
            request=request,
            git_env=git_env,
        )

    def _collect_managed_support_paths(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        owned_root_paths: list[Path],
        recursively_owned_paths: list[Path],
    ) -> None:
        codex_home_path = Path(request.codex_home_path)
        if not self._is_within_workspace_root(codex_home_path):
            return

        runtime_support_path = codex_home_path.parent
        if not runtime_support_path.exists():
            runtime_support_path.mkdir(parents=True, exist_ok=True)
        owned_root_paths.append(runtime_support_path)

        if not codex_home_path.exists():
            codex_home_path.mkdir(parents=True, exist_ok=True)
        recursively_owned_paths.append(codex_home_path)

    async def _ensure_target_branch(
        self,
        *,
        workspace_path: Path,
        request: LaunchCodexManagedSessionRequest,
        git_env: Mapping[str, str],
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
        returncode, stdout, stderr = await self._git_command_result(
            checkout_command,
            request=request,
            git_env=git_env,
        )
        if returncode == 0:
            return

        failure_detail = stderr or stdout
        if not self._branch_missing_checkout_failure(failure_detail):
            rendered_command, rendered_detail = self._scrub_command_failure(
                checkout_command,
                stderr.strip() or stdout.strip(),
                extra_env=git_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: "
                f"{rendered_detail}"
            )

        fetch_command = self._workspace_git_command(
            workspace_path,
            "fetch",
            "origin",
            target_branch,
        )
        fetch_returncode, fetch_stdout, fetch_stderr = await self._git_command_result(
            fetch_command,
            request=request,
            git_env=git_env,
        )
        if fetch_returncode == 0:
            await self._run_git_host_command(
                self._workspace_git_command(
                    workspace_path,
                    "checkout",
                    "-B",
                    target_branch,
                    f"origin/{target_branch}",
                ),
                request=request,
                git_env=git_env,
            )
            return

        fetch_detail = fetch_stderr or fetch_stdout
        if not self._remote_branch_missing_failure(fetch_detail):
            rendered_command, rendered_detail = self._scrub_command_failure(
                fetch_command,
                fetch_stderr.strip() or fetch_stdout.strip(),
                extra_env=git_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {fetch_returncode}: "
                f"{rendered_detail}"
            )

        await self._run_git_host_command(
            self._workspace_git_command(
                workspace_path,
                "checkout",
                "-b",
                target_branch,
            ),
            request=request,
            git_env=git_env,
        )

    @staticmethod
    def _can_normalize_container_path_ownership() -> bool:
        geteuid = getattr(os, "geteuid", None)
        return os.name == "posix" and callable(geteuid) and geteuid() == 0

    @staticmethod
    def _normalize_container_path_owner(path: Path) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        if path.exists():
            DockerCodexManagedSessionController._chown_path(path)

    @staticmethod
    def _normalize_container_path_owners(paths: Sequence[Path]) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        for path in paths:
            if path.exists():
                DockerCodexManagedSessionController._chown_path(path)

    @staticmethod
    def _normalize_container_path_ownership(paths: Sequence[Path]) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        roots = DockerCodexManagedSessionController._deduplicate_ownership_roots(paths)
        for path in roots:
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
    def _deduplicate_ownership_roots(paths: Sequence[Path]) -> list[Path]:
        deduplicated: list[Path] = []
        roots = sorted({Path(item) for item in paths}, key=lambda item: len(item.parts))
        for path in roots:
            if any(path == root or path.is_relative_to(root) for root in deduplicated):
                continue
            deduplicated.append(path)
        return deduplicated

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
        env = self._docker_env()
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            input_text=json.dumps(payload),
            env=env,
        )
        session_id = str(payload.get("sessionId") or "").strip() or None
        stdout_text = stdout.strip()
        if returncode != 0:
            if stdout_text:
                try:
                    response_payload = json.loads(stdout_text)
                except json.JSONDecodeError as exc:
                    self._raise_transport_failure(
                        command,
                        action=action,
                        container_id=container_id,
                        session_id=session_id,
                        reason=f"failed with exit code {returncode} and returned invalid JSON",
                        stdout=stdout_text,
                        stderr=stderr,
                        extra_env=extra_env,
                        cause=exc,
                    )
                if isinstance(response_payload, dict):
                    error_payload = response_payload.get("error")
                    if isinstance(error_payload, str) and error_payload.strip():
                        raise RuntimeError(error_payload.strip())
                    if error_payload not in (None, ""):
                        raise RuntimeError(str(error_payload))
                self._raise_transport_failure(
                    command,
                    action=action,
                    container_id=container_id,
                    session_id=session_id,
                    reason=f"failed with exit code {returncode}",
                    stdout=stdout_text,
                    stderr=stderr,
                    extra_env=extra_env,
                )
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason=f"failed with exit code {returncode}",
                stdout=None,
                stderr=stderr,
                extra_env=extra_env,
            )
        if not stdout_text:
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason="returned no JSON output",
                stdout=None,
                stderr=stderr,
                extra_env=extra_env,
            )
        try:
            response_payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason="returned invalid JSON",
                stdout=stdout_text,
                stderr=stderr,
                extra_env=extra_env,
                cause=exc,
            )
        if not isinstance(response_payload, dict):
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason=(
                    f"returned a {type(response_payload).__name__} payload instead "
                    "of a JSON object"
                ),
                stdout=stdout_text,
                stderr=stderr,
                extra_env=extra_env,
            )
        return response_payload

    def _load_runtime_state_payload(
        self,
        *,
        session_workspace_path: str,
    ) -> dict[str, Any] | None:
        state_path = Path(session_workspace_path) / _SESSION_STATE_FILENAME
        if not state_path.is_file():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _recover_send_turn_response(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse | None:
        if self._session_store is None:
            return None
        record = self._session_store.load(request.session_id)
        if record is None:
            return None
        try:
            self._matches_locator(record, request)
        except RuntimeError:
            return None
        state_payload = self._load_runtime_state_payload(
            session_workspace_path=record.session_workspace_path,
        )
        if state_payload is None:
            return None
        if str(state_payload.get("sessionId") or "").strip() != request.session_id:
            return None
        if int(state_payload.get("sessionEpoch") or 0) != request.session_epoch:
            return None
        if str(state_payload.get("containerId") or "").strip() != request.container_id:
            return None
        if str(state_payload.get("logicalThreadId") or "").strip() != request.thread_id:
            return None

        active_turn_id = str(state_payload.get("activeTurnId") or "").strip() or None
        if not active_turn_id:
            return None
        turn_id = active_turn_id
        status = str(state_payload.get("lastTurnStatus") or "").strip().lower()
        if not status:
            status = "running" if active_turn_id else ""
        if status not in {"accepted", "running"}:
            return None

        metadata: dict[str, Any] = {}
        assistant_text = str(state_payload.get("lastAssistantText") or "").strip()
        if assistant_text:
            metadata["assistantText"] = assistant_text
        error_text = str(state_payload.get("lastTurnError") or "").strip()
        if error_text:
            metadata["reason"] = error_text

        return CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
                "activeTurnId": active_turn_id,
            },
            turnId=turn_id,
            status=status,
            metadata=metadata,
        )

    async def _wait_for_terminal_turn_response(
        self,
        *,
        request: SendCodexManagedSessionTurnRequest,
        initial_response: CodexManagedSessionTurnResponse,
    ) -> CodexManagedSessionTurnResponse:
        turn_id = initial_response.turn_id
        locator_payload = self._locator_from_session_state(
            initial_response.session_state
        ).model_dump(by_alias=True)
        deadline = time.monotonic() + self._turn_poll_timeout_seconds
        while True:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="session_status",
                payload=locator_payload,
            )
            handle = CodexManagedSessionHandle.model_validate(payload)
            metadata = dict(handle.metadata)
            turn_id = str(metadata.get("lastTurnId") or turn_id).strip() or turn_id
            last_turn_status = str(metadata.get("lastTurnStatus") or "").strip().lower()
            assistant_text = str(metadata.get("lastAssistantText") or "").strip()
            reason = str(metadata.get("lastTurnError") or "").strip()

            if handle.status == "busy" and handle.session_state.active_turn_id:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "timed out waiting for terminal managed-session turn status "
                        f"after {self._turn_poll_timeout_seconds} seconds"
                    )
                await asyncio.sleep(self._turn_poll_interval_seconds)
                continue

            if handle.status == "failed" or last_turn_status == "failed":
                metadata = {"reason": reason or "turn execution failed"}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="failed",
                    metadata=metadata,
                )

            if handle.status == "interrupted" or last_turn_status == "interrupted":
                metadata = {"reason": reason or "interrupt requested"}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="interrupted",
                    metadata=metadata,
                )

            if handle.status == "ready" and not handle.session_state.active_turn_id:
                metadata = {"assistantText": assistant_text} if assistant_text else {}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="completed",
                    metadata=metadata,
                )

            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "timed out waiting for terminal managed-session turn status "
                    f"after {self._turn_poll_timeout_seconds} seconds"
                )
            await asyncio.sleep(self._turn_poll_interval_seconds)

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
                if self._ready_probe_suggests_container_exited(exc):
                    logs = await self._container_logs_excerpt(container_id)
                    log_detail = f"; logs: {logs}" if logs else ""
                    raise RuntimeError(
                        f"managed session container {container_id} exited before "
                        f"ready: {exc}{log_detail}"
                    ) from exc
            else:
                if payload.get("ready") is True:
                    return
            if self._ready_poll_interval_seconds > 0:
                await asyncio.sleep(self._ready_poll_interval_seconds)
        details = f": {last_error}" if last_error is not None else ""
        raise RuntimeError(
            f"managed session container {container_id} did not become ready{details}"
        )

    @staticmethod
    def _ready_probe_suggests_container_exited(exc: Exception) -> bool:
        detail = str(exc).lower()
        return any(
            marker in detail
            for marker in (
                "container is not running",
                "is not running",
                "not running",
                "exited",
                "is stopped",
                "cannot exec in a stopped",
                "no such container",
                "no such object",
                "not found",
                "container not found",
            )
        )

    async def _container_logs_excerpt(self, container_id: str) -> str:
        command = (
            self._docker_binary,
            "logs",
            "--tail",
            str(_CONTAINER_LOG_EXCERPT_TAIL_LINES),
            container_id,
        )
        returncode, stdout, stderr = await self._command_runner(
            command,
            env=self._docker_env(),
        )
        if returncode != 0:
            return ""
        detail = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        if not detail:
            return ""
        _rendered_command, scrubbed_detail = self._scrub_command_failure(
            command,
            detail,
        )
        return scrubbed_detail[-_CONTAINER_LOG_EXCERPT_MAX_CHARS:]

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
        if self._session_store is not None:
            existing_record = self._session_store.load(request.session_id)
            if (
                existing_record is not None
                and self._request_matches_record(request, existing_record)
                and await self._container_exists(existing_record.container_id)
            ):
                return CodexManagedSessionHandle(
                    sessionState=existing_record.session_state(),
                    status=self._handle_status_from_record_status(
                        existing_record.status
                    ),
                    imageRef=existing_record.image_ref,
                    controlUrl=existing_record.control_url,
                )
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
        ]
        session_environment = dict(request.environment)
        session_environment.pop("GITHUB_TOKEN", None)
        if self._moonmind_url:
            existing_moonmind_url = session_environment.get("MOONMIND_URL")
            if existing_moonmind_url is None or not str(existing_moonmind_url).strip():
                session_environment["MOONMIND_URL"] = self._moonmind_url
        self._apply_unrestricted_docker_session_environment(session_environment)
        github_broker_started = False
        container_secret_environment: dict[str, str] = {}
        try:
            container_secret_environment = await self._configure_session_github_auth(
                request,
                session_environment,
            )
            github_broker_started = "GIT_CONFIG_GLOBAL" in session_environment
        except Exception:
            await self._github_auth_brokers.stop(request.session_id)
            raise
        docker_network = self._network_name or _managed_session_docker_network(
            session_environment
        )
        unrestricted_proxy_network = self._unrestricted_docker_proxy_network(
            session_environment=session_environment,
            docker_network=docker_network,
        )
        if docker_network:
            run_command.extend(["--network", docker_network])
        run_command.extend(
            [
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
        )
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
        for key, value in sorted(session_environment.items()):
            run_command.extend(["-e", f"{key}={value}"])
        for key in sorted(container_secret_environment):
            run_command.extend(["-e", key])
        run_command.extend(
            [
                request.image_ref,
                "python3",
                "-m",
                _RUNTIME_MODULE,
                "serve",
            ]
        )
        try:
            stdout, _stderr = await self._run(
                run_command,
                extra_env=container_secret_environment or None,
            )
            container_id = stdout.strip()
            if not container_id:
                raise RuntimeError("docker run returned a blank container id")
            if unrestricted_proxy_network:
                await self._connect_container_network(
                    container_id=container_id,
                    network_name=unrestricted_proxy_network,
                )
        except Exception:
            if github_broker_started:
                await self._github_auth_brokers.stop(request.session_id)
            raise
        try:
            await self._wait_ready(container_id=container_id)
            container_request = request.model_copy(
                update={"environment": session_environment}
            )
            container_payload = container_request.model_dump(
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
            if github_broker_started:
                await self._github_auth_brokers.stop(request.session_id)
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
        try:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="send_turn",
                payload=request.model_dump(by_alias=True),
            )
            response = CodexManagedSessionTurnResponse.model_validate(payload)
        except RuntimeError:
            recovered = self._recover_send_turn_response(request)
            if recovered is None:
                raise
            response = recovered

        terminal_response = response
        if response.status in {"accepted", "running"}:
            terminal_response = await self._wait_for_terminal_turn_response(
                request=request,
                initial_response=response,
            )

        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                record_metadata = dict(record.metadata)
                assistant_text = terminal_response.metadata.get("assistantText")
                if isinstance(assistant_text, str) and assistant_text.strip():
                    record_metadata.update(_last_assistant_text_metadata(assistant_text))
                updated_record = await self._session_store.update(
                    request.session_id,
                    session_epoch=terminal_response.session_state.session_epoch,
                    container_id=terminal_response.session_state.container_id,
                    thread_id=terminal_response.session_state.thread_id,
                    active_turn_id=terminal_response.session_state.active_turn_id,
                    status=self._record_status_from_turn_status(terminal_response.status),
                    updated_at=datetime.now(tz=UTC),
                    error_message=self._turn_error_message(terminal_response),
                    metadata=record_metadata,
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
                    if terminal_response.status == "completed":
                        await self._emit_session_event(
                            record=updated_record,
                            kind="turn_completed",
                            text=f"Turn completed: {terminal_response.turn_id}.",
                            turn_id=terminal_response.turn_id,
                            active_turn_id=updated_record.active_turn_id,
                            metadata={
                                "action": "send_turn",
                                "assistantText": terminal_response.metadata.get("assistantText"),
                                "reason": request.reason,
                            },
                        )
        return terminal_response

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
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                self._matches_locator(record, request)
                if record.active_turn_id is None and record.status == "ready":
                    return CodexManagedSessionTurnResponse(
                        sessionState=record.session_state(),
                        turnId=request.turn_id,
                        status="interrupted",
                        metadata={"reason": request.reason or "already interrupted"},
                    )
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
                if (
                    previous_record.session_epoch == request.session_epoch + 1
                    and previous_record.container_id == request.container_id
                    and previous_record.thread_id == request.new_thread_id
                    and previous_record.latest_reset_boundary_ref
                ):
                    return CodexManagedSessionHandle(
                        sessionState=previous_record.session_state(),
                        status=self._handle_status_from_record_status(
                            previous_record.status
                        ),
                        imageRef=previous_record.image_ref,
                        controlUrl=previous_record.control_url,
                    )
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
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None and record.status == "terminated":
                self._matches_locator(record, request)
                await self._remove_container(record.container_id, ignore_failure=True)
                await self._github_auth_brokers.stop(request.session_id)
                return CodexManagedSessionHandle(
                    sessionState=record.session_state(),
                    status="terminated",
                    imageRef=record.image_ref,
                    controlUrl=record.control_url,
                )
        await self._remove_container(request.container_id, ignore_failure=True)
        await self._github_auth_brokers.stop(request.session_id)
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
                **dict(record.metadata),
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
                **dict(record.metadata),
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
