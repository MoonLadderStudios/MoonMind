"""A canceled merge gate waits for its resolver's owned cleanup."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import INTEGRATIONS_TASK_QUEUE
from moonmind.workflows.temporal.workflows import merge_automation as module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from tests.unit.workflows.temporal.test_merge_automation_continuation_replayer import (
    _payload,
    _ready,
)


@workflow.defn(name="MoonMind.UserWorkflow")
class _ResolverWithCleanup:
    def __init__(self):
        self.started = False

    @workflow.run
    async def run(self, _request: dict) -> None:
        self.started = True
        try:
            await workflow.wait_condition(lambda: False)
        finally:
            await asyncio.shield(
                workflow.execute_activity(
                    "test.resolver_cleanup",
                    task_queue=workflow.info().task_queue,
                    start_to_close_timeout=timedelta(seconds=10),
                    cancellation_type=(
                        workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
                    ),
                )
            )

    @workflow.query
    def is_started(self) -> bool:
        return self.started


class _Cleanup:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False

    @activity.defn(name="test.resolver_cleanup")
    async def run(self) -> None:
        self.entered.set()
        await self.release.wait()
        self.completed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["drain", "retained-history"])
async def test_canceled_merge_waits_for_resolver_cleanup_and_replays(
    monkeypatch, legacy
):
    async def no_artifact(self, **_kwargs):
        return None

    async def prepared(self, request, **_kwargs):
        return request

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", no_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_prepare_omnigent_resolver_request", prepared
    )
    original_patched = module.workflow.patched
    if legacy:
        monkeypatch.setattr(
            module.workflow,
            "patched",
            lambda name: (
                False
                if name == module.MERGE_AUTOMATION_RESOLVER_CANCELLATION_DRAIN_PATCH
                else original_patched(name)
            ),
        )
    cleanup = _Cleanup()
    queue = f"resolver-drain-{uuid4()}"
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with env.auto_time_skipping_disabled():
            await env.client.operator_service.add_search_attributes(
                AddSearchAttributesRequest(
                    namespace=env.client.namespace,
                    search_attributes={
                        name: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                        for name in (
                            "mm_owner_id",
                            "mm_owner_type",
                            "mm_entry",
                            "mm_repo",
                            "mm_state",
                        )
                    },
                )
            )
            async with (
                Worker(
                    env.client, task_queue=INTEGRATIONS_TASK_QUEUE, activities=[_ready]
                ),
                Worker(
                    env.client,
                    task_queue=child_queue,
                    workflows=[_ResolverWithCleanup],
                    activities=[cleanup.run],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                gate = await env.client.start_workflow(
                    MoonMindMergeAutomationWorkflow.run,
                    _payload("cancel-drain"),
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=30),
                )
                child = None
                try:
                    for _ in range(100):
                        history = await gate.fetch_history()
                        child_id = next(
                            (
                                e.child_workflow_execution_started_event_attributes.workflow_execution.workflow_id
                                for e in history.events
                                if e.HasField(
                                    "child_workflow_execution_started_event_attributes"
                                )
                            ),
                            None,
                        )
                        if child_id:
                            child = env.client.get_workflow_handle(child_id)
                            if await child.query("is_started"):
                                break
                        await asyncio.sleep(0.02)
                    assert child is not None and await child.query("is_started")
                    await gate.cancel()
                    if not legacy:
                        await asyncio.wait_for(cleanup.entered.wait(), timeout=5)
                        assert (
                            await gate.describe()
                        ).status == WorkflowExecutionStatus.RUNNING
                        cleanup.release.set()
                    result = await asyncio.wait_for(gate.result(), timeout=10)
                    assert result["status"] == ("failed" if legacy else "canceled")
                    if legacy:
                        assert not cleanup.completed
                    else:
                        assert cleanup.completed
                        assert (
                            await child.describe()
                        ).status == WorkflowExecutionStatus.CANCELED
                        assert (await gate.describe()).close_time >= (
                            await child.describe()
                        ).close_time
                    history = await gate.fetch_history()
                finally:
                    cleanup.release.set()
                    if (
                        await gate.describe()
                    ).status == WorkflowExecutionStatus.RUNNING:
                        await gate.cancel()

    # A retained history has no new patch marker. Replay decides that branch
    # from its recorded commands, without the fixture's override.
    monkeypatch.setattr(module.workflow, "patched", original_patched)
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
