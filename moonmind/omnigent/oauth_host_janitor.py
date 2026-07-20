"""Reconcile stale profile-bound Omnigent OAuth hosts and durable leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


class OmnigentOAuthHostJanitor:
    def __init__(
        self,
        *,
        repository: OmnigentOAuthHostRepository,
        runtime: OmnigentOAuthHostRuntime,
        client: OmnigentHttpClient,
        run_store: Any | None = None,
        heartbeat_timeout_seconds: int = 90,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._client = client
        self._run_store = run_store
        self._heartbeat_timeout = timedelta(
            seconds=max(30, heartbeat_timeout_seconds)
        )

    async def run(
        self, *, profile_id: str | None = None, force: bool = False
    ) -> dict[str, Any]:
        actions: list[dict[str, str]] = []
        if force and profile_id:
            binding = await self._repository.get_binding_for_profile(profile_id)
            if binding is not None and not binding.host_launch_profile_ref:
                await self._runtime.stop_static_host()
                actions.append(
                    {
                        "hostBindingRef": binding.binding_ref,
                        "action": "static_host_stopped",
                    }
                )
        leases = await self._repository.list_active_host_leases()
        cleanup_required = (
            await self._run_store.cleanup_required_host_lease_refs()
            if self._run_store is not None
            else set()
        )
        now = datetime.now(UTC)
        known_containers = {
            lease.container_name: lease for lease in leases if lease.container_name
        }
        for lease in leases:
            if profile_id and lease.provider_profile_id != profile_id:
                continue
            expired = lease.expires_at <= now
            stale = lease.last_heartbeat_at <= now - self._heartbeat_timeout
            terminal_cleanup = lease.lease_id in cleanup_required
            missing = bool(
                lease.container_name
                and not await self._runtime.container_exists(lease.container_name)
            )
            if not force and not expired and not missing and not stale and not terminal_cleanup:
                continue
            binding = await self._repository.validate_binding(lease.binding_ref)
            if lease.omnigent_session_id:
                try:
                    await self._client.get_session(lease.omnigent_session_id)
                    await self._client.interrupt(lease.omnigent_session_id)
                    await self._client.stop_session(lease.omnigent_session_id)
                except Exception as exc:
                    actions.append(
                        {
                            "hostLeaseRef": lease.lease_id,
                            "omnigentSessionRef": lease.omnigent_session_id,
                            "action": "session_cleanup_failed",
                            "errorCode": type(exc).__name__,
                        }
                    )
            if not missing:
                await self._runtime.stop_host(binding=binding, host_lease=lease)
            await self._repository.mark_host_lease_stopped(lease.lease_id)
            actions.append(
                {
                    "hostLeaseRef": lease.lease_id,
                    "action": "expired_cleanup"
                    if expired
                    else (
                        "stale_heartbeat_cleanup"
                        if stale
                        else (
                            "runner_exit_cleanup"
                            if terminal_cleanup
                            else "missing_container_repair"
                        )
                    ),
                }
            )
        for container_name in await self._runtime.list_managed_containers():
            if container_name in known_containers:
                continue
            await self._runtime.remove_container(container_name)
            actions.append(
                {"containerName": container_name, "action": "orphan_container_removed"}
            )
        return {"status": "completed", "actions": actions, "count": len(actions)}


__all__ = ["OmnigentOAuthHostJanitor"]
