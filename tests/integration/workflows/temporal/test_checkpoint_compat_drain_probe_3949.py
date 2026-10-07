"""Automated checkpoint compat drain observation (MoonLadderStudios/MoonMind#3949).

Runs ``observe_checkpoint_compat_drain`` against a Temporal dev server with
real visibility and histories: pre-marker running and closed executions are
counted, post-marker executions and other queues are not, an all-post-marker
queue set observes a clean drain, and failed visibility or history reads stay
unknown (``None``) so the gate retains compat. This proves the probe, not the
drain state of any deployment.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.gates.checkpoint_compat_drain import (
    COMPAT_PATCH_ID,
    evaluate_checkpoint_compat_drain_observations,
)
from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_TASK_QUEUE
from moonmind.workflows.temporal.checkpoint_compat_drain_probe import (
    CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE,
    observe_checkpoint_compat_drain,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn(name=CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE)
class _PreMarkerTurn:
    """A history recorded before the artifacts-fleet patch existed."""

    @workflow.run
    async def run(self, finish: bool) -> None:
        if finish:
            return
        await workflow.execute_activity(
            "checkpoint_branch.turn.mark_running",
            {},
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name=CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE)
class _PostMarkerTurn:
    """A history that routes persistence to the artifacts queue."""

    @workflow.run
    async def run(self, finish: bool) -> None:
        workflow.patched(COMPAT_PATCH_ID)
        if finish:
            return
        await workflow.execute_activity(
            "checkpoint_branch.turn.mark_running",
            {},
            task_queue=ARTIFACTS_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=1),
        )


class _HistoryUnavailable:
    """Delegates visibility to a real client but fails every history read."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.data_converter = client.data_converter

    def list_workflows(self, *args, **kwargs):
        return self._client.list_workflows(*args, **kwargs)

    def get_workflow_handle(self, *args, **kwargs):
        raise RuntimeError("history service unavailable (3949 probe)")


async def _wait_until_listed(client: Client, query: str, expected: int) -> None:
    for _attempt in range(200):
        listed = [execution async for execution in client.list_workflows(query=query)]
        if len(listed) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"visibility never listed {expected} executions")


async def _wait_for_pending_activity(handle) -> None:
    for _attempt in range(200):
        description = await handle.describe()
        if description.raw_description.pending_activities:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("activity was never scheduled")


async def test_probe_observes_pre_marker_histories_and_fails_closed_3949() -> None:
    suffix = uuid4().hex
    pre_queue = f"mm.workflow.3949-pre-{suffix}"
    post_queue = f"mm.workflow.3949-post-{suffix}"
    other_queue = f"mm.other.3949-{suffix}"
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        client = env.client
        async with (
            Worker(
                client,
                task_queue=pre_queue,
                workflows=[_PreMarkerTurn],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                client,
                task_queue=post_queue,
                workflows=[_PostMarkerTurn],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                client,
                task_queue=other_queue,
                workflows=[_PreMarkerTurn],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            running = []
            for queue in (pre_queue, post_queue, other_queue):
                handle = await client.start_workflow(
                    CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE,
                    False,
                    id=f"turn-running-{queue}",
                    task_queue=queue,
                )
                running.append(handle)
                closed = await client.start_workflow(
                    CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE,
                    True,
                    id=f"turn-closed-{queue}",
                    task_queue=queue,
                )
                await closed.result()
            for handle in running:
                await _wait_for_pending_activity(handle)
            for status in ("Running", "Completed"):
                await _wait_until_listed(
                    client,
                    f'WorkflowType="{CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE}" '
                    f'AND ExecutionStatus="{status}"',
                    3,
                )

            observed = await observe_checkpoint_compat_drain(
                client, workflow_task_queues=[pre_queue, post_queue]
            )
            assert observed.open_pre_cutover_histories == 1
            assert observed.pending_old_queue_tasks == 1
            assert observed.supported_resets_pending == 1
            decision = evaluate_checkpoint_compat_drain_observations(observed)
            assert decision.may_remove_workflow_queue_handlers is False
            assert set(decision.blocking_dimensions) == {
                "open_pre_cutover_histories",
                "pending_old_queue_tasks",
                "supported_resets_pending",
            }

            drained = await observe_checkpoint_compat_drain(
                client, workflow_task_queues=[post_queue]
            )
            assert (
                drained.open_pre_cutover_histories,
                drained.pending_old_queue_tasks,
                drained.supported_resets_pending,
            ) == (0, 0, 0)
            assert evaluate_checkpoint_compat_drain_observations(
                drained
            ).may_remove_workflow_queue_handlers

            history_failure = await observe_checkpoint_compat_drain(
                _HistoryUnavailable(client),  # type: ignore[arg-type]
                workflow_task_queues=[post_queue],
            )
            assert history_failure.open_pre_cutover_histories is None
            assert history_failure.pending_old_queue_tasks is None
            assert history_failure.supported_resets_pending is None

            missing_namespace = Client(
                client.service_client, namespace=f"missing-3949-{suffix}"
            )
            visibility_failure = await observe_checkpoint_compat_drain(
                missing_namespace, workflow_task_queues=[post_queue]
            )
            assert visibility_failure.open_pre_cutover_histories is None
            assert visibility_failure.pending_old_queue_tasks is None
            assert visibility_failure.supported_resets_pending is None
            assert not evaluate_checkpoint_compat_drain_observations(
                visibility_failure
            ).may_remove_workflow_queue_handlers

            for handle in running:
                await handle.terminate("3949 probe test cleanup")
            statuses = {(await handle.describe()).status for handle in running}
            assert statuses == {WorkflowExecutionStatus.TERMINATED}
