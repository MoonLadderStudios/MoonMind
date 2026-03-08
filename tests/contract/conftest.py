import pytest

@pytest.fixture(autouse=True)
def mock_temporal_client_adapter(monkeypatch):
    import uuid
    from dataclasses import dataclass

    from moonmind.workflows.temporal.client import TemporalClientAdapter

    @dataclass(frozen=True, slots=True)
    class DummyWorkflowStartResult:
        workflow_id: str
        run_id: str

    async def mock_start_workflow(self, *args, **kwargs):
        workflow_id = kwargs.get("workflow_id") or "mm:dummy"
        return DummyWorkflowStartResult(
            workflow_id=workflow_id, run_id=str(uuid.uuid4())
        )

    async def mock_do_nothing(*args, **kwargs):
        pass

    async def mock_describe_workflow(*args, **kwargs):
        return None

    monkeypatch.setattr(TemporalClientAdapter, "start_workflow", mock_start_workflow)
    monkeypatch.setattr(TemporalClientAdapter, "update_workflow", mock_do_nothing)
    monkeypatch.setattr(TemporalClientAdapter, "signal_workflow", mock_do_nothing)
    monkeypatch.setattr(TemporalClientAdapter, "cancel_workflow", mock_do_nothing)
    monkeypatch.setattr(TemporalClientAdapter, "describe_workflow", mock_describe_workflow)
