"""Automated drain observation for the checkpoint compat registration.

Source issue: MoonLadderStudios/MoonMind#3949.

The workflow fleet keeps the ``checkpoint_branch.turn.*`` handlers registered
only for histories recorded before the ``checkpoint-branch-artifact-fleet-v1``
patch marker. This module observes the Temporal deployment it is connected to
and feeds the existing gate
(:func:`moonmind.gates.checkpoint_compat_drain.collect_checkpoint_compat_drain_observations`)
instead of asking an operator to run the queries by hand:

- ``open_pre_cutover_histories``: running ``MoonMind.CheckpointBranchTurn``
  executions on the workflow fleet's task queues whose history has no
  artifacts-fleet patch marker;
- ``pending_old_queue_tasks``: ``checkpoint_branch.turn.*`` activities
  scheduled in those running histories to a queue other than the artifacts
  queue and not yet closed;
- ``supported_resets_pending``: closed executions Temporal still retains
  whose history has no marker. Resetting one replays ``workflow.patched``
  as false, so it would schedule persistence on the workflow queue again.
  The count reaches zero when those histories leave retention.

Visibility or history failures make the affected dimension ``None``
(unobservable), which the gate treats as retain. Observing a deployment does
not change it; removal remains a separate, reviewed change.

Run ``python -m moonmind.workflows.temporal.checkpoint_compat_drain_probe``
inside a MoonMind worker or API container. It connects with the deployment's
Temporal settings, derives the workflow fleet's task queues from the worker
topology, and prints the observations and gate decision as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from typing import Any, Sequence

from temporalio.client import Client, WorkflowExecutionStatus

from moonmind.gates.checkpoint_compat_drain import (
    COMPAT_PATCH_ID,
    CheckpointCompatDrainObservations,
    collect_checkpoint_compat_drain_observations,
    evaluate_checkpoint_compat_drain_observations,
    retention_reason,
)
from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_TASK_QUEUE

CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE = "MoonMind.CheckpointBranchTurn"
_PERSISTENCE_PREFIX = "checkpoint_branch.turn."
_PATCH_MARKER_NAME = "core_patch"
_PATCH_DETAILS_KEY = "patch-data"
_CLOSE_EVENT_FIELDS = (
    "activity_task_completed_event_attributes",
    "activity_task_failed_event_attributes",
    "activity_task_timed_out_event_attributes",
    "activity_task_canceled_event_attributes",
)


async def _history_observation(
    client: Client,
    *,
    workflow_id: str,
    run_id: str | None,
    artifacts_task_queue: str,
) -> tuple[bool, int]:
    """Return (has artifacts-fleet patch marker, open old-queue persistence)."""

    history = await client.get_workflow_handle(
        workflow_id, run_id=run_id
    ).fetch_history()
    has_marker = False
    old_queue_scheduled: set[int] = set()
    closed: set[int] = set()
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            attrs = event.marker_recorded_event_attributes
            if (
                attrs.marker_name == _PATCH_MARKER_NAME
                and _PATCH_DETAILS_KEY in attrs.details
            ):
                (patch,) = await client.data_converter.decode(
                    attrs.details[_PATCH_DETAILS_KEY].payloads
                )
                if isinstance(patch, dict) and patch.get("id") == COMPAT_PATCH_ID:
                    has_marker = True
        elif event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if (
                attrs.activity_type.name.startswith(_PERSISTENCE_PREFIX)
                and attrs.task_queue.name != artifacts_task_queue
            ):
                old_queue_scheduled.add(event.event_id)
        else:
            for field in _CLOSE_EVENT_FIELDS:
                if event.HasField(field):
                    closed.add(getattr(event, field).scheduled_event_id)
                    break
    return has_marker, len(old_queue_scheduled - closed)


async def observe_checkpoint_compat_drain(
    client: Client,
    *,
    workflow_task_queues: Sequence[str],
    artifacts_task_queue: str = ARTIFACTS_TASK_QUEUE,
) -> CheckpointCompatDrainObservations:
    """Observe the three drain dimensions from the connected deployment."""

    queues = ", ".join(f'"{queue}"' for queue in workflow_task_queues)
    query = (
        f'WorkflowType="{CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE}" '
        f"AND TaskQueue IN ({queues})"
    )
    try:
        executions = [
            execution async for execution in client.list_workflows(query=query)
        ]
    except Exception:
        return collect_checkpoint_compat_drain_observations(
            open_pre_cutover_histories=None,
            pending_old_queue_tasks=None,
            supported_resets_pending=None,
        )

    open_histories: int | None = 0
    pending_tasks: int | None = 0
    retained_resets: int | None = 0
    for execution in executions:
        running = execution.status == WorkflowExecutionStatus.RUNNING
        if running and open_histories is None:
            continue
        if not running and retained_resets is None:
            continue
        try:
            has_marker, pending = await _history_observation(
                client,
                workflow_id=execution.id,
                run_id=execution.run_id,
                artifacts_task_queue=artifacts_task_queue,
            )
        except Exception:
            if running:
                open_histories = None
                pending_tasks = None
            else:
                retained_resets = None
            continue
        if running:
            open_histories += 0 if has_marker else 1
            pending_tasks += pending
        elif not has_marker:
            retained_resets += 1
    return collect_checkpoint_compat_drain_observations(
        open_pre_cutover_histories=open_histories,
        pending_old_queue_tasks=pending_tasks,
        supported_resets_pending=retained_resets,
    )


async def _main_async() -> dict[str, Any]:
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.client import get_temporal_client
    from moonmind.workflows.temporal.workers import (
        WORKFLOW_FLEET,
        build_worker_topology,
    )

    client = await get_temporal_client(
        settings.temporal.address, settings.temporal.namespace
    )
    workflow_queues = build_worker_topology(fleet=WORKFLOW_FLEET).task_queues
    observations = await observe_checkpoint_compat_drain(
        client, workflow_task_queues=workflow_queues
    )
    decision = evaluate_checkpoint_compat_drain_observations(observations)
    return {
        "namespace": settings.temporal.namespace,
        "workflowTaskQueues": list(workflow_queues),
        "observations": asdict(observations),
        "decision": asdict(decision),
        "reason": retention_reason(decision),
    }


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="checkpoint-compat-drain-probe",
        description=(
            "Observe the workflow-queue checkpoint compat drain state of the "
            "connected Temporal deployment (read-only)."
        ),
    ).parse_args(argv)
    print(json.dumps(asyncio.run(_main_async()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
