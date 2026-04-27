"""Unit-test collection and external-side-effect guards."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from moonmind.workflows.temporal.client import (
    ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV,
    TemporalClientAdapter,
)

collect_ignore_glob = [
    "api/routers/test_task_runs.py",
    "api/routers/test_mcp_tools.py",
    "api/routers/test_agent_queue_artifacts.py",
    "mcp/test_tool_registry.py",
]


@dataclass(frozen=True, slots=True)
class _DummyWorkflowStartResult:
    workflow_id: str
    run_id: str


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep unit tests from starting/signaling/canceling real Temporal workflows."""

    async def mock_start_workflow(self, *args, **kwargs):
        workflow_id = kwargs.get("workflow_id") or kwargs.get("id") or "mm:unit-test"
        return _DummyWorkflowStartResult(workflow_id=workflow_id, run_id=str(uuid4()))

    async def mock_noop(*args, **kwargs):
        return None

    async def mock_describe_workflow(*args, **kwargs):
        return None

    monkeypatch.setenv(ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV, "0")
    monkeypatch.setattr(TemporalClientAdapter, "start_workflow", mock_start_workflow)
    monkeypatch.setattr(TemporalClientAdapter, "update_workflow", mock_noop)
    monkeypatch.setattr(TemporalClientAdapter, "signal_workflow", mock_noop)
    monkeypatch.setattr(TemporalClientAdapter, "cancel_workflow", mock_noop)
    monkeypatch.setattr(TemporalClientAdapter, "terminate_workflow", mock_noop)
    monkeypatch.setattr(TemporalClientAdapter, "describe_workflow", mock_describe_workflow)
