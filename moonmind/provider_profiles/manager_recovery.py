"""Replace a Provider Profile manager run the current build cannot replay.

``provider-profile-manager:<runtime>`` is a singleton whose history outlives
any one release, and it is the single credential-capacity ledger for its
runtime. A release whose worker cannot replay a recorded history wedges the
singleton in a ``WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR`` loop:
the run stays ``RUNNING``, its history stops advancing, and every Update —
execution admission, OAuth connect, credential validation, API-key enrollment
— fails until an operator intervenes.

The durable row, not the workflow history, decides whether a slot is spent
(MoonLadderStudios/MoonMind#3883), and a fresh manager restores held and
``cleanup_requested`` rows from that ledger. A wedged run can therefore be
replaced without inventing or losing capacity, which is what the operator
cutover in ``docs/Security/ProviderProfiles.md`` §11.9 has always done by
hand. This module is the one owner of that cutover so callers blocked by a
wedge recover instead of reporting an outage.

Replacement is deliberately narrow. It requires confirmed nondeterminism
evidence from the history tail, it targets the exact observed run so a
concurrent caller's healthy replacement cannot be killed, and it refuses
whenever the ledger reports a held lease or cannot be read at all — an
unreadable ledger is not evidence of free capacity. Every refusal is returned
with the evidence that produced it rather than retried blindly.

Wedge detection is shared with the release liveness gate in
:mod:`moonmind.workflows.skills.provider_manager_liveness`, so recovery and
promotion can never disagree about whether a singleton is wedged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from moonmind.workflows.skills.provider_manager_liveness import (
    HISTORY_TAIL_SCAN_LIMIT,
    RECOVERY_RUNBOOK,
    scan_workflow_task_failure_tail,
)

logger = logging.getLogger(__name__)


#: The run is not ``RUNNING``; starting it is the normal path, not a cutover.
MANAGER_NOT_RUNNING = "manager_not_running"
#: The history tail shows progress, so the blocked call was not a replay wedge.
MANAGER_NOT_WEDGED = "no_replay_wedge"
#: The ledger reports a live credential consumer. Replacing the manager would
#: revoke authority out from under a run that may still be executing.
MANAGER_HELD_LEASE_PRESENT = "held_lease_present"
#: The ledger could not be read. Unavailable evidence is not free capacity.
MANAGER_UNREADABLE_LEDGER = "ledger_unreadable"
#: Termination or the fresh start itself failed.
MANAGER_RECOVERY_FAILED = "recovery_failed"

#: Lease states that still spend a slot for the purposes of this gate. Only a
#: ``held`` row names a consumer that may currently be executing;
#: ``cleanup_requested`` rows are restored by the fresh start with their
#: recorded reasons.
HELD_LEASE_STATE = "held"


@dataclass(frozen=True)
class ManagerReplayRecovery:
    """What recovery observed and what it actually did about it."""

    runtime_id: str
    workflow_id: str
    recovered: bool = False
    #: Empty when ``recovered``; otherwise one of the refusal constants above.
    refusal: str = ""
    detail: str = ""
    terminated_run_id: str = ""
    #: The recorded nondeterminism message, preserved so the symptom's cure
    #: never erases its cause.
    evidence: str = ""
    nondeterminism_failures: int = 0
    #: Held rows the ledger reported, or ``None`` when it was unreadable.
    held_leases: int | None = None


class ProviderManagerUnavailableError(RuntimeError):
    """The manager is unusable and recovery did not (or must not) replace it."""

    def __init__(self, recovery: ManagerReplayRecovery) -> None:
        self.recovery = recovery
        detail = recovery.detail or recovery.refusal
        super().__init__(
            f"Provider Profile manager for runtime {recovery.runtime_id!r} is "
            f"unavailable ({recovery.refusal}): {detail}"
        )


async def count_held_provider_leases(runtime_id: str) -> int | None:
    """Held rows for one runtime, or ``None`` when the ledger is unreadable.

    This reads the authoritative lease table directly rather than through the
    manager: recovery must not depend on the component it is recovering.
    """

    try:
        from sqlalchemy import func, select

        from api_service.db.base import get_async_session_context
        from api_service.db.models import ProviderProfileSlotLease

        async with get_async_session_context() as session:
            result = await session.execute(
                select(func.count())
                .select_from(ProviderProfileSlotLease)
                .where(
                    ProviderProfileSlotLease.runtime_id == runtime_id,
                    ProviderProfileSlotLease.lease_state == HELD_LEASE_STATE,
                )
            )
            return int(result.scalar_one())
    except Exception:
        logger.warning(
            "Provider Profile lease ledger unreadable for runtime %s; "
            "refusing to replace its manager without capacity evidence",
            runtime_id,
            exc_info=True,
        )
        return None


async def recover_wedged_provider_manager(
    adapter: Any,
    *,
    runtime_id: str,
    start_manager: Callable[[], Awaitable[str]],
    held_lease_probe: Callable[[str], Awaitable[int | None]] | None = None,
    history_tail_limit: int = HISTORY_TAIL_SCAN_LIMIT,
) -> ManagerReplayRecovery:
    """Replace ``runtime_id``'s manager run when it is wedged on replay.

    ``start_manager`` is the caller's own start-or-attach step, so this module
    never becomes a second definition of how a manager is launched.
    """

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )

    workflow_id = workflow_id_for_runtime(runtime_id)
    probe = held_lease_probe or count_held_provider_leases

    def _refuse(refusal: str, detail: str, **fields: Any) -> ManagerReplayRecovery:
        return ManagerReplayRecovery(
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            recovered=False,
            refusal=refusal,
            detail=detail,
            **fields,
        )

    try:
        description = await adapter.describe_workflow(workflow_id)
    except Exception as exc:
        return _refuse(
            MANAGER_NOT_RUNNING,
            f"the manager execution could not be described: {exc}",
        )

    status = str(getattr(getattr(description, "status", None), "name", "") or "")
    if status != "RUNNING":
        return _refuse(
            MANAGER_NOT_RUNNING,
            f"the manager execution is {status or 'absent'}, not a wedged run",
        )
    run_id = str(getattr(description, "run_id", "") or "")

    try:
        handle = await adapter.get_workflow_handle(workflow_id, run_id=run_id or None)
        tail: list[Any] = []
        async for event in handle.fetch_history_events(page_size=history_tail_limit):
            tail.append(event)
            if len(tail) > history_tail_limit:
                tail.pop(0)
        failure_tail = scan_workflow_task_failure_tail(tail)
    except Exception as exc:
        return _refuse(
            MANAGER_NOT_WEDGED,
            f"the manager history tail could not be read: {exc}",
        )

    if failure_tail.nondeterminism_failures <= 0:
        return _refuse(
            MANAGER_NOT_WEDGED,
            "the manager history tail records no nondeterminism failure, so "
            "this call was blocked by something a replacement cannot fix",
        )

    held = await probe(runtime_id)
    if held is None:
        return _refuse(
            MANAGER_UNREADABLE_LEDGER,
            "the durable lease ledger could not be read, so free capacity "
            "cannot be confirmed",
            evidence=failure_tail.last_failure_message,
            nondeterminism_failures=failure_tail.nondeterminism_failures,
        )
    if held > 0:
        return _refuse(
            MANAGER_HELD_LEASE_PRESENT,
            f"the durable lease ledger reports {held} held lease(s) for this "
            "runtime, so a live consumer would lose its authority; see "
            f"{RECOVERY_RUNBOOK}",
            evidence=failure_tail.last_failure_message,
            nondeterminism_failures=failure_tail.nondeterminism_failures,
            held_leases=held,
        )

    reason = (
        f"provider-profile-manager replay wedge on run {run_id or 'unknown'}: "
        f"{failure_tail.nondeterminism_failures} consecutive nondeterministic "
        f"workflow-task failures ({failure_tail.last_failure_message}); "
        "replaced automatically with zero held leases in the durable ledger"
    )
    logger.warning(
        "Replacing wedged Provider Profile manager: runtime_id=%s "
        "workflow_id=%s run_id=%s nondeterminism_failures=%s evidence=%s",
        runtime_id,
        workflow_id,
        run_id,
        failure_tail.nondeterminism_failures,
        failure_tail.last_failure_message,
    )
    try:
        await adapter.terminate_workflow(
            workflow_id, reason=reason, run_id=run_id or None
        )
        await start_manager()
    except Exception as exc:
        return _refuse(
            MANAGER_RECOVERY_FAILED,
            f"replacing the wedged manager run failed: {exc}",
            terminated_run_id=run_id,
            evidence=failure_tail.last_failure_message,
            nondeterminism_failures=failure_tail.nondeterminism_failures,
            held_leases=held,
        )

    return ManagerReplayRecovery(
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        recovered=True,
        terminated_run_id=run_id,
        evidence=failure_tail.last_failure_message,
        nondeterminism_failures=failure_tail.nondeterminism_failures,
        held_leases=held,
    )


__all__ = [
    "HELD_LEASE_STATE",
    "MANAGER_HELD_LEASE_PRESENT",
    "MANAGER_NOT_RUNNING",
    "MANAGER_NOT_WEDGED",
    "MANAGER_RECOVERY_FAILED",
    "MANAGER_UNREADABLE_LEDGER",
    "ManagerReplayRecovery",
    "ProviderManagerUnavailableError",
    "count_held_provider_leases",
    "recover_wedged_provider_manager",
]
