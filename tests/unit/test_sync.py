from datetime import UTC, datetime
import asyncio
from unittest.mock import Mock

from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import (
    map_temporal_state_to_projection,
    merged_memo_for_projection,
    merged_parameters_for_projection,
)
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)


def test_map_temporal_state_to_projection_success():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:123"
    desc.run_id = "run-123"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time

    memo_data = {
        "entry": "run",
        "owner_id": "owner-1",
        "owner_type": "user",
        "input_ref": "input-1",
        "paused": True,
        "step_count": 5,
    }
    desc.memo = memo_data

    class MockSearchAttribute:
        def __init__(self, data):
            self.data = data

    desc.search_attributes = {
        "mm_repo": MockSearchAttribute("repo-1"),
        "mm_custom": MockSearchAttribute(b'{"key": "value"}'),
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["workflow_id"] == "mm:123"
    assert result["run_id"] == "run-123"
    assert result["namespace"] == "moonmind"
    assert result["workflow_type"] == TemporalWorkflowType.RUN
    assert result["owner_id"] == "owner-1"
    assert result["owner_type"] == TemporalExecutionOwnerType.USER
    assert result["state"] == MoonMindWorkflowState.COMPLETED
    assert result["close_status"] == TemporalExecutionCloseStatus.COMPLETED
    assert result["entry"] == "run"
    assert result["input_ref"] == "input-1"
    assert result["paused"] is True
    assert result["step_count"] == 5
    assert result["search_attributes"]["mm_repo"] == "repo-1"
    assert result["search_attributes"]["mm_custom"] == {"key": "value"}


def test_map_temporal_state_to_projection_uses_search_attributes_for_owner_fields():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:456"
    desc.run_id = "run-456"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = None

    memo_data: dict[str, object] = {
        "entry": "run",
    }

    class MockSearchAttribute:
        def __init__(self, data):
            self.data = data

    desc.search_attributes = {
        "mm_owner_id": MockSearchAttribute(["owner-from-search"]),
        "mm_owner_type": MockSearchAttribute(["user"]),
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["owner_id"] == "owner-from-search"
    assert result["owner_type"] == TemporalExecutionOwnerType.USER


def test_map_temporal_state_to_projection_memo_parameters_empty_by_default():
    """Temporal memo typically does not contain targetRuntime/model/effort.

    These fields are stored in the canonical record's parameters column at
    creation time and must be merged during projection sync (see
    sync_execution_projection) to be visible in the API response.
    """
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:789"
    desc.run_id = "run-789"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = None
    desc.search_attributes = {}

    memo_data: dict[str, object] = {
        "entry": "run",
        "title": "Some task",
        "summary": "Running",
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    # Memo doesn't contain these execution parameters; they live in the
    # canonical record's parameters column set at create_execution time.
    params = result["parameters"]
    assert params.get("targetRuntime") is None
    assert params.get("model") is None
    assert params.get("effort") is None


def test_merged_parameters_for_projection_combines_canonical_with_memo_payload():
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.parameters = {
        "targetRuntime": "codex",
        "task": {"tool": {"name": "fix-ci"}},
    }
    payload = {"parameters": {"model": "gpt-5"}}
    merged = merged_parameters_for_projection(payload, canonical)
    assert merged["targetRuntime"] == "codex"
    assert merged["task"]["tool"]["name"] == "fix-ci"
    assert merged["model"] == "gpt-5"


def test_merged_parameters_for_projection_without_canonical_returns_memo_only():
    payload = {"parameters": {"targetRuntime": "jules"}}
    assert merged_parameters_for_projection(payload, None) == {"targetRuntime": "jules"}


# --- merged_memo_for_projection tests ---

def test_merged_memo_preserves_db_only_keys_absent_from_temporal_memo():
    """DB-written keys like taskRunId survive projection sync even though
    Temporal's memo is immutable and will never contain them."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {
        "title": "My Task",
        "summary": "Running",
        "taskRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
    }
    # Temporal's memo is a subset — taskRunId is absent (it's immutable after start)
    temporal_payload = {"memo": {"title": "My Task", "summary": "Executing step 3"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    # DB-only key survives
    assert merged["taskRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    # Temporal's value wins for keys present in both
    assert merged["summary"] == "Executing step 3"
    assert merged["title"] == "My Task"


def test_merged_memo_temporal_wins_for_overlapping_keys():
    """Temporal is authoritative for any key it supplies."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"summary": "Old summary", "taskRunId": "abc-123"}
    temporal_payload = {"memo": {"summary": "New summary from Temporal"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    assert merged["summary"] == "New summary from Temporal"
    assert merged["taskRunId"] == "abc-123"


def test_merged_memo_without_canonical_returns_temporal_memo_only():
    payload = {"memo": {"title": "task", "summary": "done"}}
    merged = merged_memo_for_projection(payload, None)
    assert merged == {"title": "task", "summary": "done"}


def test_merged_memo_with_empty_temporal_memo_returns_canonical_memo():
    """When Temporal provides no memo (rare), the canonical DB memo is used."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"taskRunId": "abc", "title": "task"}
    payload: dict = {"memo": {}}

    merged = merged_memo_for_projection(payload, canonical)
    assert merged["taskRunId"] == "abc"
    assert merged["title"] == "task"
