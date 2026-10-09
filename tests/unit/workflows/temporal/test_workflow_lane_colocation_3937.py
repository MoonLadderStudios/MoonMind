"""The workflow fleet serves every workflow lane from one process (MoonMind#3937).

The merge-automation lane used to run as a second ``worker_runtime``
interpreter under a dedicated supervisor, which overrode
``TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE`` so that lane's descendants stayed on
``mm.workflow.merge_automation``. Both lanes now run as separate SDK Workers
under the one ``serve_workers`` owner, so:

- child routing is derived from the queue the parent runs on, giving exactly
  the queues each former process produced;
- workflow-fleet Activities scheduled by a workflow stay on its lane, so the
  commands in-flight merge-lane histories recorded replay unchanged;
- each lane keeps its own workflow-task budget, so a saturated normal lane
  cannot starve merge automation; and
- every lane's Worker is built by the production construction helper.

The Temporal-boundary test records real executions on a time-skipping server
through the production Worker construction and ``serve_workers``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager

import pytest
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.activity_catalog import (
        WORKFLOW_FLEET,
        get_workflow_child_task_queue,
    )
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
    from moonmind.workflows.temporal.workflows.merge_automation import (
        MoonMindMergeAutomationWorkflow,
    )

USER_V2_QUEUE = "mm.workflow.user.v2"
REPLAY_QUEUE = "mm.workflow"
MERGE_QUEUE = "mm.workflow.merge_automation"


@pytest.mark.parametrize(
    ("parent_queue", "child_queue"),
    [
        # The former normal-lane process polled these and routed to user.v2.
        (USER_V2_QUEUE, USER_V2_QUEUE),
        (REPLAY_QUEUE, USER_V2_QUEUE),
        # The former merge-lane process overrode user.v2 to the merge queue.
        (MERGE_QUEUE, MERGE_QUEUE),
    ],
)
def test_child_queue_matches_the_former_lane_process(parent_queue, child_queue):
    assert get_workflow_child_task_queue(parent_queue) == child_queue


_CHILD_PARENTS = {
    "merge": MoonMindMergeAutomationWorkflow._workflow_child_task_queue,
    "agent_run": MoonMindAgentRun._workflow_child_task_queue,
}


@workflow.defn(name="MM3937LaneChildProbe")
class _LaneChildProbe:
    @workflow.run
    async def run(self) -> str:
        return workflow.info().task_queue


@workflow.defn(name="MM3937LaneParentProbe")
class _LaneParentProbe:
    """Starts a child on the queue the real production method selects."""

    @workflow.run
    async def run(self, owner: str) -> dict[str, str]:
        queue = _CHILD_PARENTS[owner]()
        child_ran_on = await workflow.execute_child_workflow(
            _LaneChildProbe.run,
            id=f"{workflow.info().workflow_id}:child",
            task_queue=queue,
        )
        return {"routed": queue, "childRanOn": child_ran_on}


@activity.defn(name="integration.resolve_adapter_metadata")
async def _adapter_metadata_lane_probe(agent_id: str) -> dict[str, str]:
    return {"agentId": agent_id, "ranOn": activity.info().task_queue}


@workflow.defn(name="MM3937LaneAdapterMetadataProbe")
class _LaneAdapterMetadataProbe:
    """Resolves adapter metadata through the production AgentRun routing."""

    @workflow.run
    async def run(self) -> dict[str, str]:
        agent_run = MoonMindAgentRun.__new__(MoonMindAgentRun)
        return await agent_run._execute_routed_activity(
            "integration.resolve_adapter_metadata", "jules"
        )


_BUSY_LOCK = threading.Lock()
_BUSY_ENTERED: set[str] = set()
_BUSY_ACTIVE = [0]
_BUSY_PEAK = [0]
#: Below the SDK's 2s deadlock detector, so the probe runs production settings.
_BUSY_HOLD_SECONDS = 1.2


@workflow.defn(name="MM3937BusyLaneProbe")
class _BusyLaneProbe:
    """Holds one workflow-task slot (and executor thread) for a while."""

    @workflow.run
    async def run(self) -> str:
        with _BUSY_LOCK:
            _BUSY_ENTERED.add(workflow.info().workflow_id)
            _BUSY_ACTIVE[0] += 1
            _BUSY_PEAK[0] = max(_BUSY_PEAK[0], _BUSY_ACTIVE[0])
        try:
            time.sleep(_BUSY_HOLD_SECONDS)
        finally:
            with _BUSY_LOCK:
                _BUSY_ACTIVE[0] -= 1
        return "done"


async def _wait_for(predicate, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.05)


def _workflow_fleet_topology():
    from moonmind.workflows.temporal.workers import describe_configured_worker

    topology = describe_configured_worker(
        temporal_settings=settings.temporal.model_copy(
            update={
                "worker_fleet": WORKFLOW_FLEET,
                "workflow_worker_concurrency": 2,
                "merge_automation_workflow_worker_concurrency": 2,
            }
        )
    )
    assert topology.task_queues == (USER_V2_QUEUE, REPLAY_QUEUE, MERGE_QUEUE)
    return topology


@asynccontextmanager
async def _serving(workers):
    """Serve ``workers`` through the production ``serve_workers`` owner."""

    from moonmind.workflows.temporal.worker_lifecycle import serve_workers

    stop = asyncio.Event()
    ready = asyncio.Event()

    async def mark_ready():
        ready.set()

    serving = asyncio.create_task(serve_workers(workers, ready=mark_ready, stop=stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        yield
    finally:
        stop.set()
        await asyncio.wait_for(serving, timeout=30)


@pytest.mark.temporal_boundary
@pytest.mark.asyncio
async def test_workflow_fleet_activity_stays_on_the_parent_lane():
    """An external AgentRun resolves adapter metadata on its own lane.

    The former merge-lane process built its activity catalog with
    ``TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE`` overridden to the merge queue, so
    in-flight merge-lane histories recorded this command on that queue.  The
    shared process must emit the same command or those runs stop replaying.
    """

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner

    from moonmind.workflows.temporal.worker_runtime import _build_queue_workers

    topology = _workflow_fleet_topology()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        workers = _build_queue_workers(
            env.client,
            topology,
            {
                "workflows": [_LaneAdapterMetadataProbe],
                "activities": [_adapter_metadata_lane_probe],
                "workflow_runner": UnsandboxedWorkflowRunner(),
            },
        )
        async with _serving(workers):
            for parent_queue, lane_queue in (
                (MERGE_QUEUE, MERGE_QUEUE),
                (USER_V2_QUEUE, USER_V2_QUEUE),
                (REPLAY_QUEUE, USER_V2_QUEUE),
            ):
                handle = await env.client.start_workflow(
                    _LaneAdapterMetadataProbe.run,
                    id=f"mm3937-adapter-metadata-{parent_queue}",
                    task_queue=parent_queue,
                )
                assert await handle.result() == {
                    "agentId": "jules",
                    "ranOn": lane_queue,
                }
                history = await handle.fetch_history()
                assert [
                    event.activity_task_scheduled_event_attributes.task_queue.name
                    for event in history.events
                    if event.HasField("activity_task_scheduled_event_attributes")
                ] == [lane_queue]


@pytest.mark.temporal_boundary
@pytest.mark.asyncio
async def test_merge_lane_progresses_while_normal_lane_is_saturated():
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner

    from moonmind.workflows.temporal.worker_runtime import _build_queue_workers

    topology = _workflow_fleet_topology()
    _BUSY_ENTERED.clear()
    _BUSY_PEAK[0] = 0
    async with await WorkflowEnvironment.start_time_skipping() as env:
        workers = _build_queue_workers(
            env.client,
            topology,
            {
                "workflows": [_LaneParentProbe, _LaneChildProbe, _BusyLaneProbe],
                "activities": [],
                "workflow_runner": UnsandboxedWorkflowRunner(),
            },
        )
        assert [
            (worker.task_queue, worker.config()["max_concurrent_workflow_tasks"])
            for worker in workers
        ] == [(USER_V2_QUEUE, 2), (REPLAY_QUEUE, 2), (MERGE_QUEUE, 2)]

        # Busy runs hold slots in wall-clock time; skipping would expire them.
        with env.auto_time_skipping_disabled():
            async with _serving(workers):
                await _assert_merge_lane_progresses(env)


async def _assert_merge_lane_progresses(env) -> None:
    # Load the normal lane with more runs than its budget can hold at once.
    busy = [
        await env.client.start_workflow(
            _BusyLaneProbe.run,
            id=f"mm3937-busy-{index}",
            task_queue=USER_V2_QUEUE,
        )
        for index in range(6)
    ]
    await _wait_for(lambda: len(_BUSY_ENTERED) >= 1)

    # The merge lane makes progress while normal-lane work is still queued,
    # and its descendants stay on the merge lane exactly as the former
    # merge-lane process routed them.
    merge_result = await asyncio.wait_for(
        env.client.execute_workflow(
            _LaneParentProbe.run,
            "merge",
            id="mm3937-merge-parent",
            task_queue=MERGE_QUEUE,
        ),
        timeout=15,
    )
    assert merge_result == {"routed": MERGE_QUEUE, "childRanOn": MERGE_QUEUE}
    assert len(_BUSY_ENTERED) < len(busy), "merge lane waited behind normal lane"

    assert [await handle.result() for handle in busy] == ["done"] * len(busy)
    # The normal lane never exceeded its own workflow-task budget.
    assert 1 <= _BUSY_PEAK[0] <= 2

    # Replay-queue parents keep routing children to the start queue.
    replay_result = await env.client.execute_workflow(
        _LaneParentProbe.run,
        "agent_run",
        id="mm3937-replay-parent",
        task_queue=REPLAY_QUEUE,
    )
    assert replay_result == {"routed": USER_V2_QUEUE, "childRanOn": USER_V2_QUEUE}
