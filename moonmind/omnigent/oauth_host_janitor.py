"""Reconcile stale profile-bound Omnigent OAuth hosts and durable leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient


class OmnigentOAuthHostJanitor:
    def __init__(
        self,
        *,
        repository: OmnigentOAuthHostRepository,
        runtime: OmnigentOAuthHostRuntime,
        client: OmnigentHttpClient,
        run_store: Any | None = None,
        lease_client: ProviderProfileLeaseClient | None = None,
        heartbeat_timeout_seconds: int = 90,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._client = client
        self._run_store = run_store
        self._lease_client = lease_client
        self._heartbeat_timeout = timedelta(
            seconds=max(30, heartbeat_timeout_seconds)
        )

    async def _release_provider_lease(self, *, binding: Any, lease: Any) -> bool:
        """Release capacity only after the credential-bearing host is stopped.

        Omnigent host leases are created exclusively for ``execution_omnigent``
        capacity. The ProviderProfileManager deliberately uses the deterministic
        owner token as its lease ID, so the durable ``provider_lease_id`` is also
        the release authority needed after an activity process disappears.
        """

        if self._lease_client is None:
            return False
        provider_lease_id = str(lease.provider_lease_id or "").strip()
        runtime_id = str(
            binding.credential_mount_ref.auth_volume_ref.runtime_id or ""
        ).strip()
        if not provider_lease_id or not runtime_id:
            raise ValueError(
                "host lease is missing Provider Profile release authority"
            )
        await self._lease_client.release_lease(
            CredentialLease(
                profile_id=lease.provider_profile_id,
                runtime_id=runtime_id,
                lease_id=provider_lease_id,
                owner_id=provider_lease_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )
        )
        return True

    async def run_action(
        self,
        *,
        action_kind: str,
        profile_id: str,
        host_lease_ref: str,
        expected_host_state: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        """Apply one lease-scoped remediation operation with before/after evidence."""

        supported = {
            "provider_profile.evict_stale_lease",
            "host.drain",
            "host.stop",
            "host.restart",
            "host.remove",
            "host_lease.reconcile_stale",
        }
        if action_kind not in supported:
            raise ValueError(f"unsupported Omnigent remediation action: {action_kind}")
        lease = await self._repository.get_host_lease(host_lease_ref)
        if lease is None:
            raise ValueError("host lease does not exist")
        if lease.provider_profile_id != profile_id:
            raise ValueError("host lease is not owned by the Provider Profile")
        before_state = lease.status
        if expected_host_state and expected_host_state != before_state:
            raise ValueError("expectedHostState does not match the current host lease")
        binding = await self._repository.validate_binding(lease.binding_ref)
        now = datetime.now(UTC)
        stale = (
            lease.expires_at <= now
            or lease.last_heartbeat_at <= now - self._heartbeat_timeout
        )

        if action_kind == "host.drain":
            if before_state in {"draining", "stopped", "failed"}:
                return self._action_result(
                    action_kind, request_id, profile_id, lease, before_state
                )
            lease = await self._repository.transition_host_lease(
                lease.lease_id, expected_status=before_state, new_status="draining"
            )
        elif action_kind == "host.restart":
            raise ValueError(
                "host.restart is unsupported until the owning launch path can "
                "return terminal generation evidence"
            )
        elif action_kind == "host.stop":
            if before_state not in {"stopped", "failed"}:
                await self._runtime.stop_host(binding=binding, host_lease=lease)
                await self._release_provider_lease(binding=binding, lease=lease)
                lease = await self._repository.mark_host_lease_stopped(lease.lease_id)
        elif action_kind == "host.remove":
            removed = bool(lease.container_name)
            if lease.container_name:
                await self._runtime.remove_container(lease.container_name)
            if before_state != "stopped":
                await self._release_provider_lease(binding=binding, lease=lease)
                lease = await self._repository.mark_host_lease_stopped(lease.lease_id)
        elif action_kind == "provider_profile.evict_stale_lease":
            if not stale:
                raise ValueError("Provider Profile host lease is not stale")
            if before_state not in {"stopped", "failed"}:
                await self._runtime.stop_host(binding=binding, host_lease=lease)
                await self._release_provider_lease(binding=binding, lease=lease)
                lease = await self._repository.mark_host_lease_stopped(lease.lease_id)
        elif action_kind == "host_lease.reconcile_stale":
            if not stale:
                return self._action_result(
                    action_kind, request_id, profile_id, lease, before_state
                )
            missing = bool(
                lease.container_name
                and not await self._runtime.container_exists(lease.container_name)
            )
            if missing:
                await self._release_provider_lease(binding=binding, lease=lease)
                lease = await self._repository.mark_host_lease_stopped(lease.lease_id)
            elif before_state not in {"stopped", "failed"}:
                await self._runtime.stop_host(binding=binding, host_lease=lease)
                await self._release_provider_lease(binding=binding, lease=lease)
                lease = await self._repository.mark_host_lease_stopped(lease.lease_id)

        result = self._action_result(
            action_kind, request_id, profile_id, lease, before_state
        )
        if action_kind == "host.remove" and removed:
            result["status"] = "applied"
            result["afterEvidenceRefs"].append(
                f"omnigent-host-container:{host_lease_ref}:removed"
            )
        return result

    @staticmethod
    def _action_result(
        action_kind: str,
        request_id: str,
        profile_id: str,
        lease: Any,
        before_state: str,
    ) -> dict[str, Any]:
        return {
            "status": "applied" if lease.status != before_state else "no_op",
            "actionKind": action_kind,
            "requestId": request_id,
            "hostLeaseRef": lease.lease_id,
            "providerProfileId": profile_id,
            "before": {"status": before_state, "bindingRef": lease.binding_ref},
            "after": {"status": lease.status, "bindingRef": lease.binding_ref},
            "beforeEvidenceRefs": [
                f"omnigent-host-lease:{lease.lease_id}:state:{before_state}"
            ],
            "afterEvidenceRefs": [
                f"omnigent-host-lease:{lease.lease_id}:state:{lease.status}"
            ],
        }

    async def run(
        self, *, profile_id: str | None = None, force: bool = False
    ) -> dict[str, Any]:
        actions: list[dict[str, str]] = []
        if force and profile_id:
            binding = await self._repository.get_binding_for_profile(profile_id)
            if binding is not None and not binding.host_launch_profile_ref:
                await self._runtime.stop_static_host(binding=binding)
                actions.append(
                    {
                        "hostBindingRef": binding.binding_ref,
                        "action": "static_host_stopped",
                    }
                )
        leases = await self._repository.list_active_host_leases()
        terminal_provider_leases = (
            await self._repository.list_terminal_host_leases_with_active_provider_capacity()
            if hasattr(
                self._repository,
                "list_terminal_host_leases_with_active_provider_capacity",
            )
            else []
        )
        terminal_provider_lease_refs = {
            lease.lease_id for lease in terminal_provider_leases
        }
        leases = list(
            {lease.lease_id: lease for lease in [*leases, *terminal_provider_leases]}.values()
        )
        cleanup_required = (
            await self._run_store.cleanup_required_host_lease_refs()
            if self._run_store is not None
            else set()
        )
        now = datetime.now(UTC)
        reconciliation_required = (
            await self._run_store.embedded_reconciliation_host_lease_refs(
                abandoned_before=now - self._heartbeat_timeout
            )
            if self._run_store is not None
            and hasattr(self._run_store, "embedded_reconciliation_host_lease_refs")
            else {}
        )
        known_containers = {
            lease.container_name: lease for lease in leases if lease.container_name
        }
        for lease in leases:
            if profile_id and lease.provider_profile_id != profile_id:
                continue
            expired = lease.expires_at <= now
            stale = lease.last_heartbeat_at <= now - self._heartbeat_timeout
            terminal_cleanup = lease.lease_id in cleanup_required
            terminal_provider_cleanup = (
                lease.lease_id in terminal_provider_lease_refs
            )
            reconciliation_action = reconciliation_required.get(lease.lease_id)
            missing = bool(
                lease.container_name
                and not await self._runtime.container_exists(lease.container_name)
            )
            if (
                not force
                and not expired
                and not missing
                and not stale
                and not terminal_cleanup
                and not terminal_provider_cleanup
                and not reconciliation_action
            ):
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
            try:
                # Host leases persisted before lifecycle status projection was
                # introduced remain valid janitor inputs. Treat an absent status
                # as active so their owned container is still stopped before the
                # Provider Profile lease is released.
                lease_status = str(getattr(lease, "status", "assigned") or "")
                if not missing and lease_status not in {"stopped", "failed"}:
                    await self._runtime.stop_host(binding=binding, host_lease=lease)
                provider_released = await self._release_provider_lease(
                    binding=binding, lease=lease
                )
                await self._repository.mark_host_lease_stopped(lease.lease_id)
            except Exception as exc:
                if (
                    self._run_store is not None
                    and terminal_cleanup
                    and hasattr(self._run_store, "record_terminal_cleanup")
                ):
                    await self._run_store.record_terminal_cleanup(
                        host_lease_ref=lease.lease_id,
                        completed=False,
                        code=type(exc).__name__,
                        summary=str(exc),
                    )
                raise
            if (
                self._run_store is not None
                and terminal_cleanup
                and hasattr(self._run_store, "record_terminal_cleanup")
            ):
                await self._run_store.record_terminal_cleanup(
                    host_lease_ref=lease.lease_id,
                    completed=True,
                )
            actions.append(
                {
                    "hostLeaseRef": lease.lease_id,
                    "action": "expired_cleanup"
                    if expired
                    else (
                        "stale_heartbeat_cleanup"
                        if stale
                        else (
                            "runner_exit_cleanup" if terminal_cleanup else (
                                "provider_lease_reconciliation"
                                if terminal_provider_cleanup
                                else (
                                    reconciliation_action
                                    or "missing_container_repair"
                                )
                            )
                        )
                    ),
                    "providerLeaseReleased": provider_released,
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
