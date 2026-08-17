"""Artifact port: durable evidence storage boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    async def put(self, key: str, content: bytes) -> str:
        """Store ``content`` and return its durable artifact ref."""
        ...

    async def get(self, ref: str) -> bytes: ...


__all__ = ["ArtifactStore"]
