import pytest

from temporalio.testing import ActivityEnvironment
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.openclaw_activities import openclaw_execute_activity


@pytest.mark.asyncio
async def test_openclaw_execute_activity_delegates(mocker):
    # Mock the OpenClaw executor
    mock_run = mocker.patch("moonmind.openclaw.execute.run_openclaw_execution")
    expected_result = AgentRunResult(
        summary="done",
        output_refs=[]
    )
    mock_run.return_value = expected_result

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="test",
        correlationId="123",
        idempotencyKey="key",
    )
    
    env = ActivityEnvironment()
    result = await env.run(openclaw_execute_activity, req)
    
    assert result == expected_result
    mock_run.assert_called_once_with(req)
