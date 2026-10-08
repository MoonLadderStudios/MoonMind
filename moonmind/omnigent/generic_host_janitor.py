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

    async def confirm_attempt_stopped(
        self, *, execution_plan_ref: str, runtime_binding_ref: str
    ) -> dict[str, Any]:
        """Finish an interrupted attempt's cleanup before its successor writes.

        The scan above waits for a binding to go stale. A Run workflow that is
        about to start a successor Step Execution (#4627) cannot wait: an
        expired lease or lost stop acknowledgement does not prove the old
        process stopped. Once the attempt's own owner has closed, the realizer
        reconciliation fences the binding, stops the host and releases its
        credentials and capacity. Anything short of ``cleaned`` raises, so the
        caller retries within its bounds instead of starting parallel compute.
        """

        from moonmind.omnigent.harness_platform.failures import (
            HarnessPlatformError,
            HarnessPlatformFailure,
        )
        from moonmind.omnigent.runtime_bindings import RuntimeBindingState

        binding = await self._runtime_bindings.get(runtime_binding_ref)
        if binding is None or binding.executionPlanRef != execution_plan_ref:
            raise HarnessPlatformError(
                "interrupted attempt's runtime binding is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if binding.state is not RuntimeBindingState.cleaned:
            if await self._owner_has_closed(binding) is not True:
                raise HarnessPlatformError(
                    "interrupted attempt's owner has not closed; its stop is "
                    "not yet confirmed",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            await self._realizer.reconcile(execution_plan_ref, runtime_binding_ref)
            binding = await self._runtime_bindings.get(runtime_binding_ref)
        if binding is None or binding.state is not RuntimeBindingState.cleaned:
            raise HarnessPlatformError(
                "interrupted attempt's cleanup did not confirm its stop",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        saved = (binding.phaseResults or {}).get("saved")
        return {
            "stopConfirmed": True,
            "runtimeBindingRef": binding.bindingId,
            "fencingGeneration": binding.fencingGeneration,
            **({"savedWorkspaceCheckpoint": dict(saved)} if saved else {}),
        }


__all__ = ["GenericOmnigentHostJanitor"]
