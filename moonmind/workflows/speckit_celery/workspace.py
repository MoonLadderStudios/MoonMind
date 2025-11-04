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

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from moonmind.config.settings import settings

__all__ = [
    "RunWorkspacePaths",
    "SpecWorkspaceManager",
    "WorkspaceConfigurationError",
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
