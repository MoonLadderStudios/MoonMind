import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    INTEGRATION_POLL_LOOP_PATCH,
    MoonMindRunWorkflow,
)

def _mock_plan_payload(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None) -> bytes:
    import json
    return json.dumps({
        "plan_version": "1.0",
        "metadata": {
            "title": "Test", 
            "created_at": "2024-01-01T00:00:00Z", 
            "registry_snapshot": {"digest": "reg:sha256:123", "artifact_ref": "art:sha256:456"}
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": nodes,
        "edges": edges or []
    }).encode("utf-8")


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
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Do something"}}])
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
    
    # Expected activity calls: artifact.read, start, status
    assert len(captured) == 3
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
    assert captured[2][0] == "integration.jules.status"
    assert mock_run_workflow._external_status == "completed"


@pytest.mark.asyncio
async def test_run_integration_poll_completion_invokes_patched_with_stable_id(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compatibility: polling completion must use the replay-stable patch id (see WorkerDeployment)."""
    patch_calls: list[str] = []

    def fake_patched(patch_id: str) -> bool:
        patch_calls.append(patch_id)
        return True

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )

    assert patch_calls, "workflow.patched should be evaluated for integration poll completion"
    assert set(patch_calls) == {INTEGRATION_POLL_LOOP_PATCH}
    assert mock_run_workflow._external_status == "completed"


@pytest.mark.asyncio
async def test_run_integration_legacy_unpatched_poll_completion_still_completes(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-flight history without the patch marker: legacy resume path still reaches completed."""
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )

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
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Do something"}}])
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate an external event arriving during the wait
        mock_run_workflow.external_event({
            "correlation_id": "ext-1",
            "normalized_status": "completed"
        })
        return

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    # Expected activity calls: artifact.read, start only, because it woke up via signal and skipped polling
    assert len(captured) == 2
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
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
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Do something"}}])
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
    
    # Expected activity calls: fetch plan, start, fetch_result, merge_pr
    assert len(captured) == 4
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
    assert captured[2][0] == "integration.jules.fetch_result"
    assert captured[3][0] == "integration.jules.merge_pr"
    
    # Verify merge_pr payload
    merge_payload = captured[3][1]
    # No target_branch should be passed since we didn't override it
    assert merge_payload == {"pr_url": "https://github.com/org/repo/pull/123"}


@pytest.mark.asyncio
async def test_run_integration_stage_multi_step_sends_messages(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, payload))
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                nodes=[
                    {"id": "step1", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Step 1"}},
                    {"id": "step2", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Step 2"}},
                    {"id": "step3", "tool": {"type": "skill", "name": "t", "version": "1.0"}, "inputs": {"instructions": "Step 3"}},
                ],
                edges=[
                    {"from": "step1", "to": "step2"},
                    {"from": "step2", "to": "step3"}
                ]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-session-123", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.send_message":
            return {"status": "running"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    start_calls = [c for c in captured if c[0] == "integration.jules.start"]
    send_message_calls = [c for c in captured if c[0] == "integration.jules.send_message"]
    status_calls = [c for c in captured if c[0] == "integration.jules.status"]
    
    assert len(start_calls) == 1
    assert start_calls[0][1]["parameters"]["description"] == "Step 1"
    
    assert len(send_message_calls) == 2
    assert send_message_calls[0][1]["prompt"] == "Step 2"
    assert send_message_calls[1][1]["prompt"] == "Step 3"
    
    # 3 steps, so 3 status polls (1 for start, 2 for send_message)
    assert len(status_calls) == 3
    assert mock_run_workflow._external_status == "completed"
