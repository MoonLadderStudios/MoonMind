"""Recovery scanner for abandoned generic Omnigent host bindings.

MoonLadderStudios/MoonMind#3881 gives this existing owner one more
responsibility rather than adding a second coordinator: reconciling the durable
machine-capacity reservations against the containers MoonMind actually owns on
the backend. Reservations that provably never launched are reclaimed, live
consumers keep their accounting even when the workflow that asked for them
died, an owned live container with no accounting record is adopted as a
reconciliation fault rather than read as free capacity, and an unreadable
daemon blocks new admission instead of freeing anything.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class GenericOmnigentHostJanitor:
    def __init__(
        self,
        *,
        host_leases: Any,
        runtime_bindings: Any,
        realizer: Any,
        stale_after_seconds: int = 300,
        machine_capacity: Any | None = None,
        machine_backend_ref: str | None = None,
        container_inventory: Any | None = None,
    ) -> None:
        self._host_leases = host_leases
        self._runtime_bindings = runtime_bindings
        self._realizer = realizer
        self._stale_after = stale_after_seconds
        self._machine_capacity = machine_capacity
        self._machine_backend_ref = str(machine_backend_ref or "").strip() or None
        # Returns the owned running containers, or ``None`` when the backend
        # could not be read. "Nothing is running" and "I could not look" must
        # never be the same value here.
        self._container_inventory = container_inventory

    async def _reconcile_machine_capacity(self) -> dict[str, Any] | None:
        if (
            self._machine_capacity is None
            or self._machine_backend_ref is None
            or self._container_inventory is None
        ):
            return None
        try:
            live = await self._container_inventory()
        except Exception:
            # An inventory that raised proves nothing about the backend, so it
            # is reported as unreadable and blocks admission.
            logger.warning(
                "Owned container inventory failed; machine capacity admission "
                "is blocked until it can be established",
                exc_info=True,
            )
            live = None
        return await self._machine_capacity.reconcile(
            backend_ref=self._machine_backend_ref, live_containers=live
        )

    async def run(self) -> dict[str, Any]:
        stale_before = datetime.now(UTC) - timedelta(seconds=self._stale_after)
        bindings = await self._runtime_bindings.list_recoverable(
            stale_before=stale_before
        )
        leases = await self._host_leases.list_recoverable(stale_before=stale_before)
        reconciled = 0
        conflicts = 0
        failures: list[dict[str, str]] = []
        examined_bindings = {binding.bindingId for binding in bindings}
        for binding in bindings:
            try:
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
            binding = await self._runtime_bindings.get(lease.runtimeBindingId)
            if binding is None:
                failures.append(
                    {
                        "hostLeaseRef": lease.leaseRef,
                        "reason": "runtime_binding_missing",
                    }
                )
                continue
            try:
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
        result = {
            "examined": len(bindings) + len(leases),
            "runtimeBindingsExamined": len(bindings),
            "hostLeasesExamined": len(leases),
            "reconciled": reconciled,
            "conflicts": conflicts,
            "failures": failures,
        }
        machine = await self._reconcile_machine_capacity()
        if machine is not None:
            result["machineCapacity"] = machine
        return result


__all__ = ["GenericOmnigentHostJanitor"]
