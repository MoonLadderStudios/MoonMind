"""Persisted omitted tool work reaches real planning and Run dispatch without agents."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete

from api_service.db.models import ManagedAgentProviderProfile
from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload
from moonmind.workflows.skills.tool_registry import (
    ToolRegistrySnapshot,
    compute_registry_digest,
    parse_tool_registry,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as _saved_profile_submission,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    stored_parameters,
)

saved_profile_submission = _saved_profile_submission


@pytest.mark.asyncio
@pytest.mark.parametrize("default_runtime", ["omnigent", "codex_cli"])
@pytest.mark.parametrize("cancelled", [False, True], ids=["dispatch", "cancel"])
async def test_saved_unavailable_agent_default_does_not_gate_tool_dispatch(
    saved_profile_submission, monkeypatch, default_runtime, cancelled
):
    context = saved_profile_submission
    await context.catalog.apply_overrides(
        changes={"workflow.default_runtime": default_runtime}
    )
    await context.session.execute(delete(ManagedAgentProviderProfile))
    await context.session.commit()
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2020-01-01T00:00:00Z")
    task = {
        "instructions": "Discover documents without an agent.",
        "publish": {"mode": "none"},
        "steps": [
            {
                "id": "discover",
                "type": "tool",
                "tool": {"id": "document.discover", "inputs": {"directory": "."}},
            }
        ],
    }
    response = await context.client.post(
        "/api/executions", json={"type": "workflow", "payload": {"workflow": task}}
    )
    parameters = await stored_parameters(context, response)
    assert parameters["targetRuntime"] == default_runtime
    assert "omnigentExecutionPlan" not in parameters

    registry = {"tools": [default_registry_tool_payload(name="document.discover")]}
    definitions = parse_tool_registry(registry)
    snapshot = ToolRegistrySnapshot(
        digest=compute_registry_digest(skills=definitions),
        artifact_ref="artifact://registry/tool-default",
        skills=definitions,
    )
    plan = _build_runtime_planner()(inputs={}, parameters=parameters, snapshot=snapshot)
    assert len(plan["nodes"]) == 1
    assert plan["nodes"][0]["tool"] == {"type": "skill", "name": "document.discover"}
    plan_ref = "artifact://plan/tool-default"
    calls = []

    async def activity(name, payload, **kwargs):
        value = payload if isinstance(payload, dict) else payload.model_dump()
        calls.append((name, value, kwargs))
        if name == "provider_profile.list":
            return {"profiles": []}
        if name == "artifact.read":
            source = (
                registry if value["artifact_ref"] == snapshot.artifact_ref else plan
            )
            assert value["artifact_ref"] in (snapshot.artifact_ref, plan_ref)
            return json.dumps(source).encode()
        if name == "artifact.create":
            return {"artifact_id": "artifact://tool/manifest"}
        if name in {"artifact.write", "artifact.link"}:
            return {}
        if name == "mm.tool.execute":
            return {"status": "COMPLETED", "outputs": {"documentPaths": []}}
        raise AssertionError(f"Unexpected activity: {name}")

    async def wait_condition(predicate, **_kwargs):
        assert predicate(), "Unexpected workflow wait"

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait_condition)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id=response.json()["workflowId"],
            run_id="run-tool-only",
            task_queue="mm.workflow.user.v2",
            start_time=now,
            parent=None,
            continued_run_id=None,
            search_attributes={},
            typed_search_attributes={},
        ),
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: now)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda *_args: None)
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda *_args: None
    )
    # Exercise the current runtime admission, profile lookup and trusted tool
    # dispatch guards. Other patches have separate retained-history coverage.
    patches = {
        "run-supported-runtime-admission-v1",
        run_module.RUN_FETCH_PROFILE_SNAPSHOTS_PATCH,
        run_module.RUN_PROFILE_SNAPSHOT_RUNTIME_AUTHORITY_PATCH,
        run_module.RUN_TRUSTED_TOOL_REGISTRY_PATCH,
        run_module.RUN_TOOL_RUNTIME_SELECTION_CONTEXT_PATCH,
    }
    monkeypatch.setattr(run_module.workflow, "patched", lambda name: name in patches)
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    monkeypatch.setattr(run_module, "execute_typed_activity", activity)
    child = AsyncMock(side_effect=AssertionError("Tool work must not launch an agent"))
    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", child)
    owner = run_module.MoonMindRunWorkflow()
    owner._owner_id = "tool-owner"
    # Use the same runtime-context owners as normal Run startup.
    owner._target_runtime = owner._runtime_visibility_from_parameters(parameters)
    owner._runtime_inheritance_parameters = (
        owner._runtime_inheritance_parameters_from_parameters(parameters)
    )
    assert owner._target_runtime == default_runtime
    agent = Mock(
        side_effect=AssertionError("Tool work must not build an agent request")
    )
    monkeypatch.setattr(owner, "_build_agent_execution_request", agent)
    resolved = await owner._run_planning_stage(
        parameters=parameters, input_ref=None, plan_ref=plan_ref
    )
    assert resolved == plan_ref
    owner._cancel_requested = cancelled
    await owner._run_execution_stage(parameters=parameters, plan_ref=resolved)
    assert any(name == "provider_profile.list" for name, _, _ in calls)
    tools = [
        (payload, kwargs)
        for name, payload, kwargs in calls
        if name == "mm.tool.execute"
    ]
    assert len(tools) == (0 if cancelled else 1)
    if tools:
        payload, kwargs = tools[0]
        assert payload["invocation_payload"]["tool"]["name"] == "document.discover"
        assert kwargs["task_queue"] == "mm.activity.sandbox"
        selection = payload["context"]["runtime_selection"]
        assert selection["targetRuntime"] == default_runtime
        assert selection["workflow"]["runtime"]["mode"] == default_runtime
        assert "profileId" not in selection
    agent.assert_not_called()
    child.assert_not_awaited()
