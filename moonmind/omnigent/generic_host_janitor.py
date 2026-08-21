"""Recovery scanner for abandoned generic Omnigent host bindings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


class GenericOmnigentHostJanitor:
    def __init__(
        self,
        *,
        host_leases: Any,
        runtime_bindings: Any,
        realizer: Any,
        stale_after_seconds: int = 300,
    ) -> None:
        self._host_leases = host_leases
        self._runtime_bindings = runtime_bindings
        self._realizer = realizer
        self._stale_after = stale_after_seconds

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
        return {
            "examined": len(bindings) + len(leases),
            "runtimeBindingsExamined": len(bindings),
            "hostLeasesExamined": len(leases),
            "reconciled": reconciled,
            "conflicts": conflicts,
            "failures": failures,
        }


__all__ = ["GenericOmnigentHostJanitor"]
