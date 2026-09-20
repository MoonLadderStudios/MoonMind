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
whenever any ledger row for the runtime is still unreleased or the ledger
cannot be read at all — an unreadable ledger is not evidence of free capacity,
and neither is a row whose state this contract does not recognize. Once the
replacement is started, recovery waits for it to report restored state before
declaring success, so no request is admitted against empty in-memory state.
Every refusal is returned with the evidence that produced it rather than
retried blindly.

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


def _pending_workflow_task_attempt(description: Any) -> int:
    """The retry attempt of the execution's pending workflow task.

    Temporal reports ``1`` on a first attempt, so anything higher means the
    previous attempt failed and the server is retrying. ``0`` means there is no
    pending task, or a description shape that cannot be read — both of which
    leave the history scan to decide.
    """

    raw_description = getattr(description, "raw_description", None)
    if raw_description is None:
        return 0
    pending = getattr(raw_description, "pending_workflow_task", None)
    if pending is None:
        return 0
    try:
        return int(getattr(pending, "attempt", 0) or 0)
    except (TypeError, ValueError):
        return 0


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

#: The only lease state that frees capacity. Every other value — ``held``,
#: ``cleanup_requested``, a legacy ``NULL`` that predates the state column, or
#: a state this contract does not know — keeps the row spending a slot, which
#: is exactly how the authoritative ``sync_slot_leases`` loader reads the
#: table. Counting one state would let a legacy or unreconciled row read as
#: free and be destroyed by a replacement that cannot restore it.
RELEASED_LEASE_STATE = "released"

#: How many rows the refusal detail names per state before truncating.
_LEDGER_BREAKDOWN_LIMIT = 6

#: Bounded wait for a replacement manager to finish restoring the ledger.
#: Startup runs DB Activities, and the manager answers Updates while they run,
#: so a resubmission sent too early can be admitted against empty in-memory
#: state. Recovery is not complete until the successor owns real state.
STARTUP_RESTORE_ATTEMPTS = 30
STARTUP_RESTORE_DELAY_SECONDS = 1.0


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
    #: Rows still spending a slot, or ``None`` when the ledger was unreadable.
    held_leases: int | None = None
    #: Whether the replacement confirmed it finished restoring the ledger.
    #: ``recovered`` is never true without it.
    startup_restored: bool = False


class ProviderManagerUnavailableError(RuntimeError):
    """The manager is unusable and recovery did not (or must not) replace it."""

    def __init__(self, recovery: ManagerReplayRecovery) -> None:
        self.recovery = recovery
        detail = recovery.detail or recovery.refusal
        super().__init__(
            f"Provider Profile manager for runtime {recovery.runtime_id!r} is "
            f"unavailable ({recovery.refusal}): {detail}"
        )


async def count_unreleased_provider_leases(runtime_id: str) -> int | None:
    """Rows still spending a slot, or ``None`` when the ledger is unreadable.

    Every row that is not a ``released`` tombstone counts, including a legacy
    ``NULL`` state and any state outside the durable contract. The loader in
    ``provider_profile.sync_slot_leases`` reads the table the same way: a
    pre-contract row is still held and an unknown state is unreconciled
    evidence, never free capacity. Counting only ``held`` would let either kind
    authorize a termination whose replacement then restores a live lease or
    fails startup on the unreconciled row — after the destructive step.

    This reads the authoritative lease table directly rather than through the
    manager: recovery must not depend on the component it is recovering.
    """

    try:
        from sqlalchemy import func, or_, select

        from api_service.db.base import get_async_session_context
        from api_service.db.models import ProviderProfileSlotLease

        async with get_async_session_context() as session:
            result = await session.execute(
                select(
                    ProviderProfileSlotLease.lease_state, func.count()
                )
                .where(
                    ProviderProfileSlotLease.runtime_id == runtime_id,
                    or_(
                        ProviderProfileSlotLease.lease_state.is_(None),
                        ProviderProfileSlotLease.lease_state
                        != RELEASED_LEASE_STATE,
                    ),
                )
                .group_by(ProviderProfileSlotLease.lease_state)
            )
            breakdown = {
                str(state or "<null>"): int(count)
                for state, count in result.all()
            }
        total = sum(breakdown.values())
        if total:
            logger.warning(
                "Provider Profile ledger still spends capacity for runtime %s: "
                "%s",
                runtime_id,
                ", ".join(
                    f"{state}={count}"
                    for state, count in sorted(breakdown.items())[
                        :_LEDGER_BREAKDOWN_LIMIT
                    ]
                ),
            )
        return total
    except Exception:
        logger.warning(
            "Provider Profile lease ledger unreadable for runtime %s; "
            "refusing to replace its manager without capacity evidence",
            runtime_id,
            exc_info=True,
        )
        return None


class ClientRecoveryAdapter:
    """Adapt a raw Temporal client to the handful of calls recovery makes.

    The release liveness gate already holds a connected client for the exact
    deployment it is qualifying. Recovering through that same client keeps the
    repair and the observation on one connection instead of opening a second
    one that might not name the same service.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get_client(self) -> Any:
        return self._client

    async def get_workflow_handle(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> Any:
        if run_id:
            return self._client.get_workflow_handle(workflow_id, run_id=run_id)
        return self._client.get_workflow_handle(workflow_id)

    async def describe_workflow(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> Any:
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
        return await handle.describe()

    async def terminate_workflow(
        self, workflow_id: str, *, reason: str, run_id: str | None = None
    ) -> None:
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
        await handle.terminate(reason=reason)


async def recover_manager_for_runtime(
    adapter: Any, runtime_id: str
) -> ManagerReplayRecovery | None:
    """Run the shared replacement for one runtime, reporting failures as None.

    Callers that are themselves diagnostics (a release gate, a slot-wait
    inspection) must still report what they observed when the repair cannot
    run, so a recovery error never becomes their error.
    """

    from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient

    lease_client = ProviderProfileLeaseClient(adapter)

    async def _start_manager() -> str:
        return await lease_client.ensure_manager(runtime_id)

    try:
        return await recover_wedged_provider_manager(
            adapter, runtime_id=runtime_id, start_manager=_start_manager
        )
    except Exception:
        logger.warning(
            "Provider Profile manager recovery failed for runtime %s",
            runtime_id,
            exc_info=True,
        )
        return None


async def _await_startup_restoration(
    adapter: Any,
    workflow_id: str,
    *,
    attempts: int,
    delay_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> tuple[bool, str]:
    """Wait for a replacement manager to report restored durable state.

    ``start_workflow`` only submits the run. Its ``run()`` then restores
    profiles and durable leases through Activities, and Temporal dispatches
    Update handlers while those Activities are in flight — the maintenance
    handler can create a placeholder profile and grant against empty state.
    Polling the manager's own ``startup_restored`` flag is what makes the
    resubmission admit against the authoritative ledger instead.
    """

    last_error = ""
    for attempt in range(attempts):
        try:
            handle = await adapter.get_workflow_handle(workflow_id)
            state = await handle.query("get_state")
            if isinstance(state, dict) and state.get("startup_restored") is True:
                return True, ""
            last_error = "the replacement has not reported restored state"
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < attempts:
            await sleep(delay_seconds)
    return False, last_error


async def recover_wedged_provider_manager(
    adapter: Any,
    *,
    runtime_id: str,
    start_manager: Callable[[], Awaitable[str]],
    held_lease_probe: Callable[[str], Awaitable[int | None]] | None = None,
    history_tail_limit: int = HISTORY_TAIL_SCAN_LIMIT,
    startup_restore_attempts: int = STARTUP_RESTORE_ATTEMPTS,
    startup_restore_delay_seconds: float = STARTUP_RESTORE_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> ManagerReplayRecovery:
    """Replace ``runtime_id``'s manager run when it is wedged on replay.

    ``start_manager`` is the caller's own start-or-attach step, so this module
    never becomes a second definition of how a manager is launched.
    """

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )

    workflow_id = workflow_id_for_runtime(runtime_id)
    probe = held_lease_probe or count_unreleased_provider_leases

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

    # Cheap pre-filter before the history read. Temporal reports attempt 1 for
    # a workflow task that has not failed, so a manager that is merely busy or
    # slow to answer a query is excluded here without paging its history. A
    # wedge is always retrying, so this never hides one.
    if _pending_workflow_task_attempt(description) == 1:
        return _refuse(
            MANAGER_NOT_WEDGED,
            "the manager's pending workflow task is on its first attempt, so "
            "it is busy or slow rather than wedged on replay",
        )

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
            f"the durable lease ledger reports {held} unreleased lease row(s) "
            "for this runtime, so a live consumer would lose its authority. "
            "A wedged manager cannot process the holder's release signal, so "
            "the row must be reconciled against confirmed teardown before the "
            f"run can be replaced; see {RECOVERY_RUNBOOK}",
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

    if sleep is None:
        import asyncio

        sleep = asyncio.sleep
    restored, restore_error = await _await_startup_restoration(
        adapter,
        workflow_id,
        attempts=startup_restore_attempts,
        delay_seconds=startup_restore_delay_seconds,
        sleep=sleep,
    )
    if not restored:
        # The replacement exists but has not proven it owns the ledger. Report
        # that instead of resubmitting: an admission decided against empty
        # in-memory state could grant over a restored cleanup obligation whose
        # consumer may still be running.
        return _refuse(
            MANAGER_RECOVERY_FAILED,
            "the replacement manager did not confirm restored ledger state "
            f"({restore_error or 'no response'}); the wedged run was replaced "
            "but no request was resubmitted against unrestored state",
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
        startup_restored=True,
    )


__all__ = [
    "RELEASED_LEASE_STATE",
    "ClientRecoveryAdapter",
    "MANAGER_HELD_LEASE_PRESENT",
    "MANAGER_NOT_RUNNING",
    "MANAGER_NOT_WEDGED",
    "MANAGER_RECOVERY_FAILED",
    "MANAGER_UNREADABLE_LEDGER",
    "ManagerReplayRecovery",
    "ProviderManagerUnavailableError",
    "count_unreleased_provider_leases",
    "recover_manager_for_runtime",
    "recover_wedged_provider_manager",
]
