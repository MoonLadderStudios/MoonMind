"""Co-located workflow lanes keep independent progress (MoonLadderStudios/MoonMind#3937).

The workflow fleet hosts the start, replay and merge-automation queues as
separate SDK Workers inside one ``serve_workers`` group. Each Worker owns its
own workflow-task slots, so a saturated normal lane must neither block the
merge-automation lane nor lift the configured per-lane limits. This runs
against the isolated real Temporal service: the time-skipping test server
serializes workflow tasks across Workers and cannot show slot independence.
"""

from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.workflows.temporal.activity_catalog import WORKFLOW_FLEET
from moonmind.workflows.temporal.worker_lifecycle import serve_workers
from moonmind.workflows.temporal.worker_runtime import _worker_concurrency_kwargs
from moonmind.workflows.temporal.workers import describe_configured_worker
from tests.integration.reliability.test_release_routing_journey import connect

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_NORMAL_LANE_HELD = threading.Semaphore(0)
_RELEASE_NORMAL_LANE = threading.Event()


@workflow.defn(name="MoonMind.Test.LaneProbe")
class _LaneProbeWorkflow:
    @workflow.run
    async def run(self, hold: bool) -> str:
        if hold:
            # Block inside the workflow task so its slot stays occupied.
            _NORMAL_LANE_HELD.release()
            _RELEASE_NORMAL_LANE.wait(20)
        return workflow.info().task_queue


async def test_merge_lane_progresses_while_normal_lane_slots_are_saturated():
    client = await connect()
    _RELEASE_NORMAL_LANE.clear()
    suffix = uuid4().hex
    temporal_settings = settings.temporal.model_copy(
        update={
            "worker_fleet": WORKFLOW_FLEET,
            "user_workflow_v2_task_queue": f"lane-start-{suffix}",
            "workflow_task_queue": f"lane-replay-{suffix}",
            "merge_automation_workflow_task_queue": f"lane-merge-{suffix}",
            "workflow_worker_concurrency": 2,
            "merge_automation_workflow_worker_concurrency": 2,
        }
    )
    topology = describe_configured_worker(temporal_settings=temporal_settings)
    start_queue, replay_queue, merge_queue = topology.task_queues
    assert merge_queue == f"lane-merge-{suffix}"

    workers = [
        Worker(
            client,
            task_queue=task_queue,
            workflows=[_LaneProbeWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            # The probe blocks its workflow task on purpose; the deadlock
            # detector would otherwise fail it after two seconds.
            debug_mode=True,
            **_worker_concurrency_kwargs(topology, task_queue),
        )
        for task_queue in topology.task_queues
    ]
    stop = asyncio.Event()
    ready = asyncio.Event()

    async def mark_ready():
        ready.set()

    async def start(queue: str, *, hold: bool):
        return await client.start_workflow(
            _LaneProbeWorkflow.run,
            hold,
            id=f"lane-probe-{uuid4().hex}",
            task_queue=queue,
        )

    group = asyncio.create_task(serve_workers(workers, ready=mark_ready, stop=stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)

        # Occupy both normal-lane workflow-task slots at the same time.
        held = [await start(start_queue, hold=True) for _ in range(2)]
        for _ in held:
            assert await asyncio.to_thread(_NORMAL_LANE_HELD.acquire, True, 10)

        # The normal-lane limit is enforced: a third start waits for a slot.
        queued = await start(start_queue, hold=False)
        queued_result = asyncio.ensure_future(queued.result())
        done, _ = await asyncio.wait({queued_result}, timeout=1.0)
        assert not done

        # Merge automation and the replay queue keep their own slots.
        merge = await start(merge_queue, hold=False)
        assert await asyncio.wait_for(merge.result(), 10) == merge_queue
        replay = await start(replay_queue, hold=False)
        assert await asyncio.wait_for(replay.result(), 10) == replay_queue
        assert not queued_result.done()

        _RELEASE_NORMAL_LANE.set()
        assert await asyncio.wait_for(queued_result, 15) == start_queue
        for handle in held:
            assert await asyncio.wait_for(handle.result(), 15) == start_queue
    finally:
        _RELEASE_NORMAL_LANE.set()
        stop.set()
        await asyncio.wait_for(group, timeout=60)
