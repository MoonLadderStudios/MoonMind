"""A live merge child cleans up before its parent releases issue authority."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tests.unit.workflows.temporal.test_review_ready_successor_replay import _Owner


@workflow.defn(name="ReviewReadyCancellationOwner")
class _Parent:
    @workflow.run
    async def run(self, _case: dict) -> None:
        self.owner = _Owner()
        self.owner._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
        self.owner._trusted_issue_context = {
            "trustedSource": "moonmind.github.get_issue",
            "issueClaimLease": {
                "owner": workflow.info().workflow_id,
                "attemptId": "attempt",
            },
        }
        try:
            await self.owner._maybe_start_merge_gate(
                parameters={"system": {"recurrence": {"definitionId": "definition"}}},
                pull_request_url="https://github.com/org/repo/pull/1",
            )
        finally:
            await workflow.execute_activity(
                "test.release", start_to_close_timeout=timedelta(seconds=10)
            )

    @workflow.query
    def phase(self) -> str:
        return (self.owner._publish_context.get("implementationSuccessor") or {}).get(
            "status", "activity"
        )


@workflow.defn(name="MoonMind.MergeAutomation")
class _LiveMerge:
    @workflow.run
    async def run(self, _payload: dict) -> None:
        self.live = True
        try:
            await workflow.wait_condition(lambda: False)
        finally:
            await workflow.execute_activity(
                "test.cleanup", start_to_close_timeout=timedelta(seconds=10)
            )

    @workflow.query
    def started(self) -> bool:
        return self.live


class _Activities:
    def __init__(self, phase):
        self.expected_phase = phase
        self.entered = asyncio.Event()
        self.markers = []
        self.renewals = 0

    @activity.defn(name="recurring.review_ready_successor")
    async def successor(self, _payload: dict) -> dict:
        self.entered.set()
        if self.expected_phase == "activity":
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.02)
        return {"status": "skipped"}

    @activity.defn(name="github_issue.renew_claim")
    async def renew(self, lease: dict) -> dict:
        self.renewals += 1
        if self.renewals > 1:
            return {"status": "lost"}
        return {
            **lease,
            "status": "renewed",
            "leaseExpiresAt": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        }

    @activity.defn(name="test.cleanup")
    async def cleanup(self) -> None:
        await asyncio.sleep(0.05)
        self.markers.append("child.cleanup")

    @activity.defn(name="test.release")
    async def release(self) -> None:
        self.markers.append("parent.release")


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["parent_cancel", "lease_loss"])
@pytest.mark.parametrize("phase", ["activity", "wait"])
async def test_cancel_drains_live_merge_before_parent_release(
    cause, phase, monkeypatch
):
    if cause == "lease_loss":
        # A live Activity prevents test-server virtual-time jumps. Shorten
        # only the fixture's lease cadence so renewal loss arrives naturally.
        from moonmind.workflows.temporal import github_issue_lease_workflow

        monkeypatch.setattr(github_issue_lease_workflow, "RENEW_SECONDS", 1)
    activities = _Activities(phase)
    queue = f"test-review-cancel-{cause}-{phase}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_Parent, _LiveMerge],
            activities=[
                activities.successor,
                activities.renew,
                activities.cleanup,
                activities.release,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            parent = await env.client.start_workflow(
                _Parent.run, {}, id=queue, task_queue=queue
            )
            await asyncio.wait_for(activities.entered.wait(), timeout=15)
            merge = env.client.get_workflow_handle(queue + ":merge")
            assert await merge.query(_LiveMerge.started)
            if phase == "wait":
                for _ in range(50):
                    if await parent.query(_Parent.phase) == "skipped":
                        break
                    await asyncio.sleep(0.01)
                assert await parent.query(_Parent.phase) == "skipped"
            if cause == "parent_cancel":
                await parent.cancel()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(parent.result(), timeout=15)
            assert activities.markers == ["child.cleanup", "parent.release"]
            assert (await merge.describe()).status == WorkflowExecutionStatus.CANCELED
