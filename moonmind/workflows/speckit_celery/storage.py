import hashlib
import os
from pathlib import Path
from typing import Dict, Any

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
        return self.artifact_root / str(run_id)

    def store_artifact(self, run_id: str, file_path: Path, artifact_name: str) -> Dict[str, Any]:
        """
        Stores an artifact and returns its metadata.
        """
        run_path = self.get_run_path(run_id)
        run_path.mkdir(parents=True, exist_ok=True)

        destination = run_path / artifact_name

        # For now, we'll just copy the file. In a real scenario, this might be a move or upload.
        import shutil
        shutil.copy(file_path, destination)

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
