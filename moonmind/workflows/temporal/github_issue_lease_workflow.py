"""Deterministic lease lifetime around the canonical AgentRun entrypoint."""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError, is_cancelled_exception

RENEW_SECONDS = 300
STOP_MARGIN_SECONDS = 60


async def execute_with_issue_lease(*, lease, execute, renew):
    """All runtimes use their existing cancellation and preservation owner."""
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
        delay = RENEW_SECONDS
        while True:
            remaining = (expires - workflow.now()).total_seconds() - STOP_MARGIN_SECONDS
            if remaining <= 0:
                raise ApplicationError(
                    "GitHub issue claim lease expired", non_retryable=True
                )
            await workflow.sleep(min(delay, remaining))
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
