"""Deterministic lease lifetime around the canonical AgentRun entrypoint."""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError, is_cancelled_exception

RENEW_SECONDS = 300
STOP_MARGIN_SECONDS = 60

#: Typed backoff when the run never held runnable capacity. This is a
#: deployment fault (no slot was ever granted), not evidence about the issue:
#: recovery records it as ``runtime_unavailable`` with a portable cooldown
#: instead of spending the issue's retry allowance the way an expired active
#: attempt does. The code travels in the failure message so the recovery sweep
#: can match it from the controlling history without a new taxonomy.
CAPACITY_BLOCKED_CODE = "ISSUE_CLAIM_CAPACITY_BLOCKED"

#: Replay version for the fast capacity backoff. Histories recorded before
#: this change expect the legacy sequence (30s timer without renewal when
#: capacity is unavailable after the sleep); patched histories use the typed
#: backoff that releases promptly.
CAPACITY_BACKOFF_PATCH_ID = "issue-claim-capacity-backoff-v1"


def _capacity_blocked_error() -> ApplicationError:
    return ApplicationError(
        f"{CAPACITY_BLOCKED_CODE}: queued behind unavailable local capacity; "
        "releasing the issue reservation and backing off",
        type="CapacityBlocked",
        non_retryable=True,
    )


async def execute_with_issue_lease(*, lease, execute, renew, should_renew=None):
    """All runtimes use their existing cancellation and preservation owner.

    ``should_renew`` reports whether this deployment currently holds the local
    capacity the reservation was acquired for. Waiting for unavailable capacity
    must not keep an issue reserved: the run backs off promptly with a typed
    capacity-blocked error, the issue returns to assessment for anyone, and
    this deployment backs off instead of holding the backlog behind a queue
    it cannot drain.
    """
    expires = None

    async def refresh():
        nonlocal expires
        try:
            result = await renew(lease)
        except Exception as exc:
            # Temporal wraps activity cancellation in ActivityError. Treating
            # it as a retry would keep this auxiliary loop alive after its
            # agent finished, trapping the parent's terminal result in gather.
            if is_cancelled_exception(exc):
                raise
            return False
        if result.get("status") == "lost":
            raise ApplicationError("GitHub issue claim lease lost", non_retryable=True)
        if result.get("status") != "renewed":
            return False
        if any(result.get(key) != lease.get(key) for key in ("owner", "attemptId")):
            raise ApplicationError(
                "GitHub issue claim lease identity mismatch", non_retryable=True
            )
        deadline = datetime.fromisoformat(
            result["leaseExpiresAt"].replace("Z", "+00:00")
        )
        if deadline.utcoffset() is None:
            raise ApplicationError(
                "GitHub issue claim lease deadline invalid", non_retryable=True
            )
        expires = deadline
        return True

    # No agent launch before a confirmed lease. Transient pre-launch failures
    # stay inside the existing AgentRun retry/recovery budget.
    if not await refresh() or expires <= workflow.now() + timedelta(
        seconds=STOP_MARGIN_SECONDS
    ):
        raise ApplicationError("GitHub issue claim lease unavailable before launch")

    async def maintain():
        # Fast backoff: a run queued behind unavailable capacity never held
        # runnable capacity, so release promptly instead of idling to expiry.
        # The check rides the existing renew cadence (no new timers): at most
        # one renew interval elapses before the reservation is released.
        patched_fn = getattr(workflow, "patched", None)
        use_backoff = patched_fn(CAPACITY_BACKOFF_PATCH_ID) if callable(patched_fn) else True
        if not use_backoff:
            # Legacy sequence for recorded histories: stop renewing and let
            # the reservation lapse on its own deadline.
            delay = RENEW_SECONDS
            while True:
                remaining = (expires - workflow.now()).total_seconds() - STOP_MARGIN_SECONDS
                if remaining <= 0:
                    raise ApplicationError(
                        "GitHub issue claim lease expired", non_retryable=True
                    )
                await workflow.sleep(min(delay, remaining))
                if should_renew is not None and not should_renew():
                    delay = min(30, remaining)
                    continue
                delay = RENEW_SECONDS if await refresh() else min(30, remaining)
            return
        if should_renew is not None and not should_renew():
            raise _capacity_blocked_error()
        delay = RENEW_SECONDS
        while True:
            if should_renew is not None and not should_renew():
                raise _capacity_blocked_error()
            remaining = (expires - workflow.now()).total_seconds() - STOP_MARGIN_SECONDS
            if remaining <= 0:
                raise ApplicationError(
                    "GitHub issue claim lease expired", non_retryable=True
                )
            await workflow.sleep(min(delay, remaining))
            # Recheck after the sleep: capacity may have been lost while this
            # maintainer was sleeping. Renewing without this check would
            # extend the remote lease before the next loop releases it.
            if should_renew is not None and not should_renew():
                raise _capacity_blocked_error()
            delay = RENEW_SECONDS if await refresh() else min(30, remaining)

    execution = asyncio.create_task(execute())
    renewal = asyncio.create_task(maintain())
    try:
        done, _ = await asyncio.wait(
            {execution, renewal}, return_when=asyncio.FIRST_COMPLETED
        )
        if execution in done:
            return await execution
        # Explicit return: the maintainer completes only by raising expiry,
        # so this preserves behavior while keeping return shapes consistent.
        return await renewal
    finally:
        for task in (execution, renewal):
            if not task.done():
                task.cancel()
        # The runtime owns ordered cancellation/harvest/cleanup. Expiry removes
        # claim authority; it never authorizes deletion of its workspace.
        await asyncio.gather(execution, renewal, return_exceptions=True)
