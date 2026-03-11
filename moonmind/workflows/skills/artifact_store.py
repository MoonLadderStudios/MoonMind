"""Artifact storage primitives for skill and plan execution contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .tool_plan_contracts import ARTIFACT_REF_PREFIX, ArtifactRef


class ArtifactStoreError(RuntimeError):
    """Raised when artifacts cannot be read or written."""


class ArtifactStore(Protocol):
    """Minimal artifact store interface used by plan/skill runtime modules."""

    def put_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        pass

    def get_bytes(self, artifact_ref: str) -> bytes:
        pass

    def put_json(
        self,
        payload: Mapping[str, Any] | list[Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        pass

    def get_json(self, artifact_ref: str) -> Any:
        pass


@dataclass(slots=True)
class InMemoryArtifactStore:
    """Simple in-memory artifact store for tests and local execution."""

    _data: dict[str, bytes]
    _meta: dict[str, ArtifactRef]

    def __init__(self) -> None:
        self._data = {}
        self._meta = {}

    @staticmethod
    def _build_ref(payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        return f"{ARTIFACT_REF_PREFIX}{digest}"

    def put_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        artifact_ref = self._build_ref(payload)
        existing = self._data.get(artifact_ref)
        if existing is not None and existing != payload:
            raise ArtifactStoreError(
                f"Artifact immutability violated for {artifact_ref}: payload mismatch"
            )
        self._data[artifact_ref] = payload

        artifact = ArtifactRef.create(
            artifact_ref=artifact_ref,
            content_type=content_type,
            bytes=len(payload),
            metadata=dict(metadata or {}),
        )
        self._meta.setdefault(artifact_ref, artifact)
        return self._meta[artifact_ref]

    def get_bytes(self, artifact_ref: str) -> bytes:
        try:
            return self._data[artifact_ref]
        except KeyError as exc:
            raise ArtifactStoreError(f"Artifact not found: {artifact_ref}") from exc

    def put_json(
        self,
        payload: Mapping[str, Any] | list[Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.put_bytes(
            encoded,
            content_type="application/json",
            metadata=metadata,
        )

    def get_json(self, artifact_ref: str) -> Any:
        raw = self.get_bytes(artifact_ref)
        return json.loads(raw.decode("utf-8"))


@dataclass(slots=True)
class FileArtifactStore:
    """Filesystem-backed immutable artifact store.

    Artifacts are keyed by sha256 digest and stored as:
    - ``<root>/<sha256>.bin`` (raw payload)
    - ``<root>/<sha256>.meta.json`` (metadata sidecar)
    """

    root: Path
    _DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths_for_ref(self, artifact_ref: str) -> tuple[Path, Path]:
        if not artifact_ref.startswith(ARTIFACT_REF_PREFIX):
            raise ArtifactStoreError(f"Unsupported artifact ref: {artifact_ref}")
        digest = artifact_ref.removeprefix(ARTIFACT_REF_PREFIX)
        if not self._DIGEST_PATTERN.fullmatch(digest):
            raise ArtifactStoreError(f"Unsupported artifact ref: {artifact_ref}")
        return self.root / f"{digest}.bin", self.root / f"{digest}.meta.json"

    @staticmethod
    def _build_ref(payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        return f"{ARTIFACT_REF_PREFIX}{digest}"

    def put_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        artifact_ref = self._build_ref(payload)
        payload_path, meta_path = self._paths_for_ref(artifact_ref)

        if payload_path.exists():
            existing = payload_path.read_bytes()
            if existing != payload:
                raise ArtifactStoreError(
                    f"Artifact immutability violated for {artifact_ref}: payload mismatch"
                )
        else:
            payload_path.write_bytes(payload)

        if meta_path.exists():
            try:
                stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return ArtifactRef(
                    artifact_ref=str(stored_meta["artifact_ref"]),
                    content_type=str(stored_meta["content_type"]),
                    bytes=int(stored_meta["bytes"]),
                    created_at=str(stored_meta["created_at"]),
                    metadata=dict(stored_meta.get("metadata") or {}),
                )
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise ArtifactStoreError(
                    f"Artifact metadata is corrupt for {artifact_ref}: {exc}"
                ) from exc

        artifact = ArtifactRef.create(
            artifact_ref=artifact_ref,
            content_type=content_type,
            bytes=len(payload),
            metadata=dict(metadata or {}),
        )
        meta_path.write_text(
            json.dumps(artifact.to_payload(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return artifact

    def get_bytes(self, artifact_ref: str) -> bytes:
        payload_path, _ = self._paths_for_ref(artifact_ref)
        if not payload_path.is_file():
            raise ArtifactStoreError(f"Artifact not found: {artifact_ref}")
        return payload_path.read_bytes()

    def put_json(
        self,
        payload: Mapping[str, Any] | list[Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.put_bytes(
            encoded,
            content_type="application/json",
            metadata=metadata,
        )

    def get_json(self, artifact_ref: str) -> Any:
        raw = self.get_bytes(artifact_ref)
        return json.loads(raw.decode("utf-8"))


__all__ = [
    "ArtifactStore",
    "ArtifactStoreError",
    "FileArtifactStore",
    "InMemoryArtifactStore",
]
