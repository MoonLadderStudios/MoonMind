import json
from datetime import datetime, timezone
from typing import Any

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


@pytest.fixture
def mock_run_workflow(monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    
    logger = type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})
    monkeypatch.setattr(run_workflow_module.workflow, "logger", logger)

    monkeypatch.setattr(run_workflow_module.settings.workflow, "enable_task_proposals", True)

    return workflow


@pytest.mark.asyncio
async def test_run_proposals_stage_propagates_policy(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        def _to_serializable(obj: Any) -> Any:
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if hasattr(obj, "dict"):
                return obj.dict()
            return str(obj)

        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    parameters = {
        "repo": "org/repo",
        "proposeTasks": True,
        "task": {
            "proposalPolicy": {
                "max_items": {"project": 5},
                "targets": ["project"],
                "default_runtime": "claude",
            }
        }
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 2
    assert captured[0][0] == "proposal.generate"
    assert captured[1][0] == "proposal.submit"
    
    submit_payload = captured[1][1]
    policy_payload = submit_payload["policy"]
    assert policy_payload["max_items"] == 5
    assert policy_payload["targets"] == ["project"]
    assert policy_payload["default_runtime"] == "claude"
    
    assert mock_run_workflow._proposals_generated == 1
    assert mock_run_workflow._proposals_submitted == 1


@pytest.mark.asyncio
async def test_run_proposals_stage_skipped_when_proposeTasks_false(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(activity_type: str, *args: Any, **kwargs: Any) -> Any:
        captured.append((activity_type, args))

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    parameters = {
        "repo": "org/repo",
        "proposeTasks": False,
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 0


@pytest.mark.asyncio
async def test_run_proposals_stage_skipped_when_globally_disabled(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    monkeypatch.setattr(run_workflow_module.settings.workflow, "enable_task_proposals", False)

    async def fake_execute_activity(activity_type: str, *args: Any, **kwargs: Any) -> Any:
        captured.append((activity_type, args))

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    parameters = {
        "repo": "org/repo",
        "proposeTasks": True,
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 0
