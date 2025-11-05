"""Workspace helpers for Spec Kit automation runs.

The Spec Kit automation pipeline allocates a shared volume (``speckit_workspaces``)
that is mounted into both Celery workers and ephemeral job containers.  Each run
receives an isolated directory tree rooted at ``/work/runs/<run_id>`` with
dedicated ``repo`` (git checkout), ``home`` (Codex CLI / Spec Kit state), and
``artifacts`` (logs, diffs, summaries) folders as outlined in the feature
specification.  This module centralises path calculations and directory creation
so later orchestration steps can rely on a consistent layout.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import UUID

from moonmind.config.settings import settings

_BRANCH_COMPONENT_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")

logger = logging.getLogger(__name__)

__all__ = [
    "RunWorkspacePaths",
    "SpecWorkspaceManager",
    "WorkspaceConfigurationError",
    "generate_branch_name",
    "sanitize_branch_component",
    "with_retry_suffix",
]


class WorkspaceConfigurationError(RuntimeError):
    """Raised when workspace paths fall outside the configured root."""


@dataclass(frozen=True, slots=True)
class RunWorkspacePaths:
    """Materialised directory layout for a single automation run."""

    run_root: Path
    repo_path: Path
    home_path: Path
    artifacts_path: Path


class SpecWorkspaceManager:
    """Manage run-scoped directories for Spec Kit automation.

    Parameters
    ----------
    workspace_root:
        Root directory shared between the Celery worker and job containers.
        Typically this is the mount point for the ``speckit_workspaces`` Docker
        volume (defaults to ``/work`` in local development).
    runs_dirname:
        Name of the subdirectory under ``workspace_root`` where run folders are
        created.  Defaults to ``"runs"`` to align with the architecture docs.
    """

    RUNS_DIRNAME_DEFAULT = "runs"
    DEFAULT_RETENTION = timedelta(days=7)
    _CONTAINER_PREFIX = "spec-automation-job-"
    _REPO_SUBDIR = "repo"
    _HOME_SUBDIR = "home"
    _ARTIFACTS_SUBDIR = "artifacts"

    def __init__(
        self, workspace_root: Path | str, *, runs_dirname: Optional[str] = None
    ) -> None:
        base = Path(workspace_root).expanduser()
        if not base.is_absolute():
            # Resolve relative paths relative to the current working directory to avoid
            # job containers interpreting them differently.
            base = (Path.cwd() / base).resolve()
        self._workspace_root = base
        self._runs_dirname = runs_dirname or self.RUNS_DIRNAME_DEFAULT

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_settings(cls) -> "SpecWorkspaceManager":
        """Create a manager using the application settings configuration."""

        return cls(settings.spec_workflow.workspace_root)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------
    @property
    def workspace_root(self) -> Path:
        """Return the absolute workspace root path."""

        return self._workspace_root

    @property
    def runs_root(self) -> Path:
        """Return the directory under which individual run folders are stored."""

        return self.workspace_root / self._runs_dirname

    @staticmethod
    def job_container_name(run_id: UUID | str) -> str:
        """Return the deterministic container name for a workflow run."""

        return f"{SpecWorkspaceManager._CONTAINER_PREFIX}{run_id}"

    def run_root(self, run_id: UUID | str) -> Path:
        """Path to the root directory for the provided run identifier."""

        return self.runs_root / str(run_id)

    def repo_path(self, run_id: UUID | str) -> Path:
        """Path to the git checkout directory for a run."""

        return self.run_root(run_id) / self._REPO_SUBDIR

    def home_path(self, run_id: UUID | str) -> Path:
        """Path to the HOME directory exposed to the job container."""

        return self.run_root(run_id) / self._HOME_SUBDIR

    def artifacts_path(self, run_id: UUID | str) -> Path:
        """Path to the artifacts directory used for logs and outputs."""

        return self.run_root(run_id) / self._ARTIFACTS_SUBDIR

    # ------------------------------------------------------------------
    # Directory management
    # ------------------------------------------------------------------
    def ensure_runs_root(self) -> Path:
        """Ensure the ``runs`` directory exists and return it."""

        runs_root = self.runs_root
        self._assert_within_workspace(runs_root)
        runs_root.mkdir(parents=True, exist_ok=True)
        return runs_root

    def ensure_workspace(self, run_id: UUID | str) -> RunWorkspacePaths:
        """Create the run/home/artifact directories if missing.

        Returns a :class:`RunWorkspacePaths` object with the resolved paths so
        orchestrator code can export them to job containers and log producers.
        """

        run_root = self.run_root(run_id)
        repo_path = self.repo_path(run_id)
        home_path = self.home_path(run_id)
        artifacts_path = self.artifacts_path(run_id)

        for path in (run_root, repo_path, home_path, artifacts_path):
            self._assert_within_workspace(path)

        for path in (run_root, repo_path, home_path, artifacts_path):
            path.mkdir(parents=True, exist_ok=True)

        return RunWorkspacePaths(
            run_root=run_root,
            repo_path=repo_path,
            home_path=home_path,
            artifacts_path=artifacts_path,
        )

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------
    def cleanup_workspace(
        self,
        run_id: UUID | str,
        *,
        remove_artifacts: bool = False,
    ) -> None:
        """Remove run directories while respecting artifact retention policies."""

        run_root = self.run_root(run_id)
        artifacts_path = self.artifacts_path(run_id)

        for path in (run_root, artifacts_path):
            self._assert_within_workspace(path)

        if not run_root.exists():
            return

        if remove_artifacts:
            try:
                shutil.rmtree(run_root)
            except FileNotFoundError:
                return
            except OSError as exc:  # pragma: no cover - depends on filesystem state
                logger.warning("Failed to remove workspace %s: %s", run_root, exc)
            return

        artifacts_path.mkdir(parents=True, exist_ok=True)
        for entry in run_root.iterdir():
            if entry == artifacts_path:
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except OSError as exc:  # pragma: no cover - depends on filesystem state
                logger.warning(
                    "Failed to clean workspace entry %s: %s", entry, exc
                )

    def cleanup_job_container(
        self,
        run_id: UUID | str,
        *,
        docker_client: Any = None,
    ) -> bool:
        """Stop and remove the job container associated with ``run_id``."""

        container_name = self.job_container_name(run_id)
        try:
            import docker
            from docker.errors import DockerException, NotFound
        except Exception as exc:  # pragma: no cover - defensive in non-docker envs
            logger.debug(
                "Docker SDK unavailable when cleaning container %s: %s",
                container_name,
                exc,
            )
            return False

        client = docker_client or docker.from_env()
        try:
            container = client.containers.get(container_name)
        except NotFound:
            return False
        except DockerException as exc:  # pragma: no cover - depends on docker env
            logger.warning(
                "Unable to inspect job container %s: %s", container_name, exc
            )
            return False

        try:
            container.remove(force=True)
            logger.info("Removed job container %s", container_name)
            return True
        except NotFound:
            return False
        except DockerException as exc:  # pragma: no cover - depends on docker env
            logger.warning(
                "Failed to remove job container %s: %s", container_name, exc
            )
            return False

    def purge_expired_workspaces(
        self,
        *,
        retention: Optional[timedelta] = None,
        now: Optional[datetime] = None,
        docker_client: Any = None,
    ) -> list[Path]:
        """Permanently delete workspaces older than ``retention``."""

        retention_window = retention or self.DEFAULT_RETENTION
        cutoff = (now or datetime.now(UTC)) - retention_window

        runs_root = self.runs_root
        if not runs_root.exists():
            return []

        removed: list[Path] = []
        for candidate in runs_root.iterdir():
            try:
                self._assert_within_workspace(candidate)
            except WorkspaceConfigurationError:
                continue
            if not candidate.is_dir():
                continue

            try:
                stat = candidate.stat()
            except FileNotFoundError:
                continue

            modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            created = datetime.fromtimestamp(stat.st_ctime, tz=UTC)
            newest = max(modified, created)
            if newest > cutoff:
                continue

            run_id = candidate.name
            self.cleanup_job_container(run_id, docker_client=docker_client)

            try:
                shutil.rmtree(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:  # pragma: no cover - depends on filesystem state
                logger.warning(
                    "Failed to purge expired workspace %s: %s", candidate, exc
                )
                continue

            removed.append(candidate)

        return removed

    def build_job_environment(
        self,
        run_id: UUID | str,
        *,
        repository: str,
        branch_name: str,
        base_branch: str,
        extra_env: Optional[Mapping[str, str]] = None,
    ) -> dict[str, str]:
        """Construct environment variables for the Spec Automation job container."""

        paths = self.ensure_workspace(run_id)
        env: dict[str, str] = {
            "RUN_ID": str(run_id),
            "REPOSITORY": repository,
            "BRANCH": branch_name,
            "BASE_BRANCH": base_branch,
            "WORKSPACE_ROOT": str(paths.run_root),
            "REPO_PATH": str(paths.repo_path),
            "ARTIFACTS_PATH": str(paths.artifacts_path),
            "HOME": str(paths.home_path),
        }
        if extra_env:
            for key, value in extra_env.items():
                env[str(key)] = str(value)
        return env

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def ensure_artifact_file(self, run_id: UUID | str, *relative_parts: str) -> Path:
        """Return a path under the artifacts directory, creating parent folders.

        Parameters
        ----------
        run_id:
            Identifier of the automation run.
        relative_parts:
            Additional path components (e.g., ``("logs", "specify.json")``).
        """

        artifacts_root = self.artifacts_path(run_id)
        target = artifacts_root.joinpath(*relative_parts)
        self._assert_within_workspace(target)
        self._assert_within_workspace(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _assert_within_workspace(self, path: Path) -> None:
        """Ensure ``path`` does not escape the configured workspace root."""

        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise WorkspaceConfigurationError(
                f"Path {resolved} is outside workspace root {self.workspace_root}"
            )


def sanitize_branch_component(component: str) -> str:
    """Return a repository-branch-safe component string."""

    cleaned = _BRANCH_COMPONENT_PATTERN.sub("-", component.strip())
    cleaned = cleaned.strip("-")
    return cleaned.lower() or "run"


def generate_branch_name(
    run_id: UUID | str,
    *,
    prefix: str = "speckit",
    timestamp: Optional[datetime] = None,
    suffix: Optional[str] = None,
) -> str:
    """Generate a deterministic branch name for a Spec Automation run."""

    current = timestamp or datetime.now(UTC)
    date_fragment = current.strftime("%Y%m%d")
    try:
        run_uuid = UUID(str(run_id))
        run_fragment = run_uuid.hex[:8]
    except (ValueError, TypeError):
        run_fragment = sanitize_branch_component(str(run_id))[:8]
    prefix_fragment = sanitize_branch_component(prefix)
    tail = run_fragment
    if suffix:
        tail = f"{tail}-{sanitize_branch_component(suffix)}"
    return f"{prefix_fragment}/{date_fragment}/{tail}"


def with_retry_suffix(branch_name: str, attempt: int) -> str:
    """Append a retry suffix for non-initial attempts while preserving hierarchy."""

    if attempt <= 1:
        return branch_name

    head, sep, tail = branch_name.rpartition("/")
    tail_component = tail or branch_name
    updated_tail = f"{tail_component}-r{attempt}"
    return f"{head}{sep}{updated_tail}" if sep else updated_tail
