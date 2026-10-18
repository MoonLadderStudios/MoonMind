"""Keep finishing authority on the plan boundary, even without agent steps."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api_service.api.routers.executions import _raw_request_selects_omnigent
from api_service.db.models import ManagedAgentProviderProfile
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.config.settings import settings
from moonmind.workflows.executions.execution_contract import workflow_requires_agent
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    saved_profile_submission as saved_profile_submission,
    stored_parameters,
)
from tests.unit.services.test_recurring_workflows_service import _mm3935_plan_admission


def _tool_parameters(*, finish_mode, location="workflow_publish"):
    workflow = {
        "instructions": "Resolve the pull request target.",
        "steps": [
            {
                "type": "tool",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "350"},
                },
            }
        ]
    }
    parameters = {"targetRuntime": "omnigent", "workflow": workflow}
    # This deliberately lacks the closed native graph's repository input.
    # Finishing intent still has to reach the owner that validates authority.
    container = {
        "parameters": parameters,
        "workflow": workflow,
        "publish": parameters.setdefault("publish", {}),
        "workflow_publish": workflow.setdefault("publish", {}),
    }[location]
    container["mergeAutomation"] = {"enabled": True, "finishMode": finish_mode}
    return parameters


@pytest.mark.parametrize("finish_mode", ["review_only", "fix_only", "merge"])
@pytest.mark.parametrize(
    "location", ["parameters", "workflow", "publish", "workflow_publish"]
)
@pytest.mark.parametrize("runtime", ["omnigent", None])
def test_raw_tool_finishing_requires_product_plan_boundary(
    monkeypatch, finish_mode, location, runtime
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    parameters = _tool_parameters(finish_mode=finish_mode, location=location)
    if runtime is None:
        parameters.pop("targetRuntime")
    assert workflow_requires_agent(parameters["workflow"], parameters) is (
        finish_mode != "review_only"
    )
    assert _raw_request_selects_omnigent(
        workflow_type="MoonMind.UserWorkflow", parameters=parameters
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_mode", ["review_only", "fix_only", "merge"])
@pytest.mark.parametrize("compiler_rejects", [False, True])
async def test_recurring_tool_finishing_reaches_plan_owner(
    monkeypatch, finish_mode, compiler_rejects
):
    admission = _mm3935_plan_admission(monkeypatch)
    if compiler_rejects:
        admission.compile_plan.side_effect = ValueError("finishing authority denied")
    profile = ManagedAgentProviderProfile(
        profile_id=admission.snapshot["providerProfileRef"],
        runtime_id="codex_cli",
        provider_id="openai",
        model_tiers=[{"label": "Default", "model": "test-model", "effort": "low"}],
        default_model_tier=1,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=profile))
    recurring = RecurringWorkflowsService(
        session, temporal_client_adapter=AsyncMock(), artifact_service=SimpleNamespace()
    )
    parameters = _tool_parameters(finish_mode=finish_mode)
    definition_id = uuid4()
    kwargs = {
        "definition_id": definition_id,
        "target": {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": parameters,
        },
        "agent_profile_selection": None,
        "actor": SimpleNamespace(id=uuid4()),
    }
    if compiler_rejects:
        with pytest.raises(
            RecurringWorkflowValidationError, match="finishing authority denied"
        ):
            await recurring._admit_omnigent_schedule_target(**kwargs)
    else:
        admitted = await recurring._admit_omnigent_schedule_target(**kwargs)
        assert admitted is not None
        assert admitted["initialParameters"]["omnigentExecutionPlan"] == (
            admission.plan_binding
        )
    admission.default_resolver.assert_awaited_once()
    admission.compile_plan.assert_awaited_once()
    assert admission.compile_plan.await_args.kwargs["workflow_id"] == (
        f"mm-schedule:{definition_id}"
    )
    compiled = admission.compile_plan.await_args.kwargs["initial_parameters"]
    assert compiled["workflow"] == parameters["workflow"]
    assert compiled["agentProfileSnapshot"] == admission.snapshot


@pytest.mark.parametrize("enabled", ["not-a-bool", {"unexpected": True}])
def test_malformed_finishing_intent_cannot_skip_plan_validation(enabled):
    parameters = _tool_parameters(finish_mode="review_only")
    parameters["workflow"]["publish"]["mergeAutomation"]["enabled"] = enabled
    assert _raw_request_selects_omnigent(
        workflow_type="MoonMind.UserWorkflow", parameters=parameters
    )


@pytest.mark.parametrize("enabled", [None, False, "false", 0])
def test_disabled_finishing_retains_omnigent_product_admission(enabled):
    parameters = _tool_parameters(finish_mode="review_only")
    parameters["workflow"]["publish"]["mergeAutomation"]["enabled"] = enabled
    assert _raw_request_selects_omnigent(
        workflow_type="MoonMind.UserWorkflow", parameters=parameters
    )


@pytest.mark.asyncio
async def test_admitted_raw_omnigent_tool_retry_keeps_recorded_identity(
    saved_profile_submission, monkeypatch,
):
    context = saved_profile_submission
    parameters = _tool_parameters(finish_mode="review_only")
    parameters["workflow"]["publish"].pop("mergeAutomation")
    # Seed a durable identity that the earlier raw-tool exemption admitted.
    # Retrying it must reconcile that work, not apply fresh launch admission.
    original = await context.service.create_execution(
        workflow_type="MoonMind.UserWorkflow",
        owner_id=None,
        title=None,
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters=parameters,
        idempotency_key="retained-raw-tool",
    )
    original_parameters = deepcopy(original.parameters)
    saved = await context.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    await context.session.delete(saved)
    await context.session.commit()
    monkeypatch.setattr(settings.workflow, "default_runtime", "claude_code")
    await context.catalog.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_provider_profile_ref": "alternative-claude_code",
            "workflow.default_runtime": "claude_code",
        },
    )
    context.settings_reads.clear()
    repeated = await context.client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": parameters,
            "idempotencyKey": "retained-raw-tool",
        },
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["workflowId"] == original.workflow_id
    assert await stored_parameters(context, repeated) == original_parameters
    assert not context.settings_reads
    context.service._client_adapter.start_workflow.assert_awaited_once()
