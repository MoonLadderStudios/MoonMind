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
