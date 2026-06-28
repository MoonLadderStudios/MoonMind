from __future__ import annotations

from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_execute_activity,
)


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(mock_run):
    expected = AgentRunResult(summary="done", outputRefs=[])
    mock_run.return_value = expected

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )
    result = await ActivityEnvironment().run(omnigent_execute_activity, req)

    assert result == expected
    mock_run.assert_called_once_with(req)
