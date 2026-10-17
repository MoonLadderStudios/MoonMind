"""Deterministic lease lifetime around the canonical AgentRun entrypoint."""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError, is_cancelled_exception

RENEW_SECONDS = 300
STOP_MARGIN_SECONDS = 60
#: Wait between attempts after a transient (``status: retry`` or Activity
#: failure) observation. Activity retries cannot repeat a normally returned
#: ``retry`` result, so this owner is the one place that retries it.
RETRY_SECONDS = 30

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

#: Replay version for bounded renewal recovery. Recorded histories made one
#: pre-launch observation and could start a renewal at the stop margin;
#: patched histories retry transient pre-launch observations within one renew
#: interval and never let an active renewal outlive the confirmed deadline.
RENEWAL_RECOVERY_PATCH_ID = "issue-claim-renewal-recovery-v1"

#: The renewal Activity raises this when the local receipt no longer matches
#: the lease. It is confirmed evidence, not a transient read failure.
_OWNER_MISMATCH_CODE = "claim_lease_owner_mismatch"


def _capacity_blocked_error() -> ApplicationError:
    return ApplicationError(
        f"{CAPACITY_BLOCKED_CODE}: queued behind unavailable local capacity; "
        "releasing the issue reservation and backing off",
        type="CapacityBlocked",
        non_retryable=True,
    )


def _failure_text(exc: BaseException) -> str:
    parts = []
    current = exc
    for _ in range(10):
        if current is None:
            break
        parts.append(str(current))
        current = getattr(current, "cause", None) or current.__cause__
    return " ".join(parts)


def _confirmed_deadline(value):
    """A renewed deadline must be a timezone-aware timestamp; otherwise None."""
    try:
        deadline = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return deadline if deadline.utcoffset() is not None else None


async def execute_with_issue_lease(*, lease, execute, renew, should_renew=None):
    """All runtimes use their existing cancellation and preservation owner.

    This is the one renewal policy for every claimed caller. Only a confirmed
    renewal for this owner and attempt with a usable deadline grants
    authority; launch waits for one and execution stops at the last confirmed
    deadline minus the stop margin. A ``retry`` result or Activity failure is
    transient: it never extends the deadline and is retried here at a short
    cadence. Lost ownership, identity mismatch and an invalid deadline are
    confirmed and stop at once.

    ``should_renew`` reports whether this deployment currently holds the local
    capacity the reservation was acquired for. Waiting for unavailable capacity
    must not keep an issue reserved: the run backs off promptly with a typed
    capacity-blocked error, the issue returns to assessment for anyone, and
    this deployment backs off instead of holding the backlog behind a queue
    it cannot drain.
    """
    patched_fn = getattr(workflow, "patched", None)
    recover = patched_fn(RENEWAL_RECOVERY_PATCH_ID) if callable(patched_fn) else True
    expires = None

    async def observe(timeout):
        if timeout is None:
            return await renew(lease)
        # Not asyncio.wait_for: Temporal resolves the abandoned Activity as a
        # cancelled ActivityError, indistinguishable from real cancellation.
        observation = asyncio.create_task(renew(lease))
        try:
            await workflow.wait_condition(observation.done, timeout=timeout)
        except asyncio.TimeoutError:
            # Expected for a stalled observation; the finally block cancels it.
            pass
        finally:
            if not observation.done():
                observation.cancel()
                await asyncio.gather(observation, return_exceptions=True)
                # A stalled observation is unknown ownership, never renewal.
                observation = None
        return {"status": "retry"} if observation is None else observation.result()

    async def refresh(timeout=None):
        """Return True for a confirmed renewal and False for a transient one."""
        nonlocal expires
        try:
            result = await observe(timeout)
        except Exception as exc:
            # Temporal wraps activity cancellation in ActivityError. Treating
            # it as a retry would keep this auxiliary loop alive after its
            # agent finished, trapping the parent's terminal result in gather.
            if is_cancelled_exception(exc):
                raise
            if recover and _OWNER_MISMATCH_CODE in _failure_text(exc):
                raise ApplicationError(
                    "GitHub issue claim lease identity mismatch", non_retryable=True
                ) from exc
            return False
        if result.get("status") == "lost":
            raise ApplicationError("GitHub issue claim lease lost", non_retryable=True)
        if result.get("status") != "renewed":
            return False
        if any(result.get(key) != lease.get(key) for key in ("owner", "attemptId")):
            raise ApplicationError(
                "GitHub issue claim lease identity mismatch", non_retryable=True
            )
        deadline = _confirmed_deadline(result.get("leaseExpiresAt"))
        if deadline is None:
            raise ApplicationError(
                "GitHub issue claim lease deadline invalid", non_retryable=True
            )
        expires = deadline
        return True

    # No agent launch before a confirmed lease. Nothing writes before launch,
    # so transient observations are retried for at most one renew interval;
    # a lease that lapsed meanwhile is reported lost by the next readback.
    # Each observation starts inside the window and is bounded by what is
    # left of it, so recovery cannot confirm and launch after it closes.
    if recover:
        window_ends = workflow.now() + timedelta(seconds=RENEW_SECONDS)
        while True:
            remaining = (window_ends - workflow.now()).total_seconds()
            if remaining <= 0:
                raise ApplicationError(
                    "GitHub issue claim lease unavailable before launch",
                    non_retryable=True,
                )
            if await refresh(timeout=remaining):
                break
            remaining = (window_ends - workflow.now()).total_seconds()
            if remaining > 0:
                await workflow.sleep(min(RETRY_SECONDS, remaining))
        if expires <= workflow.now() + timedelta(seconds=STOP_MARGIN_SECONDS):
            raise ApplicationError(
                "GitHub issue claim lease unavailable before launch",
                non_retryable=True,
            )
    elif not await refresh() or expires <= workflow.now() + timedelta(
        seconds=STOP_MARGIN_SECONDS
    ):
        raise ApplicationError("GitHub issue claim lease unavailable before launch")

    def authority_remaining():
        return (expires - workflow.now()).total_seconds() - STOP_MARGIN_SECONDS

    async def maintain():
        # Fast backoff: a run queued behind unavailable capacity never held
        # runnable capacity, so release promptly instead of idling to expiry.
        # The check rides the existing renew cadence (no new timers): at most
        # one renew interval elapses before the reservation is released.
        use_backoff = (
            patched_fn(CAPACITY_BACKOFF_PATCH_ID) if callable(patched_fn) else True
        )
        if not use_backoff:
            # Legacy sequence for recorded histories: stop renewing and let
            # the reservation lapse on its own deadline.
            delay = RENEW_SECONDS
            while True:
                remaining = authority_remaining()
                if remaining <= 0:
                    raise ApplicationError(
                        "GitHub issue claim lease expired", non_retryable=True
                    )
                await workflow.sleep(min(delay, remaining))
                if should_renew is not None and not should_renew():
                    delay = min(RETRY_SECONDS, remaining)
                    continue
                delay = (
                    RENEW_SECONDS if await refresh() else min(RETRY_SECONDS, remaining)
                )
        if should_renew is not None and not should_renew():
            raise _capacity_blocked_error()
        delay = RENEW_SECONDS
        while True:
            if should_renew is not None and not should_renew():
                raise _capacity_blocked_error()
            remaining = authority_remaining()
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
            if recover:
                # Writers run only on the last confirmed deadline, so even a
                # stalled observation must end at the stop margin.
                remaining = authority_remaining()
                if remaining <= 0:
                    raise ApplicationError(
                        "GitHub issue claim lease expired", non_retryable=True
                    )
                renewed = await refresh(timeout=remaining)
            else:
                renewed = await refresh()
            delay = RENEW_SECONDS if renewed else min(RETRY_SECONDS, remaining)

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
