"""Host port: launch and release of an execution host (Docker/Compose)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class HostHandle:
    host_id: str
    endpoint: str
    metadata: Mapping[str, str]


@runtime_checkable
class HostLauncher(Protocol):
    async def launch(
        self, bridge_session_id: str, *, image: str
    ) -> HostHandle: ...

    async def release(self, host_id: str) -> None: ...


__all__ = ["HostHandle", "HostLauncher"]
