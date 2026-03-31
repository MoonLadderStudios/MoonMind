from datetime import UTC, datetime
from types import SimpleNamespace
from api_service.api.routers.executions import _serialize_execution
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType
import pytest

def test_serialize_execution_includes_repository():
    # Setup a mock execution record
    record = SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Test Task"},
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
        entry="run",
        parameters={},
        owner_id="system",
    )
    
    # Test 1: repository in task payload
    record.parameters = {
        "task": {
            "repository": "MoonLadderStudios/MoonMind"
        }
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/MoonMind"

    # Test 2: repository in params directly
    record.parameters = {
        "repository": "MoonLadderStudios/OtherRepo"
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/OtherRepo"

    # Test 3: repository in both (task takes precedence)
    record.parameters = {
        "task": {
            "repository": "MoonLadderStudios/MoonMind"
        },
        "repository": "MoonLadderStudios/OtherRepo"
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/MoonMind"

    # Test 4: no repository
    record.parameters = {}
    result = _serialize_execution(record)
    assert result.repository is None
