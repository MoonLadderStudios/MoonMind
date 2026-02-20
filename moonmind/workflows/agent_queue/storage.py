"""Filesystem storage helpers for queue job artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Union
from uuid import UUID


class AgentQueueArtifactStorage:
    """Stores and resolves artifacts under a job-scoped filesystem root."""

    def __init__(self, artifact_root: Union[str, Path]) -> None:
        self.artifact_root = Path(artifact_root)

    def get_job_path(self, job_id: Union[str, UUID]) -> Path:
        """Return a safe absolute path for a job-specific artifact directory."""

        job_relative = Path(str(job_id))
        if not job_relative.parts:
            raise ValueError("job_id must not be empty")
        if job_relative.is_absolute() or any(
            part == ".." for part in job_relative.parts
        ):
            raise ValueError(
                "job_id must be a relative path without traversal components"
            )

        job_path = (self.artifact_root / job_relative).resolve()
        root_resolved = self.artifact_root.resolve()
        if not job_path.is_relative_to(root_resolved):
            raise ValueError("job path resolves outside artifact root")
        return job_path

    def resolve_artifact_path(
        self, job_id: Union[str, UUID], artifact_name: str
    ) -> Path:
        """Resolve artifact destination under the job directory with traversal checks."""

        artifact_relative = Path(artifact_name)
        if not artifact_relative.parts:
            raise ValueError("artifact name must not be empty")
        if artifact_relative.is_absolute() or any(
            part == ".." for part in artifact_relative.parts
        ):
            raise ValueError(
                "artifact name must be a relative path without traversal components"
            )

        job_path = self.get_job_path(job_id)
        destination = (job_path / artifact_relative).resolve()
        if not destination.is_relative_to(job_path):
            raise ValueError("artifact path resolves outside job directory")
        return destination

    def write_artifact(
        self,
        *,
        job_id: Union[str, UUID],
        artifact_name: str,
        data: bytes,
    ) -> tuple[Path, str]:
        """Write artifact bytes and return absolute path and relative storage path."""

        destination = self.resolve_artifact_path(job_id, artifact_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

        storage_relative = destination.resolve().relative_to(
            self.artifact_root.resolve()
        )
        return destination, storage_relative.as_posix()

    def resolve_storage_path(self, storage_path: str) -> Path:
        """Resolve stored relative path to a safe absolute file path."""

        relative = Path(storage_path)
        if not relative.parts:
            raise ValueError("storage_path must not be empty")
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ValueError(
                "storage_path must be a relative path without traversal components"
            )

        destination = (self.artifact_root / relative).resolve()
        root_resolved = self.artifact_root.resolve()
        if not destination.is_relative_to(root_resolved):
            raise ValueError("storage_path resolves outside artifact root")
        return destination

    def get_state_dir(self, job_id: Union[str, UUID]) -> Path:
        """Return the root directory for job-scoped state artifacts."""

        return self.get_job_path(job_id) / "state"

    def get_step_state_dir(self, job_id: Union[str, UUID]) -> Path:
        """Return the directory containing per-step state JSON files."""

        return self.get_state_dir(job_id) / "steps"

    def get_self_heal_state_dir(self, job_id: Union[str, UUID]) -> Path:
        """Return the directory containing per-attempt self-heal JSON files."""

        return self.get_state_dir(job_id) / "self_heal"

    def get_step_state_path(self, job_id: Union[str, UUID], step_index: int) -> Path:
        """Resolve the JSON file path for a specific step checkpoint."""

        job_path = self.get_job_path(job_id)
        destination = (
            job_path / "state" / "steps" / f"step-{step_index:04d}.json"
        ).resolve()
        if not destination.is_relative_to(job_path):
            raise ValueError("step state path resolves outside job directory")
        return destination

    def get_self_heal_attempt_path(
        self, job_id: Union[str, UUID], step_index: int, attempt: int
    ) -> Path:
        """Resolve the JSON file path for a specific self-heal attempt."""

        job_path = self.get_job_path(job_id)
        destination = (
            job_path
            / "state"
            / "self_heal"
            / f"attempt-{step_index:04d}-{attempt:04d}.json"
        ).resolve()
        if not destination.is_relative_to(job_path):
            raise ValueError("self-heal state path resolves outside job directory")
        return destination
