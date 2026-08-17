"""Lease port: bounded, fenced ownership of a session's execution host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    bridge_session_id: str
    fencing_token: int


@runtime_checkable
class LeaseManager(Protocol):
    async def acquire(self, bridge_session_id: str) -> Lease: ...

    async def renew(self, lease: Lease) -> Lease: ...

    async def release(self, lease: Lease) -> None: ...


__all__ = ["Lease", "LeaseManager"]
