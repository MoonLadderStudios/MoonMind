"""One graceful signal/drain owner shared by the supported worker entrypoints."""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

WORKER_DRAIN_TIMEOUT = timedelta(minutes=5)


async def _all_running(workers, stop) -> bool:
    """Wait until every worker passed SDK validation, or the group stopped.

    ``Worker.is_running`` turns true only after Core validated the worker, so
    a lane whose poller cannot start never lets the group report ready.
    """
    while not all(getattr(worker, "is_running", True) for worker in workers):
        if stop.is_set():
            return False
        await asyncio.sleep(0.05)
    return not stop.is_set()


async def serve_workers(workers, *, ready=None, stopping=None, stop=None):
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        installed = True
    except (NotImplementedError, RuntimeError):
        pass  # Non-main-thread and Windows development hosts use task cancellation.
    try:
        async with asyncio.TaskGroup() as group:
            for worker in workers:
                task = group.create_task(worker.run())
                task.add_done_callback(lambda _task: stop.set())
            try:
                await asyncio.sleep(0)
                if ready and await _all_running(workers, stop):
                    await ready()
                await stop.wait()
            finally:
                if stopping:
                    stopping()
                await asyncio.gather(*(worker.shutdown() for worker in workers))
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)
