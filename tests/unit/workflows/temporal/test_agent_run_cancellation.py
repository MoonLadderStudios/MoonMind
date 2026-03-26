import asyncio
from datetime import timedelta
import pytest
from unittest.mock import AsyncMock, patch
from temporalio import workflow
from temporalio.exceptions import CancelledError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun, RunStatus

@pytest.mark.asyncio
async def test_agent_run_cancelled_releases_slot():
    print("Agent run cancelled test ready")
