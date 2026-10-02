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
    from api_service.api.routers.provider_profiles import (
        ProviderProfileTierPreviewRequest,
        ProviderProfileTierPreviewResponse,
        preview_model_tiers,
    )

    selected = profile()
    from datetime import datetime, timezone

    selected.updated_at = datetime(2026, 10, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(
        router, "_require_provider_profile_permission", lambda *_args: None
    )
    monkeypatch.setattr(router, "_can_view_profile", lambda *_args: True)
    response = await preview_model_tiers(
        profile_id="test-profile",
        body=ProviderProfileTierPreviewRequest.model_validate(
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
    saved, custom = ProviderProfileTierPreviewResponse.model_validate(response).items
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
