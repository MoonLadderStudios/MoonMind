"""Retained Jules merge histories and pending pre-binding activity recovery."""

import asyncio
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.jules_merge import continue_jules_merge

URL = "https://github.com/org/repo/pull/123"
BOUND = {"pr_url": URL, "expected_repository": "org/repo", "target_branch": "release"}


def _execute(payload):
    return workflow.execute_activity(
        "jules.merge_fixture",
        payload,
        task_queue=f"{workflow.info().task_queue}-activities",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="JulesMergeCutover")
class _LegacyMerge:
    @workflow.run
    async def run(self) -> dict:
        return await _execute({"pr_url": URL})

    @workflow.query
    def waiting(self) -> bool:
        return True


@workflow.defn(name="JulesMergeCutover")
class _CurrentMerge:
    @workflow.run
    async def run(self) -> dict:
        result = await _execute({"pr_url": URL})
        return await continue_jules_merge(
            result,
            authored_payload=BOUND,
            execute_merge=_execute,
        )


@activity.defn(name="jules.merge_fixture")
async def _completed_merge(payload: dict) -> dict:
    return {"merged": True, "mergeSha": "c" * 40}


@pytest.mark.asyncio
async def test_jules_completed_legacy_history_replays_without_new_merge():
    queue = "jules-completed-history"
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=queue,
            workflows=[_LegacyMerge],
            max_cached_workflows=0,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            env.client,
            task_queue=f"{queue}-activities",
            activities=[_completed_merge],
        ),
    ):
        handle = await env.client.start_workflow(
            _LegacyMerge.run, id=queue, task_queue=queue
        )
        assert (await handle.result())["merged"] is True
        history = await handle.fetch_history()
    await Replayer(
        workflows=[_CurrentMerge],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_jules_pending_legacy_activity_recovers_and_new_history_replays():
    queue = "jules-pending-history"
    calls = []

    @activity.defn(name="jules.merge_fixture")
    async def merge(payload: dict) -> dict:
        calls.append(payload)
        if not payload.get("expected_repository"):
            return {"merged": False, "reasonCode": "merge_authority_required"}
        if not payload.get("expected_head_sha"):
            return {
                "merged": False,
                "reasonCode": "merge_head_resolved",
                "expectedHeadSha": "a" * 40,
            }
        return {"merged": True, "mergeSha": "c" * 40}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_LegacyMerge],
            max_cached_workflows=0,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _LegacyMerge.run, id=queue, task_queue=queue
            )
            assert await asyncio.wait_for(handle.query(_LegacyMerge.waiting), 15)
            pending = await handle.fetch_history()
            assert any(
                event.HasField("activity_task_scheduled_event_attributes")
                for event in pending.events
            )

        async with (
            Worker(
                env.client,
                task_queue=queue,
                workflows=[_CurrentMerge],
                max_cached_workflows=0,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(env.client, task_queue=f"{queue}-activities", activities=[merge]),
        ):
            assert (await asyncio.wait_for(handle.result(), 30))["merged"] is True
            history = await handle.fetch_history()

    assert calls == [{"pr_url": URL}, BOUND, {**BOUND, "expected_head_sha": "a" * 40}]
    await Replayer(
        workflows=[_CurrentMerge],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
