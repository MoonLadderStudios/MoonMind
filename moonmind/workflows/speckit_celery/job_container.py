"""Docker job container lifecycle management for Spec Kit automation."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Optional, Sequence
from uuid import UUID

import docker
from docker.errors import APIError, DockerException, NotFound

from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery.workspace import SpecWorkspaceManager

logger = logging.getLogger(__name__)

_DEFAULT_COMMAND: tuple[str, ...] = ("sleep", "infinity")
_DEFAULT_VOLUME_NAME = os.getenv(
    "SPEC_AUTOMATION_WORKSPACE_VOLUME", "speckit_workspaces"
)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|key|credential|auth|cookie|session)", re.IGNORECASE
)
_SECRET_ENV_KEYS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "CODEX_API_KEY",
)
_SECRET_ENV_PREFIXES: tuple[str, ...] = (
    "CODEX_",
    "GIT_AUTHOR_",
    "GIT_COMMITTER_",
    "SPEC_AUTOMATION_SECRET_",
    "SPEC_WORKFLOW_SECRET_",
    "GITHUB_",
    "GH_",
)
_REDACTED = "***REDACTED***"
_SECRET_ENV_KEYS_UPPER = tuple(key.upper() for key in _SECRET_ENV_KEYS)
_SECRET_ENV_PREFIXES_UPPER = tuple(prefix.upper() for prefix in _SECRET_ENV_PREFIXES)
_NON_SECRET_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_TIME",
        "LC_NUMERIC",
        "LC_COLLATE",
        "PWD",
        "USER",
        "LOGNAME",
        "TERM",
        "HOSTNAME",
    }
)


def _collect_secret_environment(
    runtime_environment: Optional[Mapping[str, object]] = None,
) -> dict[str, str]:
    """Return environment variables containing sensitive credentials."""

    collected: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SECRET_ENV_KEYS or any(
            key.startswith(prefix) for prefix in _SECRET_ENV_PREFIXES
        ):
            collected[key] = value

    github_token = settings.spec_workflow.github_token
    if github_token:
        collected["GITHUB_TOKEN"] = github_token

    codex_env = settings.spec_workflow.codex_environment
    if codex_env:
        collected["CODEX_ENV"] = codex_env

    codex_model = settings.spec_workflow.codex_model
    if codex_model:
        collected["CODEX_MODEL"] = codex_model

    codex_profile = settings.spec_workflow.codex_profile
    if codex_profile:
        collected["CODEX_PROFILE"] = codex_profile

    runtime_keys = getattr(settings.spec_workflow, "agent_runtime_env_keys", ())
    for candidate in runtime_keys or ():
        key = str(candidate).strip()
        if not key or key in collected:
            continue
        value = os.getenv(key)
        if value is not None:
            collected[key] = value

    if runtime_environment:
        for raw_key, raw_value in runtime_environment.items():
            key = str(raw_key).strip()
            if not key or raw_value is None:
                continue
            collected[key] = str(raw_value)

    return collected


def _should_redact_key(key: str) -> bool:
    """Return ``True`` when ``key`` is likely to reference a secret."""

    upper = key.upper()
    if upper in _NON_SECRET_ENV_KEYS:
        return False
    if upper in _SECRET_ENV_KEYS_UPPER:
        return True
    if any(upper.startswith(prefix) for prefix in _SECRET_ENV_PREFIXES_UPPER):
        return True
    if _SENSITIVE_KEY_PATTERN.search(key):
        return True
    return True


def _redact_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``environment`` with sensitive keys redacted."""

    sanitized: dict[str, str] = {}
    for key, value in environment.items():
        if _should_redact_key(key):
            sanitized[key] = _REDACTED
        else:
            sanitized[key] = value
    return sanitized


class JobContainerError(RuntimeError):
    """Base exception raised for job container lifecycle failures."""


class JobContainerStartError(JobContainerError):
    """Raised when a job container cannot be started."""


class JobContainerExecError(JobContainerError):
    """Raised when execution inside the job container fails unexpectedly."""


@dataclass(frozen=True, slots=True)
class ContainerExecResult:
    """Result returned after running a command inside the job container."""

    exit_code: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        """Return the execution duration in seconds."""

        return (self.finished_at - self.started_at).total_seconds()


class JobContainer:
    """Wrapper around a Docker container used for Spec Kit automation runs."""

    def __init__(
        self, client: docker.DockerClient, container_id: str, name: str
    ) -> None:
        self._client = client
        self._container_id = container_id
        self._name = name

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        """Return the Docker container identifier."""

        return self._container_id

    @property
    def name(self) -> str:
        """Return the Docker container name."""

        return self._name

    # ------------------------------------------------------------------
    # Exec helpers
    # ------------------------------------------------------------------
    def exec(
        self,
        command: Sequence[str] | str,
        *,
        environment: Optional[Mapping[str, str]] = None,
        workdir: Optional[str] = None,
        user: Optional[str] = None,
        demux: bool = True,
    ) -> ContainerExecResult:
        """Execute ``command`` inside the container returning captured output."""

        try:
            container = self._client.containers.get(self._container_id)
        except NotFound as exc:  # pragma: no cover - defensive; requires real Docker
            raise JobContainerExecError(
                f"Container {self._container_id} is not available for exec"
            ) from exc
        except DockerException as exc:  # pragma: no cover - docker SDK errors
            raise JobContainerExecError(
                "Failed to access job container for exec"
            ) from exc

        started_at = datetime.now(UTC)
        try:
            result = container.exec_run(  # type: ignore[call-arg]
                command,
                stdout=True,
                stderr=True,
                demux=demux,
                environment=dict(environment or {}),
                workdir=workdir,
                user=user,
            )
        except DockerException as exc:  # pragma: no cover - docker SDK errors
            raise JobContainerExecError("Docker exec invocation failed") from exc

        finished_at = datetime.now(UTC)

        stdout_bytes: bytes = b""
        stderr_bytes: bytes = b""
        if demux and isinstance(result.output, tuple):
            stdout_bytes = result.output[0] or b""
            stderr_bytes = result.output[1] or b""
        else:
            data = result.output or b""
            stdout_bytes = data

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return ContainerExecResult(
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            finished_at=finished_at,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def stop(self, *, remove: bool = True, timeout: int = 10) -> None:
        """Stop (and optionally remove) the job container."""

        try:
            container = self._client.containers.get(self._container_id)
        except NotFound:
            return
        except DockerException as exc:  # pragma: no cover - docker SDK errors
            logger.warning(
                "Failed to load job container %s: %s", self._container_id, exc
            )
            return

        try:
            container.stop(timeout=timeout)
        except NotFound:
            container = None
        except APIError as exc:  # pragma: no cover - depends on Docker daemon
            logger.warning(
                "Error stopping job container %s: %s", self._container_id, exc
            )
        except DockerException as exc:  # pragma: no cover
            logger.warning(
                "Docker error stopping container %s: %s", self._container_id, exc
            )

        if not remove:
            return

        try:
            (container or self._client.containers.get(self._container_id)).remove(
                force=True
            )
        except NotFound:
            return
        except DockerException as exc:  # pragma: no cover
            logger.warning(
                "Failed to remove job container %s: %s", self._container_id, exc
            )


class JobContainerManager:
    """Manage lifecycle of job containers used in Spec Kit automation runs."""

    def __init__(self, *, docker_client: Optional[docker.DockerClient] = None) -> None:
        self._client = docker_client or docker.from_env()

    # ------------------------------------------------------------------
    # Container creation
    # ------------------------------------------------------------------
    def start(
        self,
        run_id: UUID | str,
        *,
        image: Optional[str] = None,
        command: Optional[Sequence[str] | str] = None,
        environment: Optional[Mapping[str, str]] = None,
        runtime_environment: Optional[Mapping[str, object]] = None,
        volumes: Optional[Mapping[str, Mapping[str, str]]] = None,
        workdir: Optional[str] = None,
        labels: Optional[Mapping[str, str]] = None,
        network: Optional[str] = None,
        name: Optional[str] = None,
        user: Optional[str] = None,
        auto_remove: bool = False,
        cleanup_existing: bool = True,
    ) -> JobContainer:
        """Start a detached job container and return its wrapper instance."""

        run_ref = str(run_id)

        codex_path = shutil.which("codex")
        if not codex_path or not os.access(codex_path, os.X_OK):
            message = (
                "Codex CLI is not available on PATH; rebuild the automation image "
                "to include the bundled CLI."
            )
            logger.error(
                "Cannot start job container for run %s because Codex CLI is missing",
                run_id,
                extra={"run_id": run_ref, "codex_path": codex_path},
            )
            raise JobContainerStartError(message)
        logger.debug(
            "Verified Codex CLI binary for job container start",
            extra={"run_id": run_ref, "codex_path": codex_path},
        )

        container_name = name or SpecWorkspaceManager.job_container_name(run_ref)
        effective_image = image or settings.spec_workflow.job_image
        effective_command = command or _DEFAULT_COMMAND
        env_map: dict[str, str] = dict(environment or {})
        env_map.update(
            _collect_secret_environment(runtime_environment=runtime_environment)
        )
        env_map.setdefault(
            "HOME", f"{settings.spec_workflow.workspace_root}/runs/{run_ref}/home"
        )
        redacted_env = _redact_environment(env_map)

        if volumes is None:
            volume_mounts: dict[str, Mapping[str, str]] = {
                _DEFAULT_VOLUME_NAME: {
                    "bind": settings.spec_workflow.workspace_root,
                    "mode": "rw",
                }
            }
        else:
            volume_mounts = {key: dict(value) for key, value in volumes.items()}

        codex_volume = settings.spec_workflow.codex_volume_name
        if codex_volume:
            codex_target = os.path.join(env_map["HOME"], ".codex")
            volume_mounts.setdefault(
                codex_volume,
                {
                    "bind": codex_target,
                    "mode": "ro",
                },
            )

        if cleanup_existing:
            self._cleanup_existing(container_name)

        try:
            container = self._client.containers.run(
                effective_image,
                command=effective_command,
                name=container_name,
                detach=True,
                tty=False,
                environment=dict(env_map),
                volumes={key: dict(value) for key, value in volume_mounts.items()},
                working_dir=workdir,
                labels=dict(labels or {}),
                network=network,
                user=user,
                auto_remove=auto_remove,
            )
        except APIError as exc:  # pragma: no cover - depends on Docker daemon
            logger.error(
                "Failed to start job container for run %s using image %s: %s",
                run_id,
                effective_image,
                exc,
                extra={
                    "run_id": run_ref,
                    "container_name": container_name,
                    "environment": redacted_env,
                },
            )
            raise JobContainerStartError(str(exc)) from exc
        except DockerException as exc:  # pragma: no cover
            logger.error(
                "Docker error while starting job container for run %s: %s",
                run_id,
                exc,
                extra={
                    "run_id": run_ref,
                    "container_name": container_name,
                    "environment": redacted_env,
                },
            )
            raise JobContainerStartError(str(exc)) from exc

        logger.info(
            "Started Spec Automation job container",
            extra={
                "run_id": run_ref,
                "container_id": container.id,
                "container_name": container.name,
                "environment": redacted_env,
            },
        )
        return JobContainer(self._client, container.id, container.name)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _cleanup_existing(self, name: str) -> None:
        """Remove an existing container with the provided name if it exists."""

        try:
            existing = self._client.containers.get(name)
        except NotFound:
            return
        except DockerException as exc:  # pragma: no cover
            logger.warning("Unable to inspect existing job container %s: %s", name, exc)
            return

        try:
            existing.remove(force=True)
        except DockerException as exc:  # pragma: no cover
            logger.warning("Failed to remove stale job container %s: %s", name, exc)


__all__ = [
    "JobContainer",
    "JobContainerError",
    "JobContainerExecError",
    "JobContainerManager",
    "JobContainerStartError",
    "ContainerExecResult",
]
