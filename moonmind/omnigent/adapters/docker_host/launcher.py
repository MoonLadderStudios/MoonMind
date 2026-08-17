"""In-memory host launcher and lease manager (reference adapters)."""

from __future__ import annotations

from moonmind.omnigent.ports.hosts import HostHandle
from moonmind.omnigent.ports.leases import Lease


class InMemoryHostLauncher:
    def __init__(self) -> None:
        self._hosts: dict[str, HostHandle] = {}
        self._counter = 0

    async def launch(self, bridge_session_id: str, *, image: str) -> HostHandle:
        self._counter += 1
        host_id = f"host-{self._counter}"
        handle = HostHandle(
            host_id=host_id,
            endpoint=f"inmemory://{host_id}",
            metadata={"bridgeSessionId": bridge_session_id, "image": image},
        )
        self._hosts[host_id] = handle
        return handle

    async def release(self, host_id: str) -> None:
        self._hosts.pop(host_id, None)

    @property
    def active(self) -> int:
        return len(self._hosts)


class InMemoryLeaseManager:
    def __init__(self) -> None:
        self._fencing = 0
        self._active: dict[str, Lease] = {}

    async def acquire(self, bridge_session_id: str) -> Lease:
        self._fencing += 1
        lease = Lease(
            lease_id=f"lease-{self._fencing}",
            bridge_session_id=bridge_session_id,
            fencing_token=self._fencing,
        )
        self._active[lease.lease_id] = lease
        return lease

    async def renew(self, lease: Lease) -> Lease:
        if lease.lease_id not in self._active:
            raise RuntimeError(f"Lease {lease.lease_id!r} is not active")
        return lease

    async def release(self, lease: Lease) -> None:
        self._active.pop(lease.lease_id, None)


__all__ = ["InMemoryHostLauncher", "InMemoryLeaseManager"]
