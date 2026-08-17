"""Publishing port: repository/result publication boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PublishResult:
    published: bool
    ref: str | None = None


@runtime_checkable
class Publisher(Protocol):
    async def publish(
        self, bridge_session_id: str, *, workspace_id: str
    ) -> PublishResult: ...


__all__ = ["PublishResult", "Publisher"]
