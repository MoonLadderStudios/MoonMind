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
