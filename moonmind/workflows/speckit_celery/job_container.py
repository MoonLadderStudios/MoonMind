"""Docker job container lifecycle management for Spec Kit automation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Optional, Sequence
from uuid import UUID

import docker
from docker.errors import APIError, DockerException, NotFound

from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_COMMAND: tuple[str, ...] = ("sleep", "infinity")
_DEFAULT_VOLUME_NAME = os.getenv(
    "SPEC_AUTOMATION_WORKSPACE_VOLUME", "speckit_workspaces"
)


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
        container_name = name or f"spec-automation-job-{run_ref}"
        effective_image = image or settings.spec_workflow.job_image
        effective_command = command or _DEFAULT_COMMAND
        env_map: dict[str, str] = dict(environment or {})
        env_map.setdefault(
            "HOME", f"{settings.spec_workflow.workspace_root}/runs/{run_ref}/home"
        )

        volume_mounts = (
            volumes
            if volumes is not None
            else {
                _DEFAULT_VOLUME_NAME: {
                    "bind": settings.spec_workflow.workspace_root,
                    "mode": "rw",
                }
            }
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
            )
            raise JobContainerStartError(str(exc)) from exc
        except DockerException as exc:  # pragma: no cover
            logger.error(
                "Docker error while starting job container for run %s: %s", run_id, exc
            )
            raise JobContainerStartError(str(exc)) from exc

        logger.info(
            "Started Spec Automation job container",
            extra={"run_id": run_ref, "container_id": container.id},
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
