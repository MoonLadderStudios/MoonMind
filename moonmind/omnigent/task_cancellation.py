"""Stop an Activity's helper tasks even when a library absorbs a cancel.

anyio's ``connect_tcp`` cancels its own happy-eyeballs scope when a connection
succeeds. A native ``Task.cancel()`` that lands in the same moment is merged
into that cancellation and absorbed, so a task polling the Omnigent server
over fresh connections can keep running after one cancel. A stopping Temporal
worker waits for every Activity, so one absorbed cancel would hold the worker
open indefinitely.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

#: How long a cancelled helper may keep running before the cancel is repeated.
CANCEL_REPEAT_SECONDS = 1.0


async def cancel_and_wait(*tasks: asyncio.Task | None) -> None:
    """Cancel ``tasks`` and return once every one of them has stopped.

    A helper that already finished is left alone. Once every helper stops,
    the first helper failure other than its cancellation is raised, and a
    cancellation of the caller itself is re-raised rather than swallowed.
    """

    running = [task for task in tasks if task is not None and not task.done()]
    caller_cancelled = False
    pending = set(running)
    while pending:
        for task in pending:
            task.cancel()
        try:
            _done, pending = await asyncio.wait(pending, timeout=CANCEL_REPEAT_SECONDS)
        except asyncio.CancelledError:
            caller_cancelled = True
            pending = {task for task in pending if not task.done()}
    if caller_cancelled:
        raise asyncio.CancelledError()
    for task in running:
        with suppress(asyncio.CancelledError):
            await task
