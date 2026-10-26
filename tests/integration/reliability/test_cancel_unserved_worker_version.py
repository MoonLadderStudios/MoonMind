"""Replay the 2026-09-13 slot-wait cancel stranded by a worker replacement."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.reliability.test_unversioned_cutover_journey import connect
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class CancelSlotWaitChild:
    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: False)

    @workflow.query
    def waiting(self):
        return True


@workflow.defn
class CancelSlotWaitParent:
    @workflow.run
    async def run(self):
        await workflow.execute_child_workflow(
            CancelSlotWaitChild.run, id=workflow.info().workflow_id + ":slot"
        )


async def test_cancel_preserves_live_state_until_a_replacement_worker_serves_it():
    client = await connect()
    queue = "cancel-unserved-" + uuid4().hex

    def worker():
        return Worker(
            client,
            task_queue=queue,
            workflows=[CancelSlotWaitParent, CancelSlotWaitChild],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

    async with worker():
        parent = await client.start_workflow(
            CancelSlotWaitParent.run,
            id=queue,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
        )
        child = client.get_workflow_handle(queue + ":slot")
        for _ in range(100):
            try:
                if await child.query("waiting"):
                    break
            except Exception:
                # The child may not exist or serve queries until its first task;
                # retry within the bounded readiness loop before failing the test.
                pass
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Slot-wait child did not start")

    # The worker is replaced: no poller serves the queue while cancel arrives.
    record = TemporalExecutionCanonicalRecord(
        workflow_id=parent.id,
        run_id=parent.first_execution_run_id,
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        state=MoonMindWorkflowState.AWAITING_SLOT,
        paused=False,
        waiting_reason="provider_profile_slot",
        entry="user_workflow",
        owner_type=TemporalExecutionOwnerType.USER,
    )
    session = MagicMock()
    loaded = MagicMock()
    loaded.scalar_one.return_value = record
    session.execute = AsyncMock(return_value=loaded)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = TemporalExecutionService(session)
    service._client_adapter = TemporalClientAdapter(client)
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(side_effect=lambda row: row)
    service._sync_integration_correlation_record = AsyncMock()
    service._best_effort_terminate_workflow_scoped_managed_sessions = AsyncMock()
    service._fan_out_dependency_resolution = AsyncMock()

    accepted = await service.cancel_execution(
        workflow_id=parent.id, reason="Operator canceled", graceful=True
    )
    description = await parent.describe()
    assert description.status is WorkflowExecutionStatus.RUNNING
    assert description.raw_description.pending_workflow_task.attempt == 1
    assert accepted.state is MoonMindWorkflowState.AWAITING_SLOT
    assert accepted.close_status is None
    assert accepted.closed_at is None
    assert accepted.waiting_reason == "provider_profile_slot"
    assert accepted.memo["summary"].startswith("Cancellation requested.")
    service._fan_out_dependency_resolution.assert_not_awaited()

    # The replacement worker delivers the original cancellation request.
    async with worker():
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(parent.result(), timeout=30)
        assert (await parent.describe()).status is WorkflowExecutionStatus.CANCELED
        assert (await child.describe()).status is WorkflowExecutionStatus.CANCELED
        confirmed = await service.cancel_execution(
            workflow_id=parent.id, reason="Operator canceled", graceful=True
        )
        assert confirmed.state is MoonMindWorkflowState.CANCELED
        assert confirmed.waiting_reason is None
        service._fan_out_dependency_resolution.assert_awaited_once()
        for handle, cls in [
            (parent, CancelSlotWaitParent),
            (child, CancelSlotWaitChild),
        ]:
            history = await handle.fetch_history()
            await Replayer(
                workflows=[cls], workflow_runner=UnsandboxedWorkflowRunner()
            ).replay_workflow(history)
