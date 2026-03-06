from __future__ import annotations

from types import SimpleNamespace

from api_service.api.routers.executions import _serialize_execution
from api_service.db.models import MoonMindWorkflowState


def test_serialize_execution_treats_system_owner_id_as_system_owner_type() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="system",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.Run"),
        state=MoonMindWorkflowState.INITIALIZING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        started_at="2026-03-06T00:00:00Z",
        updated_at="2026-03-06T00:00:00Z",
        closed_at=None,
    )

    payload = _serialize_execution(record)

    assert payload.owner_type == "system"
    assert payload.owner_id == "system"
