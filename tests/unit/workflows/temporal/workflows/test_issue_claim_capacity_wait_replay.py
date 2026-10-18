"""The public AgentRun keeps claim authority across durable capacity waits."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import agent_run

LEASE = {"owner": "default/claim-capacity-wait", "attemptId": "same-attempt"}


class _ClaimActivities:
    def __init__(self):
        self.queued = asyncio.Event()
        self.renewals = []
        self.launches = 0
        self.lost = False

    @activity.defn(name="github_issue.renew_claim")
    async def renew(self, lease: dict) -> dict:
        assert lease == LEASE
        self.renewals.append(activity.info().current_attempt_scheduled_time)
        if self.lost:
            return {"status": "lost", "reasonCode": "claim_lease_lost"}
        return {
            "status": "renewed",
            **lease,
            "leaseExpiresAt": (
                activity.info().current_attempt_scheduled_time + timedelta(minutes=30)
            ).isoformat(),
        }

    @activity.defn(name="claim_capacity.queued")
    async def awaiting_capacity(self):
        self.queued.set()

    @activity.defn(name="claim_capacity.launch")
    async def launch(self):
        self.launches += 1


async def _queued_execution(self, request):
    # Replace provider execution only. The public run method, claim lifetime,
    # Activity routing, durable timers, signals and cancellation are real.
    self._capacity_requested = True
    await workflow.execute_activity(
        "claim_capacity.queued", start_to_close_timeout=timedelta(seconds=10)
    )
    await workflow.wait_condition(self.slot_assigned_event.is_set)
    await workflow.execute_activity(
        "claim_capacity.launch", start_to_close_timeout=timedelta(seconds=10)
    )
    return AgentRunResult(summary="queued work completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["grant", "cancel", "lease_lost"])
async def test_capacity_wait_outlives_renewal_cadence_restarts_and_replays(
    monkeypatch,
    outcome,
):
    monkeypatch.setattr(
        agent_run.MoonMindAgentRun, "_run_under_claim", _queued_execution
    )
    queue = "claim-capacity-" + outcome
    route = agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "github_issue.renew_claim"
    )
    monkeypatch.setattr(
        agent_run,
        "DEFAULT_ACTIVITY_CATALOG",
        SimpleNamespace(resolve_activity=lambda _: replace(route, task_queue=queue)),
    )
    async with await asyncio.wait_for(
        WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ),
        timeout=30,
    ) as env:
        probe = _ClaimActivities()
        options = {
            "task_queue": queue,
            "workflows": [agent_run.MoonMindAgentRun],
            "activities": [probe.renew, probe.awaiting_capacity, probe.launch],
            "workflow_runner": UnsandboxedWorkflowRunner(),
            "max_cached_workflows": 0,
        }
        async with Worker(env.client, **options):
            handle = await env.client.start_workflow(
                agent_run.MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agentKind="external",
                    agentId="omnigent",
                    correlationId=queue,
                    idempotencyKey=queue,
                    parameters={"issueClaimLease": LEASE},
                ),
                id=queue,
                task_queue=queue,
            )
            await asyncio.wait_for(probe.queued.wait(), timeout=10)
            # Use the production five-minute cadence and cross it twice.
            await asyncio.wait_for(env.sleep(610), timeout=20)
            assert probe.launches == 0
            assert len(probe.renewals) >= 3
            waiting_history = await handle.fetch_history()

        # Rebuild workflow state from history in a replacement worker before
        # any grant. A renewed claim cannot depend on the prior process.
        async with Worker(env.client, **options):
            if outcome == "grant":
                await handle.signal(
                    "slot_assigned", {"profile_id": "selected", "fencing_generation": 7}
                )
                result = await asyncio.wait_for(handle.result(), timeout=20)
                assert result.summary == "queued work completed"
                assert probe.launches == 1
            else:
                if outcome == "cancel":
                    await handle.cancel()
                else:
                    probe.lost = True
                    await asyncio.wait_for(env.sleep(301), timeout=20)
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=20)
                assert probe.launches == 0
            completed_history = await handle.fetch_history()

        for history in (waiting_history, completed_history):
            await Replayer(
                workflows=[agent_run.MoonMindAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ).replay_workflow(history)
