from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import map_temporal_state_to_projection
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)


@pytest.mark.asyncio
async def test_map_temporal_state_to_projection_success():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:123"
    desc.run_id = "run-123"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.close_time = start_time

    memo_data = {
        "entry": "run",
        "owner_id": "owner-1",
        "owner_type": "user",
        "input_ref": "input-1",
        "paused": True,
        "step_count": 5,
    }

    async def mock_memo():
        return memo_data

    desc.memo = mock_memo

    class MockSearchAttribute:
        def __init__(self, data):
            self.data = data

    desc.search_attributes = {
        "mm_repo": MockSearchAttribute("repo-1"),
        "mm_custom": MockSearchAttribute(b'{"key": "value"}'),
    }

    result = await map_temporal_state_to_projection(desc)

    assert result["workflow_id"] == "mm:123"
    assert result["run_id"] == "run-123"
    assert result["namespace"] == "moonmind"
    assert result["workflow_type"] == TemporalWorkflowType.RUN
    assert result["owner_id"] == "owner-1"
    assert result["owner_type"] == TemporalExecutionOwnerType.USER
    assert result["state"] == MoonMindWorkflowState.SUCCEEDED
    assert result["close_status"] == TemporalExecutionCloseStatus.COMPLETED
    assert result["entry"] == "run"
    assert result["input_ref"] == "input-1"
    assert result["paused"] is True
    assert result["step_count"] == 5
    assert result["search_attributes"]["mm_repo"] == "repo-1"
    assert result["search_attributes"]["mm_custom"] == {"key": "value"}
