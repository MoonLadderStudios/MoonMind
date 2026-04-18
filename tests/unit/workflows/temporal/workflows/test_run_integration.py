import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    INTEGRATION_POLL_LOOP_PATCH,
    NATIVE_PR_BRANCH_DEFAULTS_PATCH,
    NATIVE_PR_PUSH_STATUS_GATE_PATCH,
    MoonMindRunWorkflow,
)
from moonmind.workloads.tool_bridge import build_dood_tool_definition_payload

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

def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    dump_method = getattr(payload, 'model_dump', getattr(payload, 'dict', None))
    return dump_method() if dump_method else payload


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
async def test_run_execution_stage_skips_integration_after_merge_gate_cancellation(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                return (
                    b'{"skills":[{"name":"repo.noop","version":"1.0",'
                    b'"description":"No-op","inputs":{"schema":{"type":"object"}},'
                    b'"outputs":{"schema":{"type":"object"}},'
                    b'"executor":{"activity_type":"mm.skill.execute",'
                    b'"selector":{"mode":"by_capability"}},'
                    b'"requirements":{"capabilities":["sandbox"]},'
                    b'"policies":{"timeouts":{"start_to_close_seconds":60,'
                    b'"schedule_to_close_seconds":120},"retries":{"max_attempts":1}}}]}'
                )
            return _mock_plan_payload(
                [
                    {
                        "id": "step-1",
                        "tool": {
                            "type": "skill",
                            "name": "repo.noop",
                            "version": "1.0",
                        },
                        "inputs": {},
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_merge_gate(
        *,
        parameters: dict[str, Any],
        pull_request_url: str | None,
    ) -> None:
        mock_run_workflow._cancel_requested = True
        mock_run_workflow._set_state("canceled", summary="merge automation canceled")

    async def fake_integration_stage(
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        integration_calls.append({"parameters": parameters, "plan_ref": plan_ref})

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(mock_run_workflow, "_maybe_start_merge_gate", fake_merge_gate)
    monkeypatch.setattr(
        mock_run_workflow,
        "_run_integration_stage",
        fake_integration_stage,
    )

    await mock_run_workflow._run_execution_stage(
        parameters={"publishMode": "none"},
        plan_ref="plan-1",
    )

    assert integration_calls == []


@pytest.mark.asyncio
async def test_run_integration_stage_poll_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
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
    """Compatibility: polling completion must use the replay-stable patch id."""
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
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "art:sha256:456":
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
async def test_run_execution_stage_routes_dood_skill_tool_to_agent_runtime_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    workflow._title = "DooD workload"
    captured: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload), kwargs))
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            build_dood_tool_definition_payload(
                                name="container.run_workload",
                                version="1.0",
                            )
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "workload-step",
                        "tool": {
                            "type": "skill",
                            "name": "container.run_workload",
                            "version": "1.0",
                        },
                        "inputs": {
                            "profileId": "local-python",
                            "repoDir": "/work/agent_jobs/wf-1/repo",
                            "artifactsDir": (
                                "/work/agent_jobs/wf-1/artifacts/workload-step"
                            ),
                            "command": ["python", "-V"],
                        },
                    }
                ]
            )
        if activity_type == "mm.tool.execute":
            return {
                "status": "COMPLETED",
                "outputs": {
                    "workloadResult": {
                        "status": "succeeded",
                        "profileId": "local-python",
                    }
                },
            }
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    async def fail_if_child_workflow_starts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("DooD skill tools must not start MoonMind.AgentRun")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fail_if_child_workflow_starts,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-1",
            "run_id": "run-1",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "logger",
        type(
            "Logger",
            (),
            {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
        ),
    )

    await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")

    tool_calls = [call for call in captured if call[0] == "mm.tool.execute"]
    assert len(tool_calls) == 1
    payload = tool_calls[0][1]
    assert payload["invocation_payload"]["tool"] == {
        "type": "skill",
        "name": "container.run_workload",
        "version": "1.0",
    }
    assert payload["context"]["workflow_id"] == "wf-1"
    assert payload["context"]["node_id"] == "workload-step"
    assert tool_calls[0][2]["task_queue"] == "mm.activity.agent_runtime"


@pytest.mark.asyncio
async def test_run_execution_stage_skips_integration_after_merge_automation_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._integration = "jules"
    workflow._repo = "org/repo"
    integration_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> Any:
        assert activity_type == "artifact.read"
        return _mock_plan_payload(
            [
                {
                    "id": "noop",
                    "tool": {
                        "type": "agent_runtime",
                        "name": "codex",
                        "version": "1.0",
                    },
                    "inputs": {"instructions": "No-op"},
                }
            ]
        )

    async def fake_maybe_start_merge_gate(
        *,
        parameters: dict[str, Any],
        pull_request_url: str | None,
    ) -> None:
        assert parameters["publishMode"] == "none"
        assert pull_request_url is None
        workflow._cancel_requested = True

    async def fake_run_integration_stage(
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        nonlocal integration_calls
        integration_calls += 1

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        workflow,
        "_ordered_plan_node_payloads",
        lambda *, nodes, edges: [],
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == "run-conditional-registry-read-v1",
    )
    monkeypatch.setattr(workflow, "_maybe_start_merge_gate", fake_maybe_start_merge_gate)
    monkeypatch.setattr(workflow, "_run_integration_stage", fake_run_integration_stage)

    await workflow._run_execution_stage(
        parameters={"repo": "org/repo", "publishMode": "none"},
        plan_ref="plan-1",
    )

    assert integration_calls == 0


@pytest.mark.asyncio
async def test_run_integration_stage_signal_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
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
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
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
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload)))
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


def test_determine_publish_completion_fails_for_no_commit_pr_publish(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = {
        "outputs": {
            "push_status": "no_commits",
            "push_branch": "feature/no-op",
            "push_base_ref": "origin/main",
            "push_commit_count": 0,
            "operator_summary": "Files edited in this run: none.",
        }
    }
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result=execution_result,
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert "no publishable diff was produced" in message
    assert "feature/no-op" in message
    assert "origin/main" in message
    assert "has no commits ahead of origin/main" in message
    assert "0 commits ahead" not in message
    assert "Files edited in this run: none." in message
    assert publish_failure is True


def test_native_pr_branch_resolution_keeps_legacy_branch_only_replay_shape(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={"push_base_branch": "trunk"},
        workspace_spec={"branch": "feature/existing"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "release"},
    )

    assert head_branch == "feature/existing"
    assert base_branch == "feature/existing"


def test_native_pr_branch_resolution_uses_patched_publish_defaults(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_BRANCH_DEFAULTS_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={"push_base_branch": "trunk"},
        workspace_spec={"branch": "feature/existing"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "release"},
    )

    assert head_branch == ""
    assert base_branch == "release"


def test_native_pr_push_status_gate_preserves_legacy_protected_branch_fallback(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "protected_branch",
                "push_branch": "feature/existing",
            }
        },
    )

    assert mock_run_workflow._native_pr_push_status_blocks_creation(
        "protected_branch"
    ) is False
    assert mock_run_workflow._publish_status is None


def test_native_pr_push_status_gate_blocks_protected_branch_when_patched(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_PUSH_STATUS_GATE_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "protected_branch",
                "push_branch": "feature/existing",
            }
        },
    )

    assert mock_run_workflow._native_pr_push_status_blocks_creation(
        "protected_branch"
    ) is True
    assert mock_run_workflow._publish_status == "failed"
    assert "feature/existing" in (mock_run_workflow._publish_reason or "")


def test_record_execution_context_resets_last_step_fields_when_current_node_has_no_summary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "summary": "Completed the substantive step.",
                "diagnostics_ref": "diag-1",
            }
        },
    )

    mock_run_workflow._record_execution_context(
        node_id="step-2",
        execution_result={"outputs": {}},
    )

    assert mock_run_workflow._last_step_id == "step-2"
    assert mock_run_workflow._last_step_summary is None
    assert mock_run_workflow._last_diagnostics_ref is None


def test_record_execution_context_scrubs_operator_summary_and_ignores_negative_commit_count(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "operator_summary": "Final report token ghp_abcdefghijklmnopqrstuvwxyz123456",
                "push_branch": "feature/no-op",
                "push_base_ref": "origin/main",
                "push_commit_count": -1,
            }
        },
    )

    assert mock_run_workflow._operator_summary is not None
    assert "[REDACTED]" in mock_run_workflow._operator_summary
    assert "ghp_" not in mock_run_workflow._operator_summary
    assert "commitCount" not in mock_run_workflow._publish_context


def test_determine_publish_completion_fails_when_pr_publish_creates_no_pr(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._integration = None

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert message == "publishMode 'pr' requested but no PR was created"
    assert publish_failure is True


def test_determine_publish_completion_allows_integration_pr_without_local_pr_url(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._integration = "jules"

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "success"
    assert message == "Workflow completed successfully"
    assert publish_failure is False


def test_determine_publish_completion_fails_for_unknown_branch_publish_outcome(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "branch"}
    )

    assert status == "failed"
    assert message == "branch publish outcome unknown"
    assert publish_failure is True
