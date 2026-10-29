"""Old and new merge-wait histories replay with the same durable owner."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


class _Owner(MoonMindRunWorkflow):
    legacy = False

    def _patched_or_false_outside_workflow(self, marker):
        if self.legacy and marker == "run-review-ready-successor-v1":
            return False
        return super()._patched_or_false_outside_workflow(marker)

    def _workflow_patch_enabled(self, _marker):
        return False

    def _workflow_child_task_queue(self):
        return workflow.info().task_queue

    def _build_merge_gate_start_payload(self, **_kwargs):
        return {
            "idempotencyKey": workflow.info().workflow_id + ":merge",
            "mergeAutomationConfig": {"finishMode": "merge"},
        }

    def _set_state(self, *_args, **_kwargs):
        pass

    def _update_memo(self):
        pass

    def _update_search_attributes(self):
        pass

    def _execute_kwargs_for_route(self, _route):
        return {
            "task_queue": workflow.info().task_queue,
            "start_to_close_timeout": timedelta(seconds=5),
        }


class _OldOwner(_Owner):
    legacy = True


async def _merge(owner):
    owner._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
    owner._trusted_issue_context = {"trustedSource": "moonmind.github.get_issue"}
    await owner._maybe_start_merge_gate(
        parameters={"system": {"recurrence": {"definitionId": "definition"}}},
        pull_request_url="https://github.com/org/repo/pull/1",
    )
    assert not owner._awaiting_external
    assert "implementationAvailability" not in owner._publish_context
    return owner._publish_context["mergeAutomationStatus"]


@workflow.defn(name="ReviewReadyReplayFixture")
class _OldFixture:
    @workflow.run
    async def run(self) -> str:
        return await _merge(_OldOwner())


@workflow.defn(name="ReviewReadyReplayFixture")
class _NewFixture:
    @workflow.run
    async def run(self) -> str:
        return await _merge(_Owner())


@workflow.defn(name="MoonMind.MergeAutomation")
class _MergeChild:
    @workflow.run
    async def run(self, _payload: dict) -> dict:
        await workflow.sleep(1)
        return {"status": "merged"}


@activity.defn(name="recurring.review_ready_successor")
async def _successor(_payload: dict) -> dict:
    return {"status": "enqueued"}


@pytest.mark.asyncio
async def test_pre_and_post_handoff_histories_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for label, impl in (("old", _OldFixture), ("new", _NewFixture)):
            queue = "test-review-ready-" + label
            # Both parent and child use this test queue; no deployment work is
            # submitted and the successor Activity only records its input.
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[impl, _MergeChild],
                activities=[_successor],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    impl.run, id=queue, task_queue=queue
                )
                assert await handle.result() == "merged"
                histories.append(await handle.fetch_history())
    replayer = Replayer(
        workflows=[_NewFixture], workflow_runner=UnsandboxedWorkflowRunner()
    )
    for history in histories:
        await replayer.replay_workflow(history)
