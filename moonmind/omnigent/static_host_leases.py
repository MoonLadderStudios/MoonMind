"""Adapt the existing physical static-host lease to the shared lifecycle."""

from __future__ import annotations

from typing import Any
from datetime import UTC, datetime

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.static_hosts import GENERIC_STATIC_LEASE_PURPOSE
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_leases import HostLeaseAuthority


def _conflict(message: str):
    raise HarnessPlatformError(
        message, code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT
    )


class StaticOmnigentHostLeaseRepository:
    """One physical reservation, including retained legacy consumers.

    The shared realizer still owns preparation, execution, saves and cleanup.
    This adapter retains the existing static binding's exclusive storage and
    uses a fresh allocation identity for each runtime binding.
    """

    def __init__(
        self, *, host_repository, runtime_binding_store, capacity_admission=None
    ):
        self._hosts = host_repository
        self._bindings = runtime_binding_store
        self._capacity = capacity_admission

    async def _authority(self, physical) -> HostLeaseAuthority | None:
        if physical is None or physical.lease_purpose != GENERIC_STATIC_LEASE_PURPOSE:
            return None
        binding = await self._bindings.get(physical.runtime_binding_id)
        if binding is None:
            _conflict("static allocation lost its recorded runtime binding")
        phase = (binding.phaseResults or {}).get("staticHostSelection")
        if not isinstance(phase, dict) or not phase.get("hostClassRef"):
            _conflict("static allocation lacks its admitted host selection")
        status = {
            "allocating": "allocating",
            "starting": "allocating",
            "ready": "ready",
            "assigned": "ready",
            "draining": "cleanup_pending",
            "failed": "cleanup_pending",
            "stopped": "cleaned",
        }[physical.status]
        authority = next(
            (
                dict(item)
                for item in binding.cleanupAuthorityRefs
                if isinstance(item, dict)
                and item.get("kind") == "host"
                and item.get("hostLeaseRef") == physical.lease_id
            ),
            None,
        )
        return HostLeaseAuthority(
            leaseRef=physical.lease_id,
            bindingRef=physical.binding_ref,
            runtimeBindingId=binding.bindingId,
            hostClassRef=phase["hostClassRef"],
            generation=2 if status in {"cleanup_pending", "cleaned"} else 1,
            launchGeneration=1,
            status=status,
            omnigentHostId=physical.omnigent_host_id,
            cleanupHandle=authority,
            heartbeatAt=physical.last_heartbeat_at,
            expiresAt=physical.expires_at,
        )

    async def get(self, lease_ref: str) -> HostLeaseAuthority | None:
        return await self._authority(await self._hosts.get_host_lease(lease_ref))

    async def acquire(self, **kwargs: Any) -> HostLeaseAuthority:
        selection = dict(kwargs["static_selection"])
        binding = await self._bindings.get(kwargs["runtime_binding_id"])
        if binding is None or binding.executionPlanRef != kwargs["execution_plan_ref"]:
            _conflict("static allocation does not match its recorded execution plan")
        phase = (binding.phaseResults or {}).get("staticHostSelection")
        if phase != {"hostClassRef": kwargs["host_class_ref"], "selection": selection}:
            _conflict("static allocation differs from its admitted host selection")
        current = await self._hosts.get_binding_for_profile(
            selection["providerProfileId"]
        )
        if (
            current is None
            or current.host_launch_profile_ref
            or {
                "bindingRef": current.binding_ref,
                "providerProfileId": current.provider_profile_id,
                "endpointRef": current.endpoint_ref,
                "harness": current.harness,
                "staticHostId": current.static_host_id,
            }
            != selection
            or (
                current.launch_policy_ref
                and current.launch_policy_ref != kwargs["launch_policy_ref"]
            )
        ):
            _conflict("configured static host changed after admission")
        acquired = tuple(kwargs["provider_leases"])
        if (
            len(acquired) != 1
            or acquired[0].provider_profile_ref != current.provider_profile_id
        ):
            _conflict("static host requires its selected Provider Profile grant")
        grant = acquired[0]
        recorded = binding.providerLeases.get(grant.slot, {})
        expected = grant.runtime_binding_value()
        for key in (
            "providerProfileRef",
            "providerLeaseRef",
            "leaseOwnerId",
            "credentialGeneration",
            "leaseFencingGeneration",
        ):
            if recorded.get(key) != expected.get(key):
                _conflict("static host grant differs from its runtime binding")
        if (
            current.credential_mount_ref.auth_volume_ref.credential_generation
            != grant.credential_generation
        ):
            _conflict("static host credential generation changed after admission")
        request = kwargs["request"]
        try:
            physical = await self._hosts.create_or_get_host_lease(
                binding=current,
                provider_lease_id=grant.lease.lease_id,
                holder_workflow_id=(
                    request.step_execution.workflow_id
                    if request.step_execution
                    else request.correlation_id
                ),
                agent_run_id=request.correlation_id,
                idempotency_key=binding.bindingId,
                lease_purpose=GENERIC_STATIC_LEASE_PURPOSE,
                ttl_seconds=int(kwargs.get("ttl_seconds", 3600)),
                capacity_admission=self._capacity,
                effective_launch_snapshot=kwargs.get("effective_launch_snapshot"),
            )
        except OmnigentOAuthHostError as error:
            code = (
                HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE
                if error.code == "OMNIGENT_OAUTH_HOST_PROFILE_BUSY"
                else HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT
            )
            raise HarnessPlatformError(str(error), code=code) from error
        return await self._authority(physical)

    async def _checked(self, lease_ref, expected_generation):
        physical = await self._hosts.get_host_lease(lease_ref)
        authority = await self._authority(physical)
        if authority is None or authority.generation != expected_generation:
            _conflict("static host lease CAS fence does not match")
        return physical, authority

    async def record_launch(self, lease_ref, *, expected_generation, cleanup_handle):
        physical, authority = await self._checked(lease_ref, expected_generation)
        if (
            authority.cleanupHandle != cleanup_handle
            or cleanup_handle.get("launchGeneration") != 1
        ):
            _conflict("static launch cleanup authority was not durably recorded")
        if physical.status == "allocating":
            physical = await self._hosts.transition_host_lease(
                lease_ref,
                expected_status="allocating",
                new_status="starting",
                fields={"container_name": cleanup_handle["containerName"]},
            )
        elif physical.status not in {"starting", "ready"}:
            _conflict("static host is no longer allocated for launch")
        return await self._authority(physical)

    async def mark_ready(
        self, lease_ref, *, expected_generation, omnigent_host_id, cleanup_handle
    ):
        physical, authority = await self._checked(lease_ref, expected_generation)
        if not authority.cleanupHandle or any(
            cleanup_handle.get(key) != authority.cleanupHandle.get(key)
            for key in (
                "containerName",
                "stateVolumeRef",
                "controlVolumeRef",
                "launchGeneration",
            )
        ):
            _conflict("static host readiness changed its cleanup authority")
        if physical.status == "ready":
            if physical.omnigent_host_id != omnigent_host_id:
                _conflict("static host identity changed after registration")
            return authority
        if physical.status != "starting":
            _conflict("static host launch is not ready for registration")
        return await self._authority(
            await self._hosts.transition_host_lease(
                lease_ref,
                expected_status="starting",
                new_status="ready",
                fields={"omnigent_host_id": omnigent_host_id},
            )
        )

    async def claim_cleanup(self, lease_ref, *, expected_generation):
        physical, authority = await self._checked(lease_ref, expected_generation)
        if authority.status == "cleanup_pending":
            return authority
        if authority.status == "cleaned":
            _conflict("completed static allocation cannot be reopened")
        claimed = await self._hosts.claim_host_lease_cleanup(
            lease_ref,
            expected_status=physical.status,
            expected_last_heartbeat_at=physical.last_heartbeat_at,
        )
        if claimed is None:
            _conflict("static cleanup lost its lease heartbeat fence")
        return await self._authority(claimed)

    async def mark_cleaned(self, lease_ref, *, expected_generation):
        physical, authority = await self._checked(lease_ref, expected_generation)
        if authority.status == "cleaned":
            return authority
        if authority.status != "cleanup_pending":
            _conflict("static cleanup has no fenced claim")
        return await self._authority(
            await self._hosts.transition_host_lease(
                lease_ref,
                expected_status=physical.status,
                new_status="stopped",
                fields={"cleanup_completed_at": datetime.now(UTC)},
            )
        )

    async def heartbeat(self, lease_ref, *, expected_generation, ttl_seconds=900):
        _physical, authority = await self._checked(lease_ref, expected_generation)
        if authority.status not in {"allocating", "ready"}:
            _conflict("static host heartbeat cannot reopen cleanup")
        return await self._authority(
            await self._hosts.heartbeat_host_lease(lease_ref, ttl_seconds=ttl_seconds)
        )

    async def list_recoverable(self, *, stale_before):
        leases = await self._hosts.list_active_host_leases()
        return tuple(
            [
                await self._authority(lease)
                for lease in leases
                if lease.lease_purpose == GENERIC_STATIC_LEASE_PURPOSE
                and (
                    lease.last_heartbeat_at < stale_before
                    or lease.expires_at < datetime.now(UTC)
                )
            ]
        )
