import pytest
import asyncio
from typing import Any, Dict
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.client import WorkflowFailureError
from api_service.services.temporal.workflows.shared import AgentExecutionRequest, AgentRunResult
from api_service.services.temporal.workflows.agent_run import MoonMindAgentRun, publish_artifacts_activity, invoke_adapter_cancel

@workflow.defn(name="MoonMind.AuthProfileManager")
class MockAuthProfileManager:
    def __init__(self):
        self._shutdown = False
        self.pending_requests = []

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        self.pending_requests.append(payload)

    @workflow.signal
    def release_slot(self, payload: dict) -> None:
        pass

    @workflow.signal
    def report_cooldown(self, payload: dict) -> None:
        pass

    @workflow.run
    async def run(self, input_payload: dict) -> dict:
        while not self._shutdown:
            await workflow.wait_condition(lambda: len(self.pending_requests) > 0 or self._shutdown)
            if self._shutdown:
                break
            while self.pending_requests:
                req = self.pending_requests.pop(0)
                handle = workflow.get_external_workflow_handle(req["requester_workflow_id"])
                await handle.signal("slot_assigned", {"profile_id": "default-managed"})
        return {}


@pytest.mark.asyncio
async def test_agent_run_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockAuthProfileManager],
            activities=[publish_artifacts_activity, invoke_adapter_cancel],
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="default-managed",
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            
            # Start dummy manager
            manager_id = f"auth-profile-manager:{request.agent_id}"
            await env.client.start_workflow(
                MockAuthProfileManager.run,
                {"runtime_id": request.agent_id},
                id=manager_id,
                task_queue="agent-run-task-queue",
            )
            
            # Start workflow
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-1",
                task_queue="agent-run-task-queue",
            )
            
            # Signal completion
            result_payload = {"summary": "Success"}
            await handle.signal(MoonMindAgentRun.completion_signal, result_payload)
            
            result = await handle.result()
            
            assert isinstance(result, AgentRunResult)
            assert result.summary == "Success"

@pytest.mark.asyncio
async def test_agent_run_workflow_cancellation():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockAuthProfileManager],
            activities=[publish_artifacts_activity, invoke_adapter_cancel],
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="default-managed",
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            
            # Start dummy manager
            manager_id = f"auth-profile-manager:{request.agent_id}"
            await env.client.start_workflow(
                MockAuthProfileManager.run,
                {"runtime_id": request.agent_id},
                id=manager_id,
                task_queue="agent-run-task-queue",
            )
            
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-cancel",
                task_queue="agent-run-task-queue",
            )
            
            # Yield event loop briefly to allow slot_assigned to happen if we want to cancel mid wait
            await asyncio.sleep(0.1)
            
            # Cancel the workflow while it's waiting
            await handle.cancel()
            
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()
            
            # Verifies that workflow was cancelled. Check the full exception chain
            # since the top-level message may be generic.
            exc_str = str(exc_info.value).lower()
            cause_str = str(exc_info.value.__cause__).lower() if exc_info.value.__cause__ else ""
            assert "cancel" in exc_str or "cancel" in cause_str or isinstance(
                exc_info.value.__cause__, asyncio.CancelledError
            )
