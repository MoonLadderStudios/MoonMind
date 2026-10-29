"""Exercise the owner bound against Temporal's real untracked ALLOW_ALL starts."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import RecurringWorkflowDefinition
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.schedule_mapping import make_schedule_id
from tests.unit.services.test_recurring_workflows_service import recurring_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn(name="MoonMind.MergeAutomation")
class _Merge:
    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: False)


@workflow.defn(name="MoonMind.UserWorkflow")
class _Root:
    def __init__(self):
        self.phase = "waiting"
        self.ready = False
        self.done = False

    @workflow.signal
    def review_ready(self):
        self.ready = True

    @workflow.signal
    def finish(self):
        self.done = True

    @workflow.query
    def current_phase(self) -> str:
        return self.phase

    @workflow.run
    async def run(self, payload: dict):
        while True:
            result = await workflow.execute_activity(
                "test.admit", payload, start_to_close_timeout=timedelta(seconds=10)
            )
            if result["status"] == "admitted":
                break
            assert result["status"] == "waiting"
            await workflow.sleep(0.05)
        self.phase = "implementing"
        await workflow.execute_activity(
            "test.implementation_started", start_to_close_timeout=timedelta(seconds=5)
        )
        await workflow.wait_condition(lambda: self.ready or self.done)
        child = None
        try:
            if self.ready:
                child = await workflow.start_child_workflow(
                    _Merge.run, id=workflow.info().workflow_id + ":merge"
                )
                workflow.upsert_memo(
                    {
                        "implementationAvailability": {
                            "state": "review_ready",
                            "definitionId": payload["definitionId"],
                            "mergeWorkflowId": child.id,
                        }
                    }
                )
                self.phase = "review_ready"
                await workflow.wait_condition(lambda: self.done)
        finally:
            if child is not None:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)


class _Activities:
    def __init__(self, sessions, adapter):
        self.sessions = sessions
        self.adapter = adapter
        self.started = []

    @activity.defn(name="test.admit")
    async def admit(self, payload: dict) -> dict:
        info = activity.info()
        async with self.sessions() as session:
            return await RecurringWorkflowsService(
                session, temporal_client_adapter=self.adapter
            ).admit_issue_implementation(
                definition_id=UUID(payload["definitionId"]),
                workflow_id=info.workflow_id,
                run_id=info.workflow_run_id,
            )

    @activity.defn(name="test.implementation_started")
    async def implementation_started(self):
        self.started.append(activity.info().workflow_id)


async def _eventually(predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            value = await predicate()
            if value:
                return value
            await asyncio.sleep(0.05)


async def test_untracked_successor_blocks_refill_and_next_cron_implementation(tmp_path):
    # The time-skipping server has no Schedules API. This isolated real server
    # exercises the same DontTrackOverlapping behavior as deployment Temporal.
    async with recurring_db(tmp_path) as sessions:
        async with await WorkflowEnvironment.start_local() as env:
            definition_id = uuid4()
            queue = "test-bounded-issues-" + str(definition_id)
            adapter = TemporalClientAdapter(client=env.client)
            payload = {"definitionId": str(definition_id)}
            async with sessions() as session:
                session.add(
                    RecurringWorkflowDefinition(
                        id=definition_id,
                        name="Independent issue work",
                        cron="* * * * *",
                        timezone="UTC",
                        target={
                            "initialParameters": {
                                "task": {
                                    "steps": [
                                        {
                                            "tool": {
                                                "id": "github.load_issue_preset_brief",
                                                "inputs": {"issueSearch": {}},
                                            }
                                        }
                                    ]
                                }
                            }
                        },
                        policy={"overlap": {"mode": "skip"}},
                    )
                )
                await session.commit()
            activities = _Activities(sessions, adapter)
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[_Root, _Merge],
                activities=[activities.admit, activities.implementation_started],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                schedule = await env.client.create_schedule(
                    make_schedule_id(definition_id),
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            _Root.run,
                            payload,
                            id=f"mm:{definition_id}",
                            task_queue=queue,
                        ),
                        spec=ScheduleSpec(
                            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=1))]
                        ),
                        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                    ),
                    trigger_immediately=True,
                )

                async def started(count):
                    return len(activities.started) >= count

                async def phase(handle, expected):
                    return await handle.query(_Root.current_phase) == expected

                async def successor(owner_id, owner_run, ordinal):
                    async with sessions() as session:
                        service = RecurringWorkflowsService(
                            session, temporal_client_adapter=adapter
                        )
                        # The real frozen test action already uses this worker's
                        # queue. Admission is under test, not plan compilation.
                        service._ensure_schedule_action_current = AsyncMock()
                        return await service.start_review_ready_successor(
                            definition_id=definition_id,
                            workflow_id=owner_id,
                            run_id=owner_run,
                            ordinal=ordinal,
                        )

                try:
                    await _eventually(lambda: started(1))
                    first_id = activities.started[0]
                    first = env.client.get_workflow_handle(first_id)
                    first_run = (await first.describe()).run_id
                    await first.signal(_Root.review_ready)
                    await _eventually(lambda: phase(first, "review_ready"))
                    # Native schedule action IDs have second precision. Keep
                    # this independent occurrence out of the first ID's second.
                    await asyncio.sleep(1.1)
                    result = await successor(first_id, first_run, 0)
                    assert result["status"] in {"enqueued", "pending_dispatch"}
                    await _eventually(lambda: started(2))
                    second_id = activities.started[1]
                    second = env.client.get_workflow_handle(second_id)

                    running = {
                        action.workflow_id
                        for action in (await schedule.describe()).info.running_actions
                    }
                    assert first_id in running
                    assert second_id not in running  # Reproduce the actual server trap.
                    for ordinal in (1, 2):
                        await asyncio.sleep(1.1)
                        assert (await successor(first_id, first_run, ordinal))[
                            "status"
                        ] == "skipped"
                        assert activities.started == [first_id, second_id]

                    # Once the tracked owner exits, cron can start another root
                    # while the untracked successor still implements. Its real
                    # admission Activity must hold before any implementation.
                    await first.signal(_Root.finish)
                    await first.result()

                    async def next_cron():
                        for action in (await schedule.describe()).info.running_actions:
                            if action.workflow_id not in {first_id, second_id}:
                                return action.workflow_id
                        return None

                    third_id = await _eventually(next_cron)
                    third = env.client.get_workflow_handle(third_id)
                    await _eventually(lambda: phase(third, "waiting"))
                    await asyncio.sleep(1.1)
                    assert activities.started == [first_id, second_id]

                    await second.signal(_Root.review_ready)
                    await _eventually(lambda: phase(second, "review_ready"))
                    await _eventually(lambda: started(3))
                    assert activities.started == [first_id, second_id, third_id]
                finally:
                    await schedule.delete()
                    async for execution in env.client.list_workflows():
                        if (
                            execution.id.startswith(f"mm:{definition_id}-")
                            and execution.parent_id is None
                        ):
                            await env.client.get_workflow_handle(execution.id).cancel()
