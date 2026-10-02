"""MoonLadderStudios/MoonMind#4636: authored selection through production boundaries."""

from types import SimpleNamespace

import pytest

from moonmind.workflows.executions.execution_contract import (
    build_canonical_workflow_view,
)
from moonmind.workflows.executions.model_resolver import resolve_model_effort
from moonmind.workflows.executions.runtime_inheritance import (
    InheritedRuntime,
    apply_inherited_runtime_to_payload,
)


def profile():
    return SimpleNamespace(
        runtime_id="codex_cli",
        profile_id="test-profile",
        provider_id="openai",
        enabled=True,
        auth_state="connected",
        default_model="legacy-model",
        default_effort="legacy-effort",
        default_model_tier=2,
        model_tiers=[
            {"model": "tier-one", "effort": "low", "parameters": {}},
            {"model": "tier-two", "effort": "high", "parameters": {"temperature": 1}},
        ],
    )


@pytest.mark.parametrize(
    "pair",
    [
        {"model": None, "effort": None},
        {"model": None, "effort": "max"},
        {"model": "chosen", "effort": None},
    ],
)
def test_4636_custom_nulls_resolve_runtime_defaults_without_tier_or_scalar(pair):
    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=profile(),
        authored_runtime=pair,
        env={
            "MOONMIND_CODEX_MODEL": "runtime-model",
            "MOONMIND_CODEX_EFFORT": "medium",
        },
    )
    assert resolved.model == (pair["model"] or "runtime-model")
    assert resolved.effort == (pair["effort"] or "medium")
    assert resolved.effective_model_tier is None
    assert resolved.tier_parameters == {}
    assert resolved.model_source == (
        "task_override" if pair["model"] else "runtime_default"
    )


def test_4636_null_tier_fields_bypass_legacy_scalars():
    selected = profile()
    selected.model_tiers[1].update(model=None, effort=None)
    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=selected,
        requested_model_tier=2,
        env={
            "MOONMIND_CODEX_MODEL": "runtime-model",
            "MOONMIND_CODEX_EFFORT": "medium",
        },
    )
    assert (resolved.model, resolved.effort) == ("runtime-model", "medium")


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"modelTier": 2},
        {"model": None, "effort": None},
        {"modelTier": 2, "effort": "max"},
    ],
)
def test_4636_contract_retains_exact_model_field_presence(selection):
    view = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "test/repo",
            "workflow": {"instructions": "Do work", "runtime": selection},
        },
    )
    runtime = view["workflow"]["runtime"]
    assert {
        k: runtime[k]
        for k in ("modelTier", "model", "effort", "tierFallback")
        if k in runtime
    } == selection


@pytest.mark.parametrize(
    "selection", [{"modelTier": 2}, {"model": None, "effort": None}]
)
def test_4636_child_selection_does_not_refill_parent_pair(selection):
    task = {"runtime": dict(selection)}
    apply_inherited_runtime_to_payload(
        payload={},
        task_payload=task,
        inherited=InheritedRuntime(
            target_runtime="codex_cli",
            model="parent-model",
            effort="high",
            profile_id="parent-profile",
        ),
    )
    assert {
        k: task["runtime"][k]
        for k in ("modelTier", "model", "effort")
        if k in task["runtime"]
    } == selection
    assert task["runtime"]["profileId"] == "parent-profile"


@pytest.mark.asyncio
async def test_4636_step_normalization_and_resolution_retain_custom_nulls():
    from unittest.mock import AsyncMock

    from api_service.api.routers.executions import (
        _normalize_task_steps,
        _resolve_step_runtime_selections,
    )

    steps = _normalize_task_steps(
        {
            "steps": [
                {
                    "id": "custom-step",
                    "type": "skill",
                    "skill": {"name": "auto"},
                    "runtime": {
                        "model": None,
                        "effort": None,
                        "parameters": {"temperature": 0},
                    },
                }
            ]
        }
    )
    assert steps[0]["runtime"]["model"] is None
    assert steps[0]["runtime"]["effort"] is None
    session = SimpleNamespace(get=AsyncMock(return_value=profile()))
    await _resolve_step_runtime_selections(
        steps=steps,
        task_runtime={"modelTier": 2},
        task_target_runtime="codex_cli",
        task_profile_id="test-profile",
        session=session,
    )
    assert steps[0]["runtime"]["model"] is None
    assert steps[0]["runtime"]["effort"] is None
    assert "modelTier" not in steps[0]["runtime"]
    assert steps[0]["runtime"]["parameters"] == {"temperature": 0}


@pytest.mark.asyncio
async def test_4636_public_submission_rejects_new_strict_before_effects():
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from api_service.api.routers.executions import create_execution

    service = SimpleNamespace(create_execution=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await create_execution(
            payload={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {
                    "workflow": {"runtime": {"modelTier": 2, "tierFallback": "strict"}}
                },
            },
            service=service,
            session=None,
            user=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
        )
    assert error.value.status_code == 422
    assert "strict" in str(error.value.detail)
    service.create_execution.assert_not_called()


def test_4636_launch_uses_nullable_pair_and_keeps_effort_status(monkeypatch):
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher

    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        execution_profile_ref="test-profile",
        correlation_id="issue-4636",
        idempotency_key="issue-4636",
        parameters={"model": None, "effort": None, "temperature": 0},
    )
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request,
        profile=profile(),
        strategy=SimpleNamespace(
            effort_application_status=lambda _effort: "not_supported"
        ),
    )
    assert request.parameters["model"] == "runtime-model"
    assert request.parameters["effort"] == "medium"
    assert request.parameters["temperature"] == 0
    resolution = request.parameters["metadata"]["moonmind"]["modelEffortResolution"]
    assert resolution["effectiveModelTier"] is None
    assert resolution["effortApplicationStatus"] == "not_supported"


@pytest.mark.parametrize(
    "child,parent,expected",
    [
        ({"modelTier": 1}, {"model": None, "effort": "high"}, {"modelTier": 1}),
        (
            {"model": None, "effort": None},
            {"modelTier": 2},
            {"model": None, "effort": None},
        ),
        ({}, {"modelTier": 2}, {"modelTier": 2}),
    ],
)
def test_4636_runtime_planner_replaces_selection_atomically(child, parent, expected):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    result = _build_runtime_planner()(
        inputs={},
        parameters={
            "workflow": {
                "instructions": "Work",
                "runtime": {"mode": "codex_cli", **parent},
                "steps": [
                    {
                        "id": "work",
                        "type": "skill",
                        "skill": {"id": "auto"},
                        "instructions": "Work",
                        "runtime": child,
                    }
                ],
            },
            "model": "historical-model",
            "effort": "historical-effort",
        },
        snapshot=SimpleNamespace(
            version="test", digest="sha256:test", artifact_ref="artifact://test"
        ),
    )
    runtime = result["nodes"][0]["inputs"]["runtime"]
    assert {
        k: runtime[k] for k in ("modelTier", "model", "effort") if k in runtime
    } == expected


def test_4636_recurring_metadata_does_not_pin_profile_default():
    from api_service.api.routers.executions import _stamp_recurring_runtime_metadata

    parameters = {"workflow": {"runtime": {"mode": "codex_cli"}}}
    _stamp_recurring_runtime_metadata(
        initial_parameters=parameters,
        runtime_metadata={
            "model": "preview",
            "effort": "high",
            "targetRuntime": "codex_cli",
        },
    )
    assert "model" not in parameters["workflow"]["runtime"]
    assert "effort" not in parameters["workflow"]["runtime"]


@pytest.mark.parametrize("patched", [False, True])
def test_4636_run_request_null_presence_is_replay_versioned(monkeypatch, patched):
    from unittest.mock import patch

    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda name: (
            patched if name == "run-model-selection-presence-4636-v1" else True
        ),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="issue-4636",
            run_id="test",
            task_queue="test",
            namespace="moonmind",
        ),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "codex_cli", "model": None, "effort": None}
            },
            node_id="work",
            tool_name="codex_cli",
            workflow_parameters={"modelTier": 2, "model": "parent", "effort": "high"},
        )
    if patched:
        assert request.parameters["model"] is None
        assert request.parameters["effort"] is None
        assert "modelTier" not in request.parameters
        assert request.parameters["runtime"] == {"model": None, "effort": None}
    else:
        assert request.parameters["model"] == "parent"
        assert request.parameters["effort"] == "high"
        assert request.parameters["modelTier"] == 2


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"modelTier": 2},
        {"model": None, "effort": None},
        {"modelTier": 2, "effort": "max"},
    ],
)
def test_4636_parent_inheritance_uses_authored_selection_not_diagnostics(selection):
    from moonmind.workflows.executions.runtime_inheritance import (
        _extract_parent_runtime_fields,
        extract_inheritance_directive,
        has_explicit_child_runtime,
    )

    record = SimpleNamespace(
        parameters={
            "workflow": {"runtime": {"mode": "codex_cli", **selection}},
            "model": "historical-model",
            "effort": "historical-effort",
        }
    )
    inherited = _extract_parent_runtime_fields(record)
    task = {"runtime": {}}
    apply_inherited_runtime_to_payload(
        payload={}, task_payload=task, inherited=inherited
    )
    assert {
        k: task["runtime"][k]
        for k in ("modelTier", "model", "effort")
        if k in task["runtime"]
    } == selection
    custom = {"runtime": {"model": None, "effort": None}}
    assert extract_inheritance_directive({}, custom) == (None, None)
    assert has_explicit_child_runtime({}, custom)


@pytest.mark.parametrize(
    "selection", [{"modelTier": 2}, {"model": None, "effort": None}, {}]
)
def test_4636_edit_forwarding_retains_selection_presence(selection):
    from unittest.mock import patch

    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched", return_value=True
    ):
        update = MoonMindRunWorkflow()._runtime_selection_update_payload(
            {"parametersPatch": {"workflow": {"runtime": selection}}}
        )
    assert update is not None
    assert update["runtime"] == selection


@pytest.mark.parametrize(
    "selection", [{"modelTier": 2}, {"model": None, "effort": None}, {}]
)
def test_4636_pending_launch_edit_replaces_selection_without_changing_past_attempts(
    monkeypatch, selection
):
    from copy import deepcopy

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    workflow = MoonMindAgentRun()
    monkeypatch.setattr(workflow, "_workflow_patch_enabled", lambda _name: True)
    parameters = {
        "modelTier": 1,
        "model": "old",
        "effort": "high",
        "workflow": {"runtime": {"modelTier": 1, "parameters": {"temperature": 0}}},
        "attempts": [{"model": "already-launched"}],
    }
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        correlation_id="issue-4636",
        idempotency_key="edit-4636",
        parameters=deepcopy(parameters),
    )
    workflow._apply_runtime_selection_update(
        request,
        {"runtime": selection, "parametersPatch": {"workflow": {"runtime": selection}}},
        refresh_derived_selection=True,
    )
    assert {
        k: request.parameters[k]
        for k in ("modelTier", "model", "effort")
        if k in request.parameters
    } == selection
    assert {
        k: request.parameters["workflow"]["runtime"][k]
        for k in ("modelTier", "model", "effort")
        if k in request.parameters["workflow"]["runtime"]
    } == selection
    assert request.parameters["workflow"]["runtime"]["parameters"] == {"temperature": 0}
    assert request.parameters["attempts"] == parameters["attempts"]


def test_4636_child_selection_keeps_non_model_parameters_from_both_levels():
    from moonmind.runtime_intent import merge_runtime_selection

    runtime = merge_runtime_selection(
        {"modelTier": 2, "parameters": {"temperature": 0, "seed": 42}},
        {"model": None, "effort": None, "parameters": {"seed": 7}},
    )
    assert runtime == {
        "model": None,
        "effort": None,
        "parameters": {"temperature": 0, "seed": 7},
    }


@pytest.mark.parametrize("field", ["model", "effort"])
def test_4636_structural_selection_values_are_nullable_strings(field):
    from moonmind.runtime_intent import (
        RuntimeIntentValidationError,
        validate_runtime_tier_intent,
    )

    with pytest.raises(RuntimeIntentValidationError, match=field):
        validate_runtime_tier_intent(
            {"model": None, "effort": None, field: 42}, field_name="runtime"
        )


@pytest.mark.asyncio
async def test_4636_remediation_api_rejects_new_strict_before_effects():
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from api_service.api.routers.executions import create_remediation_execution

    service = SimpleNamespace(create_execution=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await create_remediation_execution(
            workflow_id="issue-4636",
            payload={"runtime": {"modelTier": 2, "tierFallback": "strict"}},
            service=service,
            session=None,
            user=SimpleNamespace(id="issue-4636-user"),
            _submit_enabled=None,
            principal_context={},
        )
    assert error.value.status_code == 422
    assert "strict" in str(error.value.detail)
    service.create_execution.assert_not_called()


@pytest.mark.parametrize(
    "selection,expected",
    [
        ({}, "tier-two"),
        ({"modelTier": 1}, "tier-one"),
        ({"model": None, "effort": None}, "runtime-model"),
    ],
)
def test_4636_omnigent_generic_plan_uses_authored_selection_over_admission_diagnostics(
    monkeypatch, selection, expected
):
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentExecutionPlanningService,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="omnigent",
        correlation_id="issue-4636",
        idempotency_key="omnigent-4636",
        parameters={
            "workflow": {"runtime": selection},
            "runtime": {"profileSelector": {"providerId": "openai"}},
            "model": "old-preview",
            "effort": "high",
        },
    )
    model, effort, route = service._resolve_model(
        request, SimpleNamespace(model={}), profile()
    )
    assert model == f"openai/{expected}"
    assert route == "openai"


def test_4636_repeated_launch_resolution_removes_only_previous_tier_defaults():
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher

    selected = profile()
    selected.model_tiers[1]["parameters"] = {
        "temperature": 1,
        "output_format": "strict_json",
    }
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        correlation_id="issue-4636",
        idempotency_key="params-4636",
        parameters={"runtime": {"modelTier": 2}, "trace": "kept"},
    )
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=selected, strategy=None
    )
    assert request.parameters["output_format"] == "strict_json"
    request.parameters["runtime"] = {
        "model": "custom",
        "effort": None,
        "parameters": {"temperature": 0},
    }
    request.parameters["temperature"] = 0
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=selected, strategy=None
    )
    assert "output_format" not in request.parameters
    assert request.parameters["temperature"] == 0
    assert request.parameters["trace"] == "kept"
    assert "modelTierResolution" not in request.parameters


@pytest.mark.parametrize(
    "selection,expected",
    [
        ({}, "tier-two"),
        ({"modelTier": 1}, "tier-one"),
        ({"model": None, "effort": None}, "runtime-model"),
    ],
)
def test_4636_omnigent_uses_effective_step_runtime_before_workflow_and_old_resolution(
    monkeypatch, selection, expected
):
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentExecutionPlanningService,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="omnigent",
        correlation_id="issue-4636",
        idempotency_key="step-4636",
        parameters={
            "workflow": {"runtime": {"modelTier": 2}},
            "runtime": selection,
            "model": "previous-launch",
            "effort": "high",
        },
    )
    model, _effort, route = service._resolve_model(
        request, SimpleNamespace(model={}), profile()
    )
    assert model == f"openai/{expected}"
    assert route == "openai"


def test_4636_run_request_keeps_explicit_parameter_provenance_at_launch_boundary(
    monkeypatch,
):
    from unittest.mock import patch

    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched", return_value=True
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="issue-4636",
            run_id="test",
            task_queue="test",
            namespace="moonmind",
        ),
    ):
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": "codex_cli",
                    "model": None,
                    "effort": None,
                    "parameters": {"seed": 7},
                }
            },
            node_id="work",
            tool_name="codex_cli",
            workflow_parameters={
                "workflow": {
                    "runtime": {
                        "modelTier": 2,
                        "parameters": {"seed": 42, "temperature": 0},
                    }
                }
            },
        )
    assert request.parameters["runtime"]["parameters"] == {"seed": 7, "temperature": 0}
    assert request.parameters["seed"] == 7
    assert request.parameters["temperature"] == 0


@pytest.mark.asyncio
async def test_4636_preview_returns_current_field_sources_for_saved_partial_and_null_custom(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    import api_service.api.routers.provider_profiles as router

    selected = profile()
    from datetime import datetime, timezone

    selected.updated_at = datetime(2026, 10, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(
        router, "_require_provider_profile_permission", lambda *_args: None
    )
    monkeypatch.setattr(router, "_can_view_profile", lambda *_args: True)
    response = await router.preview_model_tiers(
        profile_id="test-profile",
        body=router.ProviderProfileTierPreviewRequest.model_validate(
            {
                "steps": [
                    {"id": "saved", "model": "saved-model"},
                    {"id": "custom", "model": None, "effort": None},
                ]
            }
        ),
        session=SimpleNamespace(get=AsyncMock(return_value=selected)),
        current_user=None,
    )
    saved, custom = router.ProviderProfileTierPreviewResponse.model_validate(
        response
    ).items
    assert saved.effort_source == "provider_profile_default"
    assert saved.effort == "legacy-effort"
    assert custom.model_source == "runtime_default"
    assert custom.effort_source != "provider_profile_default"


@pytest.mark.parametrize("boundary", ["snapshot", "planner"])
@pytest.mark.parametrize(
    "artifact_selection, edited_selection",
    [
        ({"modelTier": 2}, {"model": None, "effort": None}),
        ({"model": None, "effort": "max"}, {"modelTier": 1}),
        ({"modelTier": 2}, {}),
        ({"model": None, "effort": "max"}, {}),
    ],
)
def test_4636_artifact_backed_edit_replaces_selection_at_snapshot_and_plan(
    boundary,
    artifact_selection,
    edited_selection,
):
    from moonmind.runtime_intent import model_selection_fields
    from moonmind.workflows.executions.execution_contract import merge_workflow_input
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    artifact = {
        "instructions": "Original #4636",
        "runtime": {
            "mode": "codex_cli",
            "parameters": {"seed": 42, "temperature": 0},
            **artifact_selection,
        },
        "steps": [
            {
                "id": "work",
                "instructions": "Work",
                "type": "skill",
                "skill": {"id": "auto"},
            }
        ],
    }
    edited = {"runtime": {"parameters": {"seed": 7}, **edited_selection}}
    if boundary == "snapshot":
        runtime = merge_workflow_input(artifact, edited)["runtime"]
    else:
        plan = _build_runtime_planner()(
            inputs={"workflow": artifact},
            parameters={"workflow": edited},
            snapshot=SimpleNamespace(
                version="test", digest="sha256:test", artifact_ref="artifact://test"
            ),
        )
        runtime = plan["nodes"][0]["inputs"]["runtime"]
    assert model_selection_fields(runtime) == edited_selection
    assert runtime["parameters"] == {"seed": 7, "temperature": 0}
    assert runtime["mode"] == "codex_cli"


@pytest.mark.parametrize("boundary", ["snapshot", "planner"])
@pytest.mark.parametrize("reset_runtime", [None, {"parameters": {"seed": 7}}])
def test_4636_artifact_step_reset_restores_workflow_inheritance(
    boundary, reset_runtime
):
    from moonmind.runtime_intent import model_selection_fields
    from moonmind.workflows.executions.execution_contract import merge_workflow_input
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    artifact = {
        "instructions": "Original #4636",
        "runtime": {"mode": "codex_cli", "modelTier": 2},
        "steps": [
            {
                "id": "work",
                "instructions": "Work",
                "type": "skill",
                "skill": {"id": "auto"},
                "runtime": {"model": None, "effort": "max", "parameters": {"seed": 42}},
            }
        ],
    }
    edited = {
        "steps": [
            {
                "id": "work",
                "instructions": "Work",
                **({"runtime": reset_runtime} if reset_runtime is not None else {}),
            }
        ]
    }
    if boundary == "snapshot":
        restored = merge_workflow_input(artifact, edited)
        runtime = restored["steps"][0]["runtime"]
        assert model_selection_fields(runtime) == {}
        assert restored["steps"][0]["instructions"] == "Work"
    else:
        plan = _build_runtime_planner()(
            inputs={"workflow": artifact},
            parameters={"workflow": edited},
            snapshot=SimpleNamespace(
                version="test", digest="sha256:test", artifact_ref="artifact://test"
            ),
        )
        runtime = plan["nodes"][0]["inputs"]["runtime"]
        assert model_selection_fields(runtime) == {"modelTier": 2}
    assert runtime["parameters"] == {"seed": 42 if reset_runtime is None else 7}


@pytest.mark.parametrize(
    "field,value",
    [("model", "new-model"), ("effort", "max"), ("model", None), ("effort", None)],
)
def test_4636_flat_edit_reaches_child_authored_pair_and_launch(
    monkeypatch, field, value
):
    from copy import deepcopy
    from unittest.mock import patch

    from moonmind.runtime_intent import model_selection_fields
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        ManagedRuntimeProfile,
    )
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
    from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
        CodexCliStrategy,
    )
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
    previous_runtime = {
        "model": "old-model",
        "effort": "high",
        "parameters": {"seed": 7},
    }
    attempts = [{"model": "already-launched", "effort": "high"}]
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        correlation_id="4636-edit",
        idempotency_key="4636-edit",
        parameters={
            "runtime": deepcopy(previous_runtime),
            "workflow": {"runtime": deepcopy(previous_runtime)},
            "attempts": deepcopy(attempts),
        },
    )
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched", return_value=True
    ):
        parent_signal = MoonMindRunWorkflow()._runtime_selection_update_payload(
            {"parametersPatch": {field: value}}
        )
    assert parent_signal is not None
    child = MoonMindAgentRun()
    monkeypatch.setattr(child, "_workflow_patch_enabled", lambda _name: True)
    child.update_runtime_selection(parent_signal)
    assert child.runtime_selection_updated_event.is_set()
    child._apply_runtime_selection_update(
        request, child._pending_runtime_selection_update, refresh_derived_selection=True
    )
    expected = {"model": "old-model", "effort": "high", field: value}
    assert model_selection_fields(request.parameters["runtime"]) == expected
    assert model_selection_fields(request.parameters["workflow"]["runtime"]) == expected
    assert request.parameters["runtime"]["parameters"] == {"seed": 7}
    assert request.parameters["attempts"] == attempts
    child._synchronize_runtime_selection_authority(request)
    selected_profile = ManagedRuntimeProfile.model_validate(
        {**vars(profile()), "command_template": ["codex", "exec"]}
    )
    strategy = CodexCliStrategy()
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=selected_profile, strategy=strategy
    )
    assert request.parameters["model"] == (expected["model"] or "runtime-model")
    assert request.parameters["effort"] == (expected["effort"] or "medium")
    assert request.parameters["seed"] == 7
    command = strategy.build_command(selected_profile, request)
    assert command[command.index("-m") + 1] == request.parameters["model"]
    assert request.parameters["attempts"] == attempts


def test_4636_flat_edit_keeps_recorded_behavior_before_repair_patch(monkeypatch):
    from unittest.mock import patch

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        correlation_id="4636-history",
        idempotency_key="4636-history",
        parameters={
            "runtime": {"model": "old-model", "effort": "high"},
            "workflow": {"runtime": {"model": "old-model", "effort": "high"}},
        },
    )
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda name: name != "run-model-selection-flat-edit-4636-v1",
    ):
        signal = MoonMindRunWorkflow()._runtime_selection_update_payload(
            {"parametersPatch": {"model": "new-model"}}
        )
    child = MoonMindAgentRun()
    monkeypatch.setattr(
        child,
        "_workflow_patch_enabled",
        lambda name: name != "agent-run-model-selection-flat-edit-4636-v1",
    )
    child._apply_runtime_selection_update(
        request, signal, refresh_derived_selection=True
    )
    assert request.parameters["model"] == "old-model"
    assert request.parameters["runtime"] == {"model": "old-model", "effort": "high"}


def test_4636_absent_nested_selection_preserves_legacy_flat_fields():
    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=profile(),
        authored_runtime=None,
        requested_model="saved-flat-model",
        requested_effort="max",
    )
    assert (resolved.model, resolved.effort) == ("saved-flat-model", "max")
    assert resolved.model_source == "task_override"
    assert resolved.effort_source == "task_override"


def test_4636_authored_custom_nulls_supersede_legacy_flat_fields():
    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=profile(),
        authored_runtime={"model": None, "effort": None},
        requested_model="stale-flat-model",
        requested_effort="max",
        env={
            "MOONMIND_CODEX_MODEL": "runtime-model",
            "MOONMIND_CODEX_EFFORT": "medium",
        },
    )
    assert (resolved.model, resolved.effort) == ("runtime-model", "medium")


def test_4636_authored_tier_supersedes_legacy_flat_fields():
    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=profile(),
        authored_runtime={"modelTier": 1},
        requested_model="stale-flat-model",
        requested_effort="max",
    )
    assert (resolved.model, resolved.effort) == ("tier-one", "low")


@pytest.mark.parametrize("complete_selection", [False, True])
def test_4636_artifact_step_merge_matches_stable_ids_after_reordering(
    complete_selection,
):
    from copy import deepcopy

    from moonmind.runtime_intent import model_selection_fields
    from moonmind.workflows.executions.execution_contract import merge_workflow_input

    artifact = {
        "steps": [
            {
                "id": "strict",
                "instructions": "Strict work",
                "runtime": {
                    "modelTier": 2,
                    "tierFallback": "strict",
                    "parameters": {"seed": 1},
                },
            },
            {
                "id": "custom",
                "instructions": "Custom work",
                "runtime": {
                    "model": "chosen",
                    "effort": "max",
                    "parameters": {"seed": 2},
                },
            },
        ]
    }
    before = deepcopy(artifact)
    merged = merge_workflow_input(
        artifact,
        {"steps": [{"id": "custom"}, {"id": "strict"}]},
        runtime_selection_is_complete=complete_selection,
    )
    assert [step["instructions"] for step in merged["steps"]] == [
        "Custom work",
        "Strict work",
    ]
    assert [step["runtime"]["parameters"] for step in merged["steps"]] == [
        {"seed": 2},
        {"seed": 1},
    ]
    expected_selections = [
        {"model": "chosen", "effort": "max"},
        {"modelTier": 2, "tierFallback": "strict"},
    ]
    assert [model_selection_fields(step["runtime"]) for step in merged["steps"]] == (
        [{}, {}] if complete_selection else expected_selections
    )
    assert artifact == before


@pytest.mark.parametrize(
    "artifact_id,edited_id,matched",
    [
        (None, None, True),
        ("saved", None, False),
        (None, "new", False),
        ("saved", "new", False),
    ],
)
def test_4636_artifact_step_merge_uses_position_only_without_identity(
    artifact_id, edited_id, matched
):
    from moonmind.workflows.executions.execution_contract import merge_workflow_input

    artifact_step = {
        "instructions": "Saved work",
        "runtime": {"parameters": {"seed": 42}},
    }
    edited_step = {}
    if artifact_id is not None:
        artifact_step["id"] = artifact_id
    if edited_id is not None:
        edited_step["id"] = edited_id
    merged = merge_workflow_input(
        {"steps": [artifact_step]},
        {"steps": [edited_step]},
        runtime_selection_is_complete=False,
    )["steps"][0]
    if matched:
        assert merged["instructions"] == "Saved work"
        assert merged["runtime"]["parameters"] == {"seed": 42}
    else:
        assert "instructions" not in merged
        assert "runtime" not in merged


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_step_id", ["strict", "custom"])
async def test_4636_reordered_artifact_strict_provenance_stays_with_its_step(
    strict_step_id,
):
    from unittest.mock import AsyncMock

    from moonmind.runtime_intent import (
        RuntimeIntentValidationError,
        validate_model_selection_submission,
    )

    strict_runtime = {"modelTier": 2, "tierFallback": "strict"}
    artifact = {
        "workflow": {
            "steps": [
                {"id": "strict", "runtime": dict(strict_runtime)},
                {"id": "custom", "runtime": {"model": None, "effort": None}},
            ]
        }
    }
    saved = {
        "inputArtifactRef": "art-saved",
        "workflow": {
            "steps": [
                {"id": "custom"},
                {"id": "strict"},
            ]
        },
    }
    payload = {
        "workflow": {"steps": [{"id": strict_step_id, "runtime": dict(strict_runtime)}]}
    }
    reader = AsyncMock(return_value=artifact)
    if strict_step_id == "strict":
        await validate_model_selection_submission(
            payload, saved_payload=saved, read_input_artifact=reader
        )
    else:
        with pytest.raises(RuntimeIntentValidationError, match="new strict"):
            await validate_model_selection_submission(
                payload, saved_payload=saved, read_input_artifact=reader
            )
    reader.assert_awaited_once_with("art-saved")


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_key", ["inputArtifactRef", "input_artifact_ref"])
@pytest.mark.parametrize(
    "selection", [{}, {"modelTier": 1}, {"model": None, "effort": None}]
)
async def test_4636_new_non_strict_artifact_replaces_unreadable_saved_reference(
    ref_key, selection
):
    from unittest.mock import AsyncMock

    from moonmind.runtime_intent import validate_model_selection_submission

    async def read(ref):
        if ref == "art-old-missing":
            raise FileNotFoundError("saved artifact is unavailable")
        assert ref == "art-new"
        return {
            "draft": {"workflow": {"instructions": "Replacement", "runtime": selection}}
        }

    reader = AsyncMock(side_effect=read)
    await validate_model_selection_submission(
        {ref_key: "art-new"},
        saved_payload={ref_key: "art-old-missing"},
        read_input_artifact=reader,
    )
    reader.assert_awaited_once_with("art-new")


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_strict", [True, False])
async def test_4636_new_strict_artifact_still_requires_saved_selection_provenance(
    matching_strict,
):
    from unittest.mock import AsyncMock, call

    from moonmind.runtime_intent import (
        RuntimeIntentValidationError,
        validate_model_selection_submission,
    )

    async def read(ref):
        tier = 2 if ref == "art-new" or matching_strict else 1
        return {"workflow": {"runtime": {"modelTier": tier, "tierFallback": "strict"}}}

    reader = AsyncMock(side_effect=read)
    kwargs = {
        "saved_payload": {"inputArtifactRef": "art-old"},
        "read_input_artifact": reader,
    }
    if matching_strict:
        await validate_model_selection_submission(
            {"inputArtifactRef": "art-new"}, **kwargs
        )
    else:
        with pytest.raises(RuntimeIntentValidationError, match="new strict"):
            await validate_model_selection_submission(
                {"inputArtifactRef": "art-new"}, **kwargs
            )
    assert reader.await_args_list == [call("art-new"), call("art-old")]


def test_4636_tier_metadata_defaults_serialize_without_mutating_profile():
    import json
    from copy import deepcopy

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher

    selected = profile()
    selected.model_tiers[1]["parameters"] = {
        "metadata": {
            "trace": {"label": "tier-owned"},
            "moonmind": {"source": "profile"},
        },
    }
    before = deepcopy(selected.model_tiers)
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="codex_cli",
        correlation_id="4636-metadata",
        idempotency_key="4636-metadata",
        parameters={"runtime": {"modelTier": 2}},
    )
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=selected, strategy=None
    )
    parameters = json.loads(request.model_dump_json())["parameters"]
    metadata = parameters["metadata"]
    assert metadata["trace"] == {"label": "tier-owned"}
    assert metadata["moonmind"]["source"] == "profile"
    assert (
        metadata["moonmind"]["modelEffortResolution"]["tierParameterDefaults"]
        == before[1]["parameters"]
    )
    assert selected.model_tiers == before
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=selected, strategy=None
    )
    assert json.loads(request.model_dump_json())["parameters"]["model"] == "tier-two"
    assert selected.model_tiers == before
