import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.client import WorkflowFailureError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from api_service.services.temporal.workflows.agent_run import MoonMindAgentRun, publish_artifacts_activity, invoke_adapter_cancel

@pytest.mark.asyncio
async def test_agent_run_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun],
            activities=[publish_artifacts_activity, invoke_adapter_cancel],
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="prof-1",
                correlation_id="corr-1",
                idempotency_key="idem-1",
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
            workflows=[MoonMindAgentRun],
            activities=[publish_artifacts_activity, invoke_adapter_cancel],
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="prof-1",
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-cancel",
                task_queue="agent-run-task-queue",
            )
            
            # Cancel the workflow while it's waiting
            await handle.cancel()
            
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()
            
            # Verifies that workflow was cancelled. The mock activity invoke_adapter_cancel
            # should have been called in the background. In a true unit test we might mock it
            # and verify call count.
            assert "cancel" in str(exc_info.value).lower()
