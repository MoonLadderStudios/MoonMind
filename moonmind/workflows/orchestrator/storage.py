"""Filesystem utilities for orchestrator artifact management."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID


def _resolve_root(base: Path, configured: str | os.PathLike[str] | None) -> Path:
    """Return a safe artifact root based on ``configured`` or ``base``."""

    root = base.resolve()
    if configured in (None, ""):
        return root

    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactPathError(
            "Configured artifact root escapes the allowed base directory"
        ) from exc

    return candidate


class ArtifactStorageError(Exception):
    """Base class for storage related errors."""


class ArtifactPathError(ArtifactStorageError):
    """Raised when a caller attempts to traverse outside the artifact root."""


@dataclass(slots=True)
class ArtifactWriteResult:
    """Metadata returned after writing an artifact to disk."""

    path: str
    size_bytes: int
    checksum: str


def resolve_artifact_root(
    default_base: os.PathLike[str] | str, override: str | os.PathLike[str] | None
) -> Path:
    """Return a sanitized artifact root."""

    base = Path(default_base)
    return _resolve_root(base, override)


class ArtifactStorage:
    """Manage orchestrator artifact directories under a configurable root."""

    def __init__(self, base_path: os.PathLike[str] | str) -> None:
        self._base_path = _resolve_root(Path(base_path), None)

    @property
    def base_path(self) -> Path:
        """Return the configured artifact root."""

        return self._base_path

    def ensure_run_directory(self, run_id: UUID) -> Path:
        """Create the artifact directory for ``run_id`` if it does not exist."""

        run_path = self._base_path / str(run_id)
        run_path.mkdir(parents=True, exist_ok=True)
        return run_path

    def resolve_path(self, run_id: UUID, relative_path: str) -> Path:
        """Resolve ``relative_path`` within the run directory ensuring confinement."""

        if Path(relative_path).is_absolute():
            raise ArtifactPathError("Artifact paths must be relative")

        run_path = self.ensure_run_directory(run_id)
        if run_path.is_symlink():
            raise ArtifactPathError("Artifact run directory cannot be a symlink")

        run_root = run_path.resolve()
        candidate = (run_path / relative_path).resolve()
        try:
            candidate.relative_to(run_root)
        except ValueError as exc:
            raise ArtifactPathError("Artifact path escapes the run directory") from exc

        for ancestor in candidate.parents:
            if ancestor == run_root:
                break
            if ancestor.is_symlink():
                raise ArtifactPathError("Artifact path traverses a symbolic link")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def write_bytes(
        self,
        run_id: UUID,
        relative_path: str,
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> ArtifactWriteResult:
        """Write ``data`` to ``relative_path`` returning artifact metadata."""

        target = self.resolve_path(run_id, relative_path)
        if not overwrite and target.exists():
            raise ArtifactStorageError(f"Artifact '{relative_path}' already exists")
        target.write_bytes(data)
        return self._build_result(run_id, target)

    def write_text(
        self,
        run_id: UUID,
        relative_path: str,
        text: str,
        *,
        encoding: str = "utf-8",
        overwrite: bool = True,
    ) -> ArtifactWriteResult:
        """Write ``text`` to ``relative_path`` using ``encoding``."""

        target = self.resolve_path(run_id, relative_path)
        if not overwrite and target.exists():
            raise ArtifactStorageError(f"Artifact '{relative_path}' already exists")
        target.write_text(text, encoding=encoding)
        return self._build_result(run_id, target)

    def write_stream(
        self,
        run_id: UUID,
        relative_path: str,
        stream: BinaryIO,
        *,
        chunk_size: int = 65536,
        overwrite: bool = True,
    ) -> ArtifactWriteResult:
        """Write from ``stream`` into the artifact path."""

        target = self.resolve_path(run_id, relative_path)
        if not overwrite and target.exists():
            raise ArtifactStorageError(f"Artifact '{relative_path}' already exists")
        with target.open("wb") as handle:
            for chunk in iter(lambda: stream.read(chunk_size), b""):
                handle.write(chunk)
        return self._build_result(run_id, target)

    def checksum(self, run_id: UUID, relative_path: str) -> str:
        """Return the SHA256 checksum of a stored artifact."""

        path = self.resolve_path(run_id, relative_path)
        if not path.exists():
            raise ArtifactStorageError(f"Artifact '{relative_path}' does not exist")
        return self._compute_checksum(path)

    def _build_result(self, run_id: UUID, path: Path) -> ArtifactWriteResult:
        checksum = self._compute_checksum(path)
        size_bytes = path.stat().st_size
        relative = path.relative_to(self.ensure_run_directory(run_id))
        return ArtifactWriteResult(
            path=str(relative), size_bytes=size_bytes, checksum=checksum
        )

    @staticmethod
    def _compute_checksum(path: Path, *, algorithm: str = "sha256") -> str:
        hasher = hashlib.new(algorithm)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()


__all__ = [
    "ArtifactStorage",
    "ArtifactStorageError",
    "ArtifactPathError",
    "ArtifactWriteResult",
    "resolve_artifact_root",
]
