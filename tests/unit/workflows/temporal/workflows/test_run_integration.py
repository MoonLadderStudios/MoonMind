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
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)
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
async def test_run_execution_stage_bundles_consecutive_jules_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    workflow._title = "Bundled Jules execution"

    child_calls: list[object] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            if payload.get("artifact_ref") == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                                "description": "Run tests",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.skill.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 3},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                nodes=[
                    {
                        "id": "jules-step-1",
                        "tool": {"type": "agent_runtime", "name": "jules", "version": "1.0"},
                        "inputs": {
                            "repository": "org/repo",
                            "startingBranch": "main",
                            "publishMode": "none",
                            "instructions": "Step 1",
                        },
                    },
                    {
                        "id": "jules-step-2",
                        "tool": {"type": "agent_runtime", "name": "jules", "version": "1.0"},
                        "inputs": {
                            "repository": "org/repo",
                            "startingBranch": "main",
                            "publishMode": "none",
                            "instructions": "Step 2",
                        },
                    },
                ]
            )
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://bundle/1"}, {"upload_url": "unused"})
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: Any,
    ) -> object:
        child_calls.append(args)
        return {"summary": "Bundled run complete", "metadata": {}, "output_refs": []}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_search_attributes", lambda _attributes: None)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "org/repo", "publishMode": "none"},
        plan_ref="plan-1",
    )

    assert len(child_calls) == 1
    request = child_calls[0]
    assert "Ordered Checklist:" in request.instruction_ref
    assert "1. Step 1" in request.instruction_ref
    assert "2. Step 2" in request.instruction_ref
    assert request.parameters["metadata"]["moonmind"]["bundleManifestRef"] == "artifact://bundle/1"
    assert request.parameters["metadata"]["moonmind"]["bundleStrategy"] == "one_shot_jules"


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
        if activity_type == "repo.merge_pr":
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
    assert captured[3][0] == "repo.merge_pr"
    
    # Verify merge_pr payload
    merge_payload = captured[3][1]
    # No target_branch should be passed since we didn't override it
    assert merge_payload == {"pr_url": "https://github.com/org/repo/pull/123"}


@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_requires_pr_url(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"summary": "Done"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        mock_run_workflow.external_event(
            {"correlation_id": "ext-1", "normalized_status": "completed"}
        )

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    with pytest.raises(ValueError, match="no PR URL found"):
        await mock_run_workflow._run_integration_stage(
            parameters={
                "repo": "org/repo",
                "publishMode": "branch",
                "workspaceSpec": {"startingBranch": "feature-branch"},
            },
            plan_ref="plan-1",
        )


@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_requires_merge_success(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"url": "https://github.com/org/repo/pull/123", "summary": "Done"}
        if activity_type == "repo.merge_pr":
            return {"merged": False, "summary": "Merge rejected"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        mock_run_workflow.external_event(
            {"correlation_id": "ext-1", "normalized_status": "completed"}
        )

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    with pytest.raises(ValueError, match="merge failed"):
        await mock_run_workflow._run_integration_stage(
            parameters={
                "repo": "org/repo",
                "publishMode": "branch",
                "workspaceSpec": {"startingBranch": "feature-branch"},
            },
            plan_ref="plan-1",
        )


@pytest.mark.asyncio
async def test_run_integration_stage_multi_step_bundles_into_single_start(
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
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://bundle/1"}, {"upload_url": "unused"})
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-session-123", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed"}
        return {}

    def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    def fake_patched(patch_id: str) -> bool:
        return True

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    start_calls = [c for c in captured if c[0] == "integration.jules.start"]
    artifact_create_calls = [c for c in captured if c[0] == "artifact.create"]
    artifact_write_calls = [c for c in captured if c[0] == "artifact.write_complete"]
    status_calls = [c for c in captured if c[0] == "integration.jules.status"]
    
    assert len(start_calls) == 1
    description = start_calls[0][1]["parameters"]["description"]
    assert "Ordered Checklist:" in description
    assert "1. Step 1" in description
    assert "2. Step 2" in description
    assert "3. Step 3" in description
    assert len(artifact_create_calls) == 1
    assert len(artifact_write_calls) == 1
    assert all(c[0] != "integration.jules.send_message" for c in captured)
    assert len(status_calls) == 1
    assert mock_run_workflow._external_status == "completed"
