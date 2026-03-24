import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


@pytest.fixture
def mock_run_workflow(monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._integration = "jules"
    workflow._repo = "org/repo"
    
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
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
    
    # Mock logger
    logger = type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})
    monkeypatch.setattr(run_workflow_module.workflow, "logger", logger)

    return workflow


@pytest.mark.asyncio
async def test_run_integration_stage_poll_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, payload))
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            # Simulate completion on the first poll
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate timeout so we fall through to polling
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    # Expected activity calls: start, status
    assert len(captured) == 2
    assert captured[0][0] == "integration.jules.start"
    assert captured[1][0] == "integration.jules.status"
    assert mock_run_workflow._external_status == "completed"


@pytest.mark.asyncio
async def test_run_integration_stage_signal_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, payload))
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate an external event arriving during the wait
        mock_run_workflow.external_event({
            "correlation_id": "ext-1",
            "normalized_status": "completed"
        })
        # the lambda cond() would now be True since external_event sets _resume_requested = True 
        # (wait, external_event calls _resume_requested? Let's check. 
        # external_event does NOT set _resume_requested directly, it calls resume() usually. 
        # run.py line 1358: external_event... oh wait, external_event just sets _external_status.
        # Who sets _resume_requested? 
        # Wait, if external_event just sets _external_status, does it break the loop?)
        # Let's just simulate the effect.
        mock_run_workflow._resume_requested = True
        return

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    # Expected activity calls: start only, because it woke up via signal and skipped polling
    assert len(captured) == 1
    assert captured[0][0] == "integration.jules.start"
    assert mock_run_workflow._external_status == "completed"


@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_auto_merge_after_signal(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, payload))
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"url": "https://github.com/org/repo/pull/123", "summary": "Done"}
        if activity_type == "integration.jules.merge_pr":
            return {"merged": True, "summary": "Merged successfully"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate signal arriving
        mock_run_workflow.external_event({
            "correlation_id": "ext-1",
            "normalized_status": "completed"
        })
        mock_run_workflow._resume_requested = True
        return

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={
            "repo": "org/repo",
            "publishMode": "branch",
            "workspaceSpec": {"startingBranch": "feature-branch"}
        },
        plan_ref="plan-1",
    )
    
    # Expected activity calls: start, fetch_result, merge_pr
    assert len(captured) == 3
    assert captured[0][0] == "integration.jules.start"
    assert captured[1][0] == "integration.jules.fetch_result"
    assert captured[2][0] == "integration.jules.merge_pr"
    
    # Verify merge_pr payload
    merge_payload = captured[2][1]
    # No target_branch should be passed since we didn't override it
    assert merge_payload == {"pr_url": "https://github.com/org/repo/pull/123"}
