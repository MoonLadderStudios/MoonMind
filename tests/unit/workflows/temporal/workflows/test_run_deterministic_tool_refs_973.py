"""MoonLadderStudios/MoonMind#973: deterministic tool ref resolution.

A normal admitted plan must execute a supported deterministic tool and its
dependencies through production dispatch without an agent wrapper. The plan
validator admits ``{"ref": {"node", "json_pointer"}}`` output references; the
Temporal run path must resolve them deterministically from recorded results
instead of forwarding unresolved refs to the tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    MoonMindRunWorkflow,
    _resolve_plan_json_pointer,
    _resolve_plan_ref_inputs,
)


def _tool_definition_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} deterministic tool",
        "inputs": {"schema": {"type": "object"}},
        "outputs": {"schema": {"type": "object"}},
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["integration:github"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 60,
                "schedule_to_close_seconds": 120,
            },
            "retries": {"max_attempts": 1},
        },
    }


def _mock_plan_payload(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None
) -> bytes:
    import json

    return json.dumps(
        {
            "plan_version": "1.0",
            "metadata": {
                "title": "Test",
                "created_at": "2024-01-01T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:123",
                    "artifact_ref": "art:sha256:456",
                },
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "nodes": nodes,
            "edges": edges or [],
        }
    ).encode("utf-8")


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    dump_method = getattr(payload, "model_dump", getattr(payload, "dict", None))
    return dump_method() if dump_method else payload


async def _immediate_wait_condition(
    predicate: Callable[[], bool],
    **_kwargs: Any,
) -> None:
    assert predicate() is True


def test_resolve_plan_ref_inputs_passes_through_without_refs() -> None:
    inputs = {"ticket": "MM-1", "nested": {"count": 2}, "tags": ["a", "b"]}
    resolved = _resolve_plan_ref_inputs(
        inputs, results_by_node={}, current_node_id="consume"
    )
    assert resolved == inputs


def test_resolve_plan_ref_inputs_resolves_nested_refs() -> None:
    results = {
        "produce": {
            "status": "COMPLETED",
            "outputs": {"ticket": "MM-1", "detail": {"count": 3}},
            "progress": {},
        }
    }
    inputs = {
        "ticket": {"ref": {"node": "produce", "json_pointer": "/outputs/ticket"}},
        "nested": {"count": {"ref": {"node": "produce", "json_pointer": "/outputs/detail/count"}}},
    }
    resolved = _resolve_plan_ref_inputs(
        inputs, results_by_node=results, current_node_id="consume"
    )
    assert resolved == {"ticket": "MM-1", "nested": {"count": 3}}


def test_resolve_plan_ref_inputs_rejects_unknown_node() -> None:
    with pytest.raises(ValueError, match="incomplete node 'missing'"):
        _resolve_plan_ref_inputs(
            {"ticket": {"ref": {"node": "missing", "json_pointer": "/outputs/ticket"}}},
            results_by_node={},
            current_node_id="consume",
        )


def test_resolve_plan_ref_inputs_rejects_invalid_pointer() -> None:
    results = {
        "produce": {"status": "COMPLETED", "outputs": {"ticket": "MM-1"}, "progress": {}}
    }
    with pytest.raises(ValueError, match="invalid output path"):
        _resolve_plan_ref_inputs(
            {"ticket": {"ref": {"node": "produce", "json_pointer": "/outputs/absent"}}},
            results_by_node=results,
            current_node_id="consume",
        )


def test_resolve_plan_json_pointer_rejects_scalar_traversal() -> None:
    with pytest.raises(ValueError, match="cannot be applied to scalar"):
        _resolve_plan_json_pointer({"outputs": {"ticket": "MM-1"}}, "/outputs/ticket/extra")


@pytest.mark.asyncio
async def test_run_execution_stage_resolves_tool_dependency_ref_without_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed tool dependency: consume resolves produce's outputs via dispatch."""

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    captured: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
        captured.append((activity_type, normalized, kwargs))
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            _tool_definition_payload("test.produce"),
                            _tool_definition_payload("test.consume"),
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "produce",
                        "tool": {"type": "skill", "name": "test.produce"},
                        "inputs": {"prompt": "hello"},
                    },
                    {
                        "id": "consume",
                        "tool": {"type": "skill", "name": "test.consume"},
                        "inputs": {
                            "ticket": {
                                "ref": {
                                    "node": "produce",
                                    "json_pointer": "/outputs/ticket",
                                }
                            }
                        },
                    },
                ],
                edges=[{"from": "produce", "to": "consume"}],
            )
        if activity_type == "mm.tool.execute":
            invocation = normalized.get("invocation_payload", {})
            node_id = invocation.get("id")
            if node_id == "produce":
                return {"status": "COMPLETED", "outputs": {"ticket": "MM-1"}}
            if node_id == "consume":
                return {"status": "COMPLETED", "outputs": {"ok": True}}
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )

    async def fail_if_child_workflow_starts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("deterministic tool steps must not start AgentRun")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fail_if_child_workflow_starts,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _attrs: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
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
    assert len(tool_calls) == 2
    consume_call = next(
        call for call in tool_calls if call[1]["invocation_payload"]["id"] == "consume"
    )
    assert consume_call[1]["invocation_payload"]["inputs"]["ticket"] == "MM-1"


@pytest.mark.asyncio
async def test_run_execution_stage_rejects_unresolvable_tool_ref_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid dependency ref fails the step explicitly, not empty success."""

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> Any:
        normalized = _normalize_payload(payload)
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {"skills": [_tool_definition_payload("test.consume")]}
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "consume",
                        "tool": {"type": "skill", "name": "test.consume"},
                        "inputs": {
                            "ticket": {
                                "ref": {
                                    "node": "missing",
                                    "json_pointer": "/outputs/ticket",
                                }
                            }
                        },
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _attrs: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
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

    with pytest.raises(ValueError, match="incomplete node 'missing'"):
        await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")


def test_resolve_plan_ref_inputs_resolves_mixed_tool_to_agent_consumer() -> None:
    """Mixed tool/agent plan: a skill producer output feeds an agent consumer.

    Resolution in run.py happens before the tool_type dispatch branch, so the
    same recorded COMPLETED map serves both deterministic tool and
    agent_runtime nodes without an AgentRun for the tool step.
    """

    results = {
        "produce": {
            "status": "COMPLETED",
            "outputs": {"ticket": "MM-1"},
            "progress": {},
        }
    }
    agent_inputs = {
        "ticket": {"ref": {"node": "produce", "json_pointer": "/outputs/ticket"}},
        "prompt": "summarize",
    }
    resolved = _resolve_plan_ref_inputs(
        agent_inputs, results_by_node=results, current_node_id="agent-consume"
    )
    assert resolved == {"ticket": "MM-1", "prompt": "summarize"}


def test_activity_result_retryable_skill_business_vs_system_error() -> None:
    """Tool path: returned business FAILED is not retryable; system_error is."""

    workflow = MoonMindRunWorkflow()
    business = {"status": "FAILED", "outputs": {"error": "validation_failed"}}
    assert (
        workflow._activity_result_retryable(
            business, failure_message="validation_failed", tool_type="skill"
        )
        is False
    )
    system = {"status": "FAILED", "outputs": {"error": "system_error"}}
    assert (
        workflow._activity_result_retryable(
            system, failure_message="system_error", tool_type="skill"
        )
        is True
    )


@pytest.mark.asyncio
async def test_run_execution_stage_skill_business_failure_maps_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned business FAILED maps once at its owner: one call, terminal."""

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    tool_calls: list[str] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> Any:
        normalized = _normalize_payload(payload)
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {"skills": [_tool_definition_payload("test.consume")]}
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "consume",
                        "tool": {"type": "skill", "name": "test.consume"},
                        "inputs": {"ticket": "MM-1"},
                    }
                ]
            )
        if activity_type == "mm.tool.execute":
            tool_calls.append(activity_type)
            return {
                "status": "FAILED",
                "outputs": {"error": "validation_failed", "summary": "bad ticket"},
            }
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _attrs: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
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

    with pytest.raises(ValueError, match="bad ticket|validation_failed"):
        await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")
    assert tool_calls == ["mm.tool.execute"]


@pytest.mark.asyncio
async def test_run_execution_stage_skill_route_ignores_caller_chosen_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Least privilege: caller inputs cannot select activity/queue/mounts."""

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    captured: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_execute_activity(
        activity_type: str, payload: Any, **kwargs: Any
    ) -> Any:
        normalized = _normalize_payload(payload)
        captured.append((activity_type, normalized, kwargs))
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {"skills": [_tool_definition_payload("test.consume")]}
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "consume",
                        "tool": {"type": "skill", "name": "test.consume"},
                        "inputs": {
                            "ticket": "MM-1",
                            "activity_type": "host.shell",
                            "taskQueue": "caller-chosen-queue",
                            "mounts": ["/etc/secrets"],
                            "queues": ["caller-chosen-queue"],
                        },
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {"ok": True}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _attrs: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
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
    _, payload, kwargs = tool_calls[0]
    assert kwargs.get("task_queue") != "caller-chosen-queue"
    assert payload["invocation_payload"]["tool"] == {
        "type": "skill",
        "name": "test.consume",
    }


def test_preserved_step_predicate_and_sparse_seed_shape() -> None:
    """Restart: preserved COMPLETED tool work is skipped, never re-dispatched.

    The predicate is the skip mechanism; the sparse seed (artifact refs only)
    is the recorded identity reused for ref resolution without rebuilding.
    """

    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [
        {
            "logicalStepId": "produce",
            "preservedFrom": {"workflowId": "wf-0", "runId": "run-0"},
            "artifacts": {"outputSummary": "art:summary", "outputPrimary": "art:primary"},
        }
    ]
    workflow._rebuild_step_ledger_index()
    assert workflow._is_preserved_step("produce") is True
    assert workflow._is_preserved_step("consume") is False
    seeded = workflow._preserved_step_outputs("produce")
    assert seeded["outputSummaryRef"] == "art:summary"
    assert seeded["outputPrimaryRef"] == "art:primary"
    completed: dict[str, Any] = {
        "produce": {"status": "COMPLETED", "outputs": dict(seeded), "progress": {}}
    }
    resolved = _resolve_plan_ref_inputs(
        {"summary": {"ref": {"node": "produce", "json_pointer": "/outputs/outputSummaryRef"}}},
        results_by_node=completed,
        current_node_id="consume",
    )
    assert resolved == {"summary": "art:summary"}


def test_step_execution_idempotency_key_is_stable_for_lost_ack() -> None:
    """Lost acknowledgment: the same step/execution/operation reuses one key."""

    from moonmind.workflows.temporal.step_executions import (
        step_execution_operation_idempotency_key,
    )

    first = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="consume",
        execution_ordinal=1,
        operation="execute",
    )
    second = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="consume",
        execution_ordinal=1,
        operation="execute",
    )
    assert first == second
    assert "consume" in first and first.endswith(":execute")


@pytest.mark.asyncio
async def test_execute_container_job_tool_cancellation_is_request_not_stop_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation requests container_job.cancel; CANCELLED is not stop proof."""

    from datetime import datetime, timezone

    from moonmind.workflows.temporal.workflows import run as run_module
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    calls: list[str] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> Any:
        calls.append(activity_type)
        if activity_type == "container_job.submit":
            return {"jobId": "container-job:0123456789abcdef0123456789abcdef"}
        if activity_type == "container_job.cancel":
            return {"jobId": "container-job:0123456789abcdef0123456789abcdef"}
        raise AssertionError(f"status must not be polled after cancel: {activity_type}")

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._cancel_requested = True
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {
                "namespace": "default",
                "workflow_id": "wf-973",
                "run_id": "run-1",
                "search_attributes": {},
            },
        ),
    )
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await workflow._execute_container_job_tool(
        node_inputs={
            "idempotencyKey": "wf-973:cancel-step:1",
            "spec": {
                "image": "docker.io/library/qualification-fixture:1.0.0",
                "command": ["sh", "-lc", "probe"],
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            },
        },
        node_id="cancel-step",
        execution_ordinal=1,
    )

    assert calls[0] == "container_job.submit"
    assert "container_job.cancel" in calls
    assert "container_job.status" not in calls
    assert result["status"] == "CANCELLED"
    assert result["outputs"]["state"] == "canceling"
