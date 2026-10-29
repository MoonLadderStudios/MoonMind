"""Recovery scanner for abandoned generic Omnigent host bindings.

Reconciles whether each abandoned binding's container exists and whether it
stopped, then drives ordinary cleanup of stopped or orphaned containers, host
leases, temporary credentials, and run-owned storage. Cleanup failure remains
visible, and a live container retains its lifecycle ownership. It never
reconciles whether a calculated machine resource budget is trustworthy: there
is no automatic resource accounting, only fixed per-container limits enforced
directly by Docker and the host-count admission in
``moonmind.omnigent.host_capacity``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


async def temporal_owner_has_closed(binding):
    """Stale heartbeats are not authority to steal a paused/retrying run."""
    from temporalio.client import WorkflowExecutionStatus
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.client import get_temporal_client
    owner = (binding.phaseResults or {}).get("owner", {})
    if not all(owner.get(key) for key in ("namespace", "workflowId", "runId")):
        return None
    client = await get_temporal_client(settings.temporal.address, owner["namespace"])
    execution = await client.get_workflow_handle(owner["workflowId"], run_id=owner["runId"]).describe()
    if execution.status == WorkflowExecutionStatus.CONTINUED_AS_NEW:
        return None  # The successor, rather than this stale binding, owns recovery.
    return execution.status not in (None, WorkflowExecutionStatus.RUNNING)


async def list_profile_host_consumers(
    provider_profile_ref: str, *, runtime_bindings: Any, host_leases: Any
) -> list[Any]:
    """Read durable hosts that can still mount this Profile's credential home."""
    bindings = await runtime_bindings.list_recoverable(
        stale_before=datetime.now(UTC) + timedelta(days=1)
    )
    consumers = []
    for binding in bindings:
        if (
            not any(
                isinstance(lease, dict)
                and lease.get("providerProfileRef") == provider_profile_ref
                for lease in binding.providerLeases.values()
            )
            or not binding.hostLeaseRef
        ):
            continue
        host_lease = await host_leases.get(binding.hostLeaseRef)
        if host_lease is not None and host_lease.status != "cleaned":
            consumers.append(binding)
    return consumers


class GenericOmnigentHostJanitor:

    def __init__(
        self,
        *,
        host_leases: Any,
        runtime_bindings: Any,
        realizer: Any,
        stale_after_seconds: int = 300,
        owner_has_closed: Any = temporal_owner_has_closed,
    ) -> None:
        self._host_leases = host_leases
        self._runtime_bindings = runtime_bindings
        self._realizer = realizer
        self._stale_after = stale_after_seconds
        self._owner_has_closed = owner_has_closed

    async def run(self) -> dict[str, Any]:
        stale_before = datetime.now(UTC) - timedelta(seconds=self._stale_after)
        failures: list[dict[str, str]] = []
        snapshots = []
        for name, repository in (
            ("runtimeBindings", self._runtime_bindings),
            ("hostLeases", self._host_leases),
        ):
            try:
                snapshots.append(
                    await repository.list_recoverable(stale_before=stale_before)
                )
            except Exception as exc:
                snapshots.append([])
                failures.append({"stage": name, "reason": type(exc).__name__})
        bindings, leases = snapshots
        reconciled = 0
        conflicts = 0
        examined_bindings = {binding.bindingId for binding in bindings}
        for binding in bindings:
            try:
                if await self._owner_has_closed(binding) is not True:
                    conflicts += 1
                    continue
                await self._realizer.reconcile(
                    binding.executionPlanRef, binding.bindingId
                )
            except Exception as exc:
                code = str(getattr(exc, "code", ""))
                if code == "OMNIGENT_RUNTIME_BINDING_CONFLICT":
                    conflicts += 1
                else:
                    failures.append(
                        {
                            "runtimeBindingId": binding.bindingId,
                            "reason": code or exc.__class__.__name__,
                        }
                    )
            else:
                reconciled += 1
        for lease in leases:
            if lease.runtimeBindingId in examined_bindings:
                continue
            try:
                binding = await self._runtime_bindings.get(lease.runtimeBindingId)
                if binding is None:
                    failures.append(
                        {
                            "hostLeaseRef": lease.leaseRef,
                            "reason": "runtime_binding_missing",
                        }
                    )
                    continue
                if await self._owner_has_closed(binding) is not True:
                    conflicts += 1
                    continue
                await self._realizer.reconcile(
                    binding.executionPlanRef, binding.bindingId
                )
            except Exception as exc:
                code = str(getattr(exc, "code", ""))
                if code == "OMNIGENT_RUNTIME_BINDING_CONFLICT":
                    conflicts += 1
                else:
                    failures.append(
                        {
                            "hostLeaseRef": lease.leaseRef,
                            "reason": code or exc.__class__.__name__,
                        }
                    )
            else:
                reconciled += 1
        return {
            "examined": len(bindings) + len(leases),
            "runtimeBindingsExamined": len(bindings),
            "hostLeasesExamined": len(leases),
            "reconciled": reconciled,
            "conflicts": conflicts,
            "failures": failures,
        }

    async def drain_profile(self, provider_profile_ref: str) -> dict[str, Any]:
        """Release generic hosts still bound to one Provider Profile.

        Credential maintenance calls this while holding the Profile's exclusive
        maintenance lease, so no execution can admit new work for it. A binding
        whose host lease is not cleaned can still mount the Profile's credential
        home read-write. Ordinary reconciliation releases it once its owning
        workflow has closed. An open owner keeps its lifecycle, so maintenance
        fails closed rather than mutating a home that host may still write.
        """
        from moonmind.omnigent.harness_platform.failures import (
            HarnessPlatformError,
            HarnessPlatformFailure,
        )

        bindings = await list_profile_host_consumers(
            provider_profile_ref,
            runtime_bindings=self._runtime_bindings,
            host_leases=self._host_leases,
        )
        drained = 0
        open_owners: list[str] = []
        for binding in bindings:
            if await self._owner_has_closed(binding) is not True:
                open_owners.append(binding.bindingId)
                continue
            await self._realizer.reconcile(binding.executionPlanRef, binding.bindingId)
            drained += 1
        if open_owners:
            raise HarnessPlatformError(
                "generic hosts of an open workflow still hold this Provider "
                "Profile: " + ", ".join(open_owners),
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return {"drained": drained}


__all__ = ["GenericOmnigentHostJanitor", "list_profile_host_consumers"]
