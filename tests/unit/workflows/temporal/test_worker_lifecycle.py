import asyncio
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.worker_lifecycle import serve_workers


@pytest.mark.asyncio
async def test_shutdown_withdraws_readiness_and_waits_for_all_drains():
    stop, drained = asyncio.Event(), asyncio.Event()
    events = []

    async def run():
        await drained.wait()

    async def shutdown():
        assert events[-1] == "stopping"
        await asyncio.sleep(0.02)
        events.append("drained")
        drained.set()

    async def ready():
        events.append("ready")
        stop.set()

    await serve_workers(
        [SimpleNamespace(run=run, shutdown=shutdown)],
        stop=stop,
        ready=ready,
        stopping=lambda: events.append("stopping"),
    )
    assert events == ["ready", "stopping", "drained"]


class _Lane:
    """Models the SDK: ``is_running`` flips only after validation succeeds."""

    def __init__(self, name, events, *, validation_error=None, delay=0.0):
        self.name = name
        self.is_running = False
        self._events = events
        self._error = validation_error
        self._delay = delay
        self._stopped = asyncio.Event()

    async def run(self):
        await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        self.is_running = True
        self._events.append(f"{self.name}:running")
        await self._stopped.wait()

    async def shutdown(self):
        self._events.append(f"{self.name}:shutdown")
        self._stopped.set()


@pytest.mark.asyncio
async def test_ready_waits_for_every_lane_to_pass_validation():
    """#3937: one co-located lane still validating keeps the group unready."""
    stop, events = asyncio.Event(), []

    async def ready():
        events.append("ready")
        stop.set()

    await serve_workers(
        [_Lane("normal", events), _Lane("merge", events, delay=0.2)],
        stop=stop,
        ready=ready,
    )
    assert events.index("ready") > events.index("merge:running")
    assert events.index("ready") > events.index("normal:running")


@pytest.mark.asyncio
async def test_lane_that_cannot_poll_is_never_ready_and_stops_the_group():
    """#3937: a failed member stops every lane; Compose restarts the service."""
    events = []

    async def ready():
        events.append("ready")

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await serve_workers(
            [
                _Lane("normal", events),
                _Lane(
                    "merge",
                    events,
                    validation_error=RuntimeError("no poller"),
                    delay=0.1,
                ),
            ],
            ready=ready,
            stopping=lambda: events.append("stopping"),
        )
    assert exc_info.group_contains(RuntimeError, match="no poller")
    assert "ready" not in events
    assert "stopping" in events
    assert "normal:shutdown" in events
