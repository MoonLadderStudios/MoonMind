"""Shared Temporal side-effect guards for tests that exercise Temporal clients."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class DummyWorkflowStartResult:
    workflow_id: str
    run_id: str


def install_temporal_client_adapter_guard(monkeypatch) -> None:
    """Patch Temporal lifecycle calls without importing Temporal during collection."""

    from moonmind.workflows.temporal.client import (
        ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV,
        TemporalClientAdapter,
    )

    async def mock_start_workflow(self, *args, **kwargs):
        workflow_id = kwargs.get("workflow_id") or kwargs.get("id") or "mm:unit-test"
        return DummyWorkflowStartResult(workflow_id=workflow_id, run_id=str(uuid4()))

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
    monkeypatch.setattr(
        TemporalClientAdapter, "describe_workflow", mock_describe_workflow
    )
