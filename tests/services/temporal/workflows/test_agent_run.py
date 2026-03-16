import pytest
import asyncio
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun, publish_artifacts_activity, invoke_adapter_cancel

@pytest.mark.asyncio
async def test_agent_run_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun],
            activities=[publish_artifacts_activity, invoke_adapter_cancel],
            workflow_runner=UnsandboxedWorkflowRunner(),
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
            workflow_runner=UnsandboxedWorkflowRunner(),
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
            
            # Verifies that workflow was cancelled. Check the full exception chain
            # since the top-level message may be generic.
            exc_str = str(exc_info.value).lower()
            cause_str = str(exc_info.value.__cause__).lower() if exc_info.value.__cause__ else ""
            assert "cancel" in exc_str or "cancel" in cause_str or isinstance(
                exc_info.value.__cause__, asyncio.CancelledError
            )
