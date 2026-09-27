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
    RUN_CONTAINER_JOB_DERIVED_IDEMPOTENCY_KEY_PATCH,
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


def test_generic_skill_resolves_through_real_catalog_without_agent_run() -> None:
    """REQ-1/REQ-2 real boundary: registry definition -> catalog route.

    Uses the real ``parse_tool_definition`` + ``DEFAULT_ACTIVITY_CATALOG``
    (no mocked ``execute_activity``) to prove a normal admitted skill node
    reaches ``mm.tool.execute`` on its capability fleet with definition-owned
    timeouts — never a caller-chosen activity type, queue, or mount.
    """

    from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
    from moonmind.workflows.temporal.workflows.run import DEFAULT_ACTIVITY_CATALOG

    definition = parse_tool_definition(_tool_definition_payload("test.consume"))
    route = DEFAULT_ACTIVITY_CATALOG.resolve_skill(definition)
    assert route.activity_type == "mm.tool.execute"
    assert route.task_queue
    assert "caller-chosen-queue" not in route.task_queue
    assert (
        route.timeouts.start_to_close_seconds
        == definition.policies.timeouts.start_to_close_seconds
    )
    assert (
        route.timeouts.schedule_to_close_seconds
        == definition.policies.timeouts.schedule_to_close_seconds
    )
    assert route.retries.max_attempts == definition.policies.retries.max_attempts


@pytest.mark.asyncio
async def test_generic_skill_executes_through_real_dispatcher_without_agent_run() -> None:
    """REQ-2 real boundary: ``execute_tool_activity`` with real dispatcher.

    A registered deterministic handler returns COMPLETED without any AgentRun
    or model lease; an unregistered skill name fails closed as non-retryable
    ``INVALID_INPUT`` (explicit denied/unknown enforcement at the dispatcher).
    """

    from moonmind.workflows.skills.tool_dispatcher import (
        ToolActivityDispatcher,
        execute_tool_activity,
    )
    from moonmind.workflows.skills.tool_plan_contracts import (
        ToolFailure,
        ToolResult,
        parse_tool_definition,
    )
    from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot

    definition = parse_tool_definition(_tool_definition_payload("test.consume"))
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:" + "a" * 64,
        artifact_ref="art:sha256:456",
        skills=(definition,),
    )
    dispatcher = ToolActivityDispatcher()

    async def deterministic_handler(inputs: Any, context: Any) -> ToolResult:
        assert inputs.get("ticket") == "MM-1"
        assert context is not None
        return ToolResult(status="COMPLETED", outputs={"ok": True})

    dispatcher.register_skill(skill_name="test.consume", handler=deterministic_handler)
    result = await execute_tool_activity(
        invocation_payload={
            "id": "consume",
            "tool": {"type": "skill", "name": "test.consume"},
            "inputs": {"ticket": "MM-1"},
            "options": {},
        },
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context={"principal": "owner-1", "idempotency_key": "wf-1:consume:1:execute"},
    )
    assert result.status == "COMPLETED"
    assert result.outputs == {"ok": True}

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload={
                "id": "consume",
                "tool": {"type": "skill", "name": "test.unknown"},
                "inputs": {"ticket": "MM-1"},
                "options": {},
            },
            registry_snapshot=snapshot,
            dispatcher=dispatcher,
            context={"principal": "owner-1"},
        )
    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_lost_ack_idempotency_key_reaches_real_dispatcher_context() -> None:
    """REQ-2 lost-ack plumbing: stable step key arrives in activity context.

    The same step/execution/operation identity produces one key and the real
    dispatcher path carries it into the handler context, so a retried
    acknowledgment reconciles against the same operation instead of mutating
    twice under a fresh key.
    """

    from moonmind.workflows.skills.tool_dispatcher import (
        ToolActivityDispatcher,
        execute_tool_activity,
    )
    from moonmind.workflows.skills.tool_plan_contracts import (
        ToolResult,
        parse_tool_definition,
    )
    from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
    from moonmind.workflows.temporal.step_executions import (
        step_execution_operation_idempotency_key,
    )

    definition = parse_tool_definition(_tool_definition_payload("test.consume"))
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:" + "b" * 64,
        artifact_ref="art:sha256:456",
        skills=(definition,),
    )
    dispatcher = ToolActivityDispatcher()
    seen_contexts: list[Any] = []

    async def capturing_handler(inputs: Any, context: Any) -> ToolResult:
        seen_contexts.append(dict(context or {}))
        return ToolResult(status="COMPLETED", outputs={"ok": True})

    dispatcher.register_skill(skill_name="test.consume", handler=capturing_handler)
    key = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="consume",
        execution_ordinal=1,
        operation="execute",
    )
    for _ in range(2):
        result = await execute_tool_activity(
            invocation_payload={
                "id": "consume",
                "tool": {"type": "skill", "name": "test.consume"},
                "inputs": {"ticket": "MM-1"},
                "options": {},
            },
            registry_snapshot=snapshot,
            dispatcher=dispatcher,
            context={"principal": "owner-1", "idempotency_key": key},
        )
        assert result.status == "COMPLETED"
    assert [ctx.get("idempotency_key") for ctx in seen_contexts] == [key, key]


@pytest.mark.asyncio
async def test_container_job_derives_stable_key_and_reconciles_lost_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 lost-ack reconciliation: omitted plan key retries under one identity.

    A normal admitted ``container.run_job`` plan carries only ``spec``. The
    workflow derives the stable step-execution key, so a retried acknowledgment
    resubmits the same ``idempotencyKey`` and the backend replays the same
    ``jobId`` (reconciliation) instead of creating a second job (remutation).
    """

    from moonmind.workflows.temporal.step_executions import (
        step_execution_operation_idempotency_key,
    )
    from moonmind.workflows.temporal.workflows import run as run_module
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    submitted_keys: list[str] = []
    jobs_by_key: dict[str, str] = {}
    job_id = "container-job:" + "ab" * 16

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> Any:
        normalized = _normalize_payload(payload)
        if activity_type == "container_job.submit":
            request = normalized.get("request", {})
            key = str(request.get("idempotencyKey") or "")
            assert key, "submit must carry a stable idempotencyKey"
            submitted_keys.append(key)
            # Backend exact-replay semantics: same key returns the same job.
            jobs_by_key.setdefault(key, job_id)
            return {"jobId": jobs_by_key[key], "state": "queued"}
        if activity_type == "container_job.status":
            return {"jobId": job_id, "state": "succeeded", "terminal": {"exitCode": 0}}
        raise AssertionError(f"unexpected activity: {activity_type}")

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_CONTAINER_JOB_DERIVED_IDEMPOTENCY_KEY_PATCH,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1",
             "search_attributes": {}},
        ),
    )
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    node_inputs = {
        "spec": {
            "image": "docker.io/library/qualification-fixture:1.0.0",
            "command": ["sh", "-lc", "probe"],
            "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
    }
    expected_key = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="workload-step",
        execution_ordinal=1,
        operation="execute",
    )
    first = await workflow._execute_container_job_tool(
        node_inputs=dict(node_inputs), node_id="workload-step", execution_ordinal=1
    )
    # Lost acknowledgment: retry the same step/execution without a new mutation.
    second = await workflow._execute_container_job_tool(
        node_inputs=dict(node_inputs), node_id="workload-step", execution_ordinal=1
    )

    assert first["status"] == "COMPLETED"
    assert second["status"] == "COMPLETED"
    assert first["outputs"]["jobId"] == job_id
    assert second["outputs"]["jobId"] == job_id
    assert submitted_keys == [expected_key, expected_key]
    assert list(jobs_by_key.keys()) == [expected_key]


@pytest.mark.asyncio
async def test_run_execution_stage_skips_preserved_tool_step_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 restart: a preserved COMPLETED tool node is skipped end-to-end.

    Exercises the real ``_is_preserved_step`` branch inside
    ``_run_execution_stage`` (not just the predicate unit test): ``produce``
    is seeded as preserved with artifact refs, ``consume`` references the
    preserved output via ``ref.node``, and only ``consume`` reaches
    ``mm.tool.execute`` without an AgentRun.
    """

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
                            "summary": {
                                "ref": {
                                    "node": "produce",
                                    "json_pointer": "/outputs/outputSummaryRef",
                                }
                            }
                        },
                    },
                ],
                edges=[{"from": "produce", "to": "consume"}],
            )
        if activity_type == "mm.tool.execute":
            invocation = normalized.get("invocation_payload", {})
            assert invocation.get("id") == "consume"
            assert invocation["inputs"]["summary"] == "art:summary"
            return {"status": "COMPLETED", "outputs": {"ok": True}}
        return {"status": "COMPLETED", "outputs": {}}

    # Seed the preserved COMPLETED tool result after the ledger initializes,
    # exercising the workflow's preserved-step short-circuit on restart.
    # Mirror production materialization: preserved produce is completed, then
    # readiness refresh unblocks the dependent consume (pending -> ready).
    original_initialize = MoonMindRunWorkflow._initialize_step_ledger

    def _initialize_with_preserved(self, *, ordered_nodes, dependency_map, updated_at):
        original_initialize(
            self,
            ordered_nodes=ordered_nodes,
            dependency_map=dependency_map,
            updated_at=updated_at,
        )
        self._step_ledger_rows = [
            {
                "logicalStepId": "produce",
                "status": "completed",
                "preservedFrom": {"workflowId": "wf-0", "runId": "run-0"},
                "artifacts": {
                    "outputSummary": "art:summary",
                    "outputPrimary": "art:primary",
                },
            }
        ] + [
            row
            for row in self._step_ledger_rows
            if row.get("logicalStepId") != "produce"
        ]
        self._rebuild_step_ledger_index()
        self._refresh_step_readiness(updated_at=updated_at)

    monkeypatch.setattr(
        MoonMindRunWorkflow, "_initialize_step_ledger", _initialize_with_preserved
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )

    async def fail_if_child_workflow_starts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("restart must not start AgentRun for tool steps")

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
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1",
         "search_attributes": {}},
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
    assert tool_calls[0][1]["invocation_payload"]["id"] == "consume"


def _collect_payload_keys(value: Any, *, _seen: set[str] | None = None) -> set[str]:
    seen = _seen if _seen is not None else set()
    if isinstance(value, dict):
        for key, item in value.items():
            seen.add(str(key))
            _collect_payload_keys(item, _seen=seen)
    elif isinstance(value, list):
        for item in value:
            _collect_payload_keys(item, _seen=seen)
    return seen


@pytest.mark.asyncio
async def test_dispatcher_denies_capability_mismatch_and_payload_has_no_model_profile() -> None:
    """REQ-2 denied-authority breadth: wrong tool type fails closed.

    Unknown skill names are already covered. A capability/route mismatch
    (``agent_runtime`` sent to the deterministic dispatcher) must also fail
    closed as non-retryable ``INVALID_INPUT`` instead of dispatching.
    """

    from moonmind.workflows.skills.tool_dispatcher import (
        ToolActivityDispatcher,
        execute_tool_activity,
    )
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
    from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
    from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot

    definition = parse_tool_definition(_tool_definition_payload("test.consume"))
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:" + "c" * 64,
        artifact_ref="art:sha256:456",
        skills=(definition,),
    )
    dispatcher = ToolActivityDispatcher()

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload={
                "id": "consume",
                "tool": {"type": "agent_runtime", "name": "test.consume"},
                "inputs": {"ticket": "MM-1"},
                "options": {},
            },
            registry_snapshot=snapshot,
            dispatcher=dispatcher,
            context={"principal": "owner-1"},
        )
    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_skill_execute_payload_carries_no_model_profile_or_github_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 least privilege: deterministic payload has no blanket credentials.

    The skill ``execute_payload`` built in run.py must carry only the
    principal, registry snapshot ref, invocation, scoped context, and step
    idempotency key -- never a model Profile or GitHub connection.
    """

    import json

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
    _, payload, _kwargs = tool_calls[0]
    assert set(payload.keys()) == {
        "registry_snapshot_ref",
        "principal",
        "invocation_payload",
        "context",
        "idempotency_key",
    }
    assert payload["invocation_payload"]["tool"] == {
        "type": "skill",
        "name": "test.consume",
    }
    payload_keys = _collect_payload_keys(payload)
    forbidden = {
        "modelProfile",
        "model_profile",
        "profile",
        "githubConnection",
        "github_connection",
        "connection",
        "secret",
        "token",
        "taskQueue",
        "mounts",
    }
    assert payload_keys.isdisjoint(forbidden), (
        f"deterministic payload must not carry blanket credentials: "
        f"{sorted(payload_keys & forbidden)}"
    )
    dumped = json.dumps(payload, default=str).lower()
    assert "model profile" not in dumped
    assert "github connection" not in dumped


@pytest.mark.asyncio
async def test_raised_transient_activity_failure_records_once_with_retry_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 raised transient vs returned business failure on a skill node.

    A raised retryable Activity failure is recorded once at its owner
    (single ``mm.tool.execute`` attempt, failed ledger row, FAIL_FAST raise)
    under the definition-owned bounded retry policy, complementing the
    existing returned-``FAILED``-once test. The retryable/business mapping
    distinction is asserted via ``_activity_result_retryable``.
    """

    from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
    from moonmind.workflows.temporal.workflows.run import DEFAULT_ACTIVITY_CATALOG

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
            raise RuntimeError("transient system_error: upstream unavailable")
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

    with pytest.raises(RuntimeError, match="transient system_error"):
        await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")
    # Mapped once at its owner: one Activity attempt before the FAIL_FAST raise.
    assert tool_calls == ["mm.tool.execute"]

    row = workflow._step_ledger_row_for("consume")
    assert isinstance(row, dict)
    assert row.get("status") == "failed"

    # Bounded retry policy is definition-owned: raised transients retry via
    # the Temporal activity retry policy, not an unbounded workflow loop.
    definition = parse_tool_definition(_tool_definition_payload("test.consume"))
    route = DEFAULT_ACTIVITY_CATALOG.resolve_skill(definition)
    retry_policy = workflow._retry_policy_for_route(route)
    assert retry_policy.maximum_attempts == definition.policies.retries.max_attempts

    # Returned-result mapping distinction at the same owner.
    assert (
        workflow._activity_result_retryable(
            {"status": "FAILED", "outputs": {"error": "system_error"}},
            failure_message="system_error",
            tool_type="skill",
        )
        is True
    )
    assert (
        workflow._activity_result_retryable(
            {"status": "FAILED", "outputs": {"error": "validation_failed"}},
            failure_message="validation_failed",
            tool_type="skill",
        )
        is False
    )


@pytest.mark.asyncio
async def test_large_plan_output_ref_resolves_from_recorded_results_after_artifact_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 large inputs: artifact reads outside history + recorded refs.

    The plan and registry are materialized through the existing
    ``artifact.read`` Activities (outside Workflow history); dependency
    outputs then resolve deterministically from the recorded COMPLETED map.
    A large upstream output proves the ref path carries the value without
    re-reading an artifact or guessing a default.
    """

    large_output = "x" * 100_000
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
            if invocation.get("id") == "produce":
                return {"status": "COMPLETED", "outputs": {"ticket": large_output}}
            if invocation.get("id") == "consume":
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

    artifact_reads = [call for call in captured if call[0] == "artifact.read"]
    # Plan + pinned registry snapshot both materialize via artifact reads.
    assert len(artifact_reads) >= 2
    tool_calls = [call for call in captured if call[0] == "mm.tool.execute"]
    assert len(tool_calls) == 2
    consume_call = next(
        call for call in tool_calls if call[1]["invocation_payload"]["id"] == "consume"
    )
    assert consume_call[1]["invocation_payload"]["inputs"]["ticket"] == large_output


@pytest.mark.asyncio
async def test_unresolvable_ref_marks_step_ledger_failed_with_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-2 ledger visibility: ref failure is a failed Step ledger row.

    Beyond the raised ``ValueError``, the unresolvable dependency must leave
    a ``failed`` Step ledger row with a diagnostic category/message and a
    readiness refresh -- using the single Step status vocabulary.
    """

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

    row = workflow._step_ledger_row_for("consume")
    assert isinstance(row, dict)
    # Single Step status vocabulary: terminal failure is "failed".
    assert row.get("status") == "failed"
    summary = str(row.get("summary") or "")
    assert "missing" in summary
    last_error = str(row.get("lastError") or "")
    assert last_error, "ref failure must record a diagnostic category"
    diagnostic = workflow._failure_diagnostic
    assert isinstance(diagnostic, dict)
    assert diagnostic.get("stepId") == "consume"
    assert str(diagnostic.get("message") or ""), (
        "ref failure must keep a bounded diagnostic message"
    )
