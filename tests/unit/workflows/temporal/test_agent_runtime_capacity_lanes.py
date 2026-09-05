"""Control and cleanup keep progressing while the execution lane is saturated.

Source issue: MoonLadderStudios/MoonMind#3880 (remaining implementation 6,
AC6, AC7).

Long Omnigent executions occupy their worker slot for hours. Anything that has
to happen *while* they run — cancelling one, heartbeating a host lease so it is
not wrongly reclaimed, stopping a session, releasing leases, reclaiming an
orphaned host — cannot share their queue: at concurrency N every control slot
would already be held by the work it is supposed to control.

The agent-runtime fleet polls its execution queue and its control queue from
one worker process, each with its own activity budget, so this is a routing
property rather than another always-on service (reconciles with #3937).
"""

from __future__ import annotations

import pytest

from moonmind.config.settings import TemporalSettings, settings
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_CONTROL_TASK_QUEUE,
    AGENT_RUNTIME_FLEET,
    AGENT_RUNTIME_TASK_QUEUE,
    TemporalActivityCatalogError,
    build_default_activity_catalog,
)

#: Every step that must make progress while executions saturate their lane.
CONTROL_LANE_ACTIVITIES = (
    # Cancellation that queues behind the run it cancels is not cancellation.
    "agent_runtime.cancel",
    # Capacity admission the AgentRun workflow polls while it waits.
    "omnigent.admit_generic_host_capacity",
    # Liveness: a starved heartbeat expires a lease whose host is still alive.
    "omnigent.heartbeat_host_lease",
    # Cleanup: the owner of the capacity every queued run is waiting for.
    "omnigent.stop_provider_session",
    "omnigent.stop_host",
    "omnigent.release_leases",
    "integration.omnigent.oauth_host_janitor",
)

#: The long lane. These are the activities that saturate it.
EXECUTION_LANE_ACTIVITIES = (
    "integration.omnigent.execute",
    "integration.omnigent.profile_bound_execute",
    "omnigent.submit_turn",
)


def _queues() -> dict[str, str]:
    catalog = build_default_activity_catalog()
    return {
        entry.activity_type: entry.task_queue
        for entry in catalog.activities
        if entry.fleet == AGENT_RUNTIME_FLEET
    }


@pytest.mark.parametrize("activity_type", CONTROL_LANE_ACTIVITIES)
def test_control_and_cleanup_never_share_the_execution_lane(
    activity_type: str,
) -> None:
    queues = _queues()

    assert queues[activity_type] == AGENT_RUNTIME_CONTROL_TASK_QUEUE


@pytest.mark.parametrize("activity_type", EXECUTION_LANE_ACTIVITIES)
def test_turn_execution_stays_on_the_long_lane(activity_type: str) -> None:
    """The control lane must not become a second place to run long work."""

    queues = _queues()

    assert queues[activity_type] == AGENT_RUNTIME_TASK_QUEUE


def test_saturating_the_execution_lane_leaves_the_control_lane_free() -> None:
    """The property the routing exists for, at the deployment's own capacity."""

    queues = _queues()
    capacity = settings.temporal.agent_runtime_worker_concurrency or 0
    # AC7: the deployment default must carry the chosen 8/16 execution rows.
    assert capacity >= 16

    # Every execution slot is held by a long run.
    execution_lane = [
        activity_type
        for activity_type in EXECUTION_LANE_ACTIVITIES
        for _ in range(capacity)
        if queues[activity_type] == AGENT_RUNTIME_TASK_QUEUE
    ]
    assert len(execution_lane) >= capacity

    # Nothing that has to run now is waiting behind any of them.
    assert not [
        activity_type
        for activity_type in CONTROL_LANE_ACTIVITIES
        if queues[activity_type] == AGENT_RUNTIME_TASK_QUEUE
    ]


def test_the_agent_runtime_fleet_polls_both_lanes_from_one_worker() -> None:
    """One process, two budgets: no extra always-on container per lane."""

    catalog = build_default_activity_catalog()
    fleet = next(
        item for item in catalog.fleets if item.fleet == AGENT_RUNTIME_FLEET
    )

    assert AGENT_RUNTIME_TASK_QUEUE in fleet.task_queues
    assert AGENT_RUNTIME_CONTROL_TASK_QUEUE in fleet.task_queues


def test_a_shared_control_queue_is_rejected_at_build_time() -> None:
    """Configuring one queue for both lanes silently removes the guarantee."""

    shared = TemporalSettings(
        TEMPORAL_ACTIVITY_AGENT_RUNTIME_CONTROL_TASK_QUEUE=(
            AGENT_RUNTIME_TASK_QUEUE
        ),
    )

    with pytest.raises(TemporalActivityCatalogError, match="isolated"):
        build_default_activity_catalog(shared)
