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


@pytest.mark.asyncio
async def test_member_failure_stops_group_withdraws_readiness_and_drains_all():
    """A failed co-located lane stops every member of the one group.

    MoonLadderStudios/MoonMind#3937: the merge-automation lane is a member of
    the workflow process's single group, so its poller failing must withdraw
    readiness and drain the other lanes before the process exits for the
    Compose restart policy, instead of leaving a partially polling worker
    reporting ready.
    """

    events = []
    normal_drained = asyncio.Event()

    async def normal_run():
        await normal_drained.wait()

    async def normal_shutdown():
        events.append("normal-drained")
        normal_drained.set()

    async def merge_run():
        await asyncio.sleep(0.01)
        raise RuntimeError("merge-automation poller failed")

    async def merge_shutdown():
        events.append("merge-drained")

    async def ready():
        events.append("ready")

    with pytest.raises(ExceptionGroup) as raised:
        await serve_workers(
            [
                SimpleNamespace(run=normal_run, shutdown=normal_shutdown),
                SimpleNamespace(run=merge_run, shutdown=merge_shutdown),
            ],
            ready=ready,
            stopping=lambda: events.append("stopping"),
        )

    assert raised.group_contains(RuntimeError, match="merge-automation poller")
    assert events[:2] == ["ready", "stopping"]
    assert sorted(events[2:]) == ["merge-drained", "normal-drained"]
