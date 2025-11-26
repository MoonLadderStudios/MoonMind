import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict


class ArtifactStorage:
    """
    Manages artifacts for Spec Kit workflow runs.
    """

    def __init__(self, artifact_root: str):
        self.artifact_root = Path(artifact_root)

    def get_run_path(self, run_id: str) -> Path:
        """
        Returns the artifact path for a given run ID.
        """
        run_relative = Path(str(run_id))

        if not run_relative.parts:
            raise ValueError("run_id must not be empty")

        if run_relative.is_absolute() or any(
            part == ".." for part in run_relative.parts
        ):
            raise ValueError(
                "run_id must be a relative path without traversal components"
            )

        run_path = (self.artifact_root / run_relative).resolve()
        artifact_root_resolved = self.artifact_root.resolve()

        if not run_path.is_relative_to(artifact_root_resolved):
            raise ValueError("run_id resolves outside the artifact root directory")

        return run_path

    def store_artifact(
        self, run_id: str, file_path: Path, artifact_name: str
    ) -> Dict[str, Any]:
        """
        Stores an artifact and returns its metadata.
        """
        run_path = self.get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        artifact_relative = Path(artifact_name)
        if not artifact_relative.parts:
            raise ValueError("artifact_name must not be empty")

        if artifact_relative.is_absolute() or any(
            part == ".." for part in artifact_relative.parts
        ):
            raise ValueError(
                "artifact_name must be a relative path without traversal components"
            )

        destination = (run_path / artifact_relative).resolve()
        run_path_resolved = run_path.resolve()

        if not destination.is_relative_to(run_path_resolved):
            raise ValueError("artifact_name resolves outside the run directory")

        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy(file_path, destination)

        return self.get_artifact_metadata(destination)

    def write_json_artifact(
        self, run_id: str, artifact_name: str, payload: dict[str, Any]
    ) -> Dict[str, Any]:
        """Serialize ``payload`` into the run directory and return its metadata."""

        run_path = self.get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        artifact_relative = Path(artifact_name)
        if not artifact_relative.parts:
            raise ValueError("artifact_name must not be empty")

        if artifact_relative.is_absolute() or any(
            part == ".." for part in artifact_relative.parts
        ):
            raise ValueError(
                "artifact_name must be a relative path without traversal components"
            )

        destination = (run_path / artifact_relative).resolve()
        if not destination.parent.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)

        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.get_artifact_metadata(destination)

    def get_artifact_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Returns metadata for a given artifact file.
        """
        if not file_path.exists():
            return {}

        stat = file_path.stat()
        return {
            "name": file_path.name,
            "path": str(file_path),
            "size": stat.st_size,
            "created_at": stat.st_ctime,
            "modified_at": stat.st_mtime,
            "digest": self._calculate_digest(file_path),
        }

    def _calculate_digest(self, file_path: Path, algorithm: str = "sha256") -> str:
        """
        Calculates the digest of a file.
        """
        hasher = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return f"{algorithm}:{hasher.hexdigest()}"
