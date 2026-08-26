"""Image acquisition coordination for the Docker container-job backend.

Implements the normalization, failure classification, digest parsing, and
cross-worker per-image acquisition lease used by the trusted ``docker-engine``
backend to satisfy MoonLadderStudios/MoonMind#3256:

- normalize an image reference into a stable identity for locking/diagnostics;
- derive a bounded, backend-scoped lock key from that identity;
- serialize acquisition of one missing image per normalized identity on a
  selected backend without serializing unrelated images;
- expire and recover a lease whose owning worker died;
- classify pull failures into granular, non-secret failure classes.

The lock is intentionally an injectable protocol so callers can supply a
deterministic implementation in tests. The default implementation uses an
atomic filesystem lease on a deployment-shared path, which matches the
system-Docker cache scope (workers that share the daemon share the volume).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from moonmind.schemas.container_job_models import ContainerJobFailureClass

_DEFAULT_REGISTRY = "docker.io"
_DEFAULT_TAG = "latest"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

# Image failure classes that will not resolve by retrying the same request.
_TERMINAL_IMAGE_FAILURES: frozenset[ContainerJobFailureClass] = frozenset(
    {
        ContainerJobFailureClass.IMAGE_NOT_FOUND,
        ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED,
        ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH,
        ContainerJobFailureClass.IMAGE_BUILD_NOT_CONFIGURED,
        ContainerJobFailureClass.IMAGE_BUILD_INPUTS_UNAVAILABLE,
        ContainerJobFailureClass.IMAGE_VALIDATION_FAILED,
    }
)


@dataclass(frozen=True)
class NormalizedImage:
    """Canonical registry/repository/reference identity of a requested image."""

    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @property
    def identity(self) -> str:
        base = f"{self.registry}/{self.repository}"
        if self.digest:
            return f"{base}@{self.digest}"
        return f"{base}:{self.tag or _DEFAULT_TAG}"


def normalize_image_reference(image: str) -> NormalizedImage:
    """Normalize a Docker image reference into a stable identity.

    Applies Docker's default-registry, official-``library``-namespace, and
    default-``latest``-tag rules so ``python`` and ``docker.io/library/python:latest``
    lock and observe as the same identity.
    """

    ref = image.strip()
    if not ref:
        raise ValueError("image reference must not be empty")

    digest: str | None = None
    if "@" in ref:
        ref, _, digest_part = ref.partition("@")
        digest = digest_part.strip() or None

    first, slash, rest = ref.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry = first
        remainder = rest
    else:
        registry = _DEFAULT_REGISTRY
        remainder = ref

    name = remainder
    tag: str | None = None
    last_slash = remainder.rfind("/")
    colon = remainder.find(":", last_slash + 1)
    if colon != -1:
        name = remainder[:colon]
        tag = remainder[colon + 1 :] or None

    if not name:
        raise ValueError("image reference must include a repository")

    if registry == _DEFAULT_REGISTRY and "/" not in name:
        name = f"library/{name}"

    if digest is None and tag is None:
        tag = _DEFAULT_TAG

    return NormalizedImage(registry=registry, repository=name, tag=tag, digest=digest)


def image_lock_key(backend_ref: str, normalized: NormalizedImage) -> str:
    """Return a bounded, backend-scoped lock key for a normalized image.

    The backend reference is part of the key so a future endpoint change cannot
    reuse another backend's lease or report a false cache observation.
    """

    raw = f"{backend_ref}\n{normalized.identity}".encode()
    return sha256(raw).hexdigest()


def parse_resolved_digest(repo_digests: str, image_id: str) -> str | None:
    """Return the exact ``sha256:`` digest for launch reproducibility.

    Prefers a registry repo digest (``repo@sha256:...``); falls back to the
    image config id when it is itself a content digest. Returns ``None`` when no
    digest is observable (for example a locally built image without repo digests).
    """

    for candidate in re.split(r"[,\s]+", repo_digests or ""):
        candidate = candidate.strip()
        if not candidate:
            continue
        _, _, digest = candidate.partition("@")
        match = _SHA256.fullmatch(digest.strip())
        if match:
            return match.group(0)
    match = _SHA256.fullmatch((image_id or "").strip())
    return match.group(0) if match else None


def classify_pull_failure(stderr: str) -> ContainerJobFailureClass:
    """Classify a ``docker pull`` failure into a granular, non-secret class."""

    text = (stderr or "").lower()
    if any(
        token in text
        for token in ("no matching manifest", "platform", "does not match")
    ):
        return ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH
    if any(
        token in text
        for token in (
            "cannot connect to the docker daemon",
            "is the docker daemon running",
            "connection refused",
            "dial tcp",
        )
    ):
        return ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE
    if any(
        token in text
        for token in ("timeout", "timed out", "deadline exceeded", "context deadline")
    ):
        return ContainerJobFailureClass.IMAGE_PULL_TIMEOUT
    if any(
        token in text
        for token in ("unauthorized", "authentication required", "docker login", "denied")
    ):
        return ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED
    if any(
        token in text
        for token in (
            "not found",
            "manifest unknown",
            "manifest for",
            "repository does not exist",
        )
    ):
        return ContainerJobFailureClass.IMAGE_NOT_FOUND
    return ContainerJobFailureClass.IMAGE


class ImageAcquisitionError(RuntimeError):
    """Raised when image acquisition cannot satisfy the requested pull policy."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: ContainerJobFailureClass,
        diagnostics_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.diagnostics_ref = diagnostics_ref

    @property
    def terminal(self) -> bool:
        """Whether retrying the same request cannot change the outcome."""

        return self.failure_class in _TERMINAL_IMAGE_FAILURES


class ImageAcquisitionLock(Protocol):
    """Cross-worker per-image acquisition lease abstraction."""

    async def try_acquire(
        self, key: str, *, ttl_seconds: float, owner_id: str
    ) -> bool:
        """Attempt to become the single pull owner for ``key``.

        Returns ``True`` when this caller now owns the lease. An existing lease
        whose deadline has passed is reclaimed so a dead owner never blocks
        later jobs permanently.
        """

    async def release(self, key: str, owner_id: str) -> None:
        """Release ``key`` only if still owned by ``owner_id``."""


class FilesystemImageAcquisitionLock:
    """Atomic filesystem lease keyed by backend-scoped normalized image identity.

    Ownership is claimed with ``O_CREAT | O_EXCL`` so exactly one worker wins a
    race. A lease records its owner and an expiry deadline; an expired lease is
    reclaimed. The lease is never treated as proof that the image is present —
    callers must re-inspect the daemon after acquiring or waiting.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._clock = clock

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.lease"

    async def try_acquire(
        self, key: str, *, ttl_seconds: float, owner_id: str
    ) -> bool:
        now = self._clock()
        path = self._path(key)
        payload = json.dumps(
            {"owner": owner_id, "expiresAt": now + ttl_seconds}
        ).encode()

        def _attempt() -> bool:
            self._root.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._lease_active(path, now):
                    return False
                # Reclaim an expired/abandoned lease. A rare double-reclaim race
                # is safe: correctness relies on re-inspection, not the lease.
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    # Another contender already reclaimed the expired lease.
                    pass
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    return False
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            return True

        return await asyncio.to_thread(_attempt)

    async def release(self, key: str, owner_id: str) -> None:
        path = self._path(key)

        def _attempt() -> None:
            record = self._read(path)
            if record is not None and record.get("owner") == owner_id:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    # Another worker already released or reclaimed this lease.
                    pass

        await asyncio.to_thread(_attempt)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    @classmethod
    def _lease_active(cls, path: Path, now: float) -> bool:
        record = cls._read(path)
        if record is None:
            try:
                # A creator may have made the file but not written its payload.
                # Preserve that atomic claim during this short initialization gap.
                return (now - path.stat().st_mtime) < 10.0
            except OSError:
                return False
        try:
            return float(record.get("expiresAt", 0)) > now
        except (TypeError, ValueError):
            return False


ProgressPublisher = Callable[[str, bytes], Awaitable[str | None]]
