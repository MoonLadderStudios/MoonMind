"""Coordinate host acquisition/release under a fenced lease, over ports."""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.omnigent.ports.hosts import HostHandle, HostLauncher
from moonmind.omnigent.ports.leases import Lease, LeaseManager


@dataclass(frozen=True, slots=True)
class BoundHost:
    lease: Lease
    host: HostHandle


class ManageHost:
    """Acquire a lease then a host; release both in reverse order."""

    def __init__(self, leases: LeaseManager, launcher: HostLauncher) -> None:
        self._leases = leases
        self._launcher = launcher

    async def bind(self, bridge_session_id: str, *, image: str) -> BoundHost:
        lease = await self._leases.acquire(bridge_session_id)
        host = await self._launcher.launch(bridge_session_id, image=image)
        return BoundHost(lease=lease, host=host)

    async def unbind(self, bound: BoundHost) -> None:
        await self._launcher.release(bound.host.host_id)
        await self._leases.release(bound.lease)


__all__ = ["BoundHost", "ManageHost"]
