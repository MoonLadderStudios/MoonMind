"""Schedule cache identity and workflow adoption preserve step authority."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.omnigent_execution_plan_service import PersistedOmnigentExecutionPlan
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    [{}, {"id": "selected"}, {"stepId": "selected"}, {"stepRef": "selected"}, {"ref": "selected"}],
)
@pytest.mark.parametrize("task_key", ["workflow", "task"])
async def test_schedule_preserves_canonical_step_identity_and_selection(
    identity, task_key
):
    from api_service.api.routers.executions import (
        _build_recurring_target,
        _resolve_recurring_runtime_metadata,
    )

    request = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            task_key: {
                "runtime": {"mode": "codex_cli", "model": "root-model"},
                "steps": [
                    {"instructions": "Generate a report."},
                    {
                        **identity,
                        "instructions": "Review it.",
                        "runtime": {
                            "model": "step-model",
                            "effort": None,
                            "parameters": {"native_option": "retain"},
                        },
                    },
                ],
            },
        },
    }
    original = deepcopy(request)
    metadata = await _resolve_recurring_runtime_metadata(request, session=None)
    target = _build_recurring_target(request, runtime_metadata=metadata)
    steps = target["initialParameters"][task_key]["steps"]
    assert [step["id"] for step in steps] == [
        "step-1", "selected" if identity else "step-2"
    ]
    assert steps[1]["runtime"]["model"] == "step-model"
    assert steps[1]["runtime"]["effort"] is None
    assert steps[1]["runtime"]["parameters"] == {"native_option": "retain"}
    assert request == original


def test_replacement_without_specializations_clears_old_workflow_handoffs(monkeypatch):
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        planDigest="sha256:" + "a" * 64, planArtifactRef="art_root",
        taskInputSnapshotRef="art_input", taskInputSnapshotDigest="sha256:" + "b" * 64,
    )
    admission = PersistedOmnigentExecutionPlan(
        artifact_access=None, envelope=None, binding=binding, artifact_refs=(),
        resolved_skillset_ref="art_skills", resolved_skillset_digest="sha256:" + "c" * 64,
    )
    original = {"omnigentStepExecutionPlans": {"selected": {"omnigentExecutionPlan": {"planRef": "old"}}}}
    controller = MoonMindRunWorkflow()
    controller._original_input_payload = {"initial_parameters": deepcopy(original)}
    monkeypatch.setattr(controller, "_workflow_patch_enabled", lambda _: True)
    for result in (admission.execution_parameters, {"omnigentExecutionPlan": binding.model_dump(mode="json", by_alias=True), "resolvedSkillsetRef": "art_skills"}):
        parameters = deepcopy(original)
        controller._adopt_admitted_execution_plan(parameters, {"disposition": "admitted", **result}, subject="Replacement")
        assert parameters["omnigentStepExecutionPlans"] == {}
        assert controller._original_input_payload["initial_parameters"]["omnigentStepExecutionPlans"] == {}
    specialized = replace(admission, step_parameters={"selected": {"model": "own"}})
    handed_off = specialized.execution_parameters
    handed_off["omnigentStepExecutionPlans"]["selected"]["model"] = "changed"
    assert specialized.step_parameters["selected"]["model"] == "own"


@pytest.mark.asyncio
async def test_schedule_cache_ignores_generated_refs_and_tracks_authored_step_selection(monkeypatch):
    monkeypatch.setattr("api_service.services.omnigent_policies.OmnigentPolicyService.resolve_runtime_snapshot", AsyncMock(return_value={"policy": "same"}))
    service = RecurringWorkflowsService(AsyncMock())
    provider = SimpleNamespace(profile_id="selected", runtime_id="codex_cli", provider_id="openai")
    binding = SimpleNamespace(task_input_snapshot_digest="sha256:" + "a" * 64)
    parameters = {"workflow": {"steps": [{"id": "selected", "runtime": {"modelTier": 2}}]}}
    async def digest(value):
        return await service._schedule_plan_inputs_digest(target={}, initial_parameters=value, provider_profile=provider, binding=binding)
    before = await digest(parameters)
    generated = {**deepcopy(parameters), "omnigentExecutionPlan": {"planRef": "new-root"}, "resolvedSkillsetRef": "new-skills", "omnigentStepExecutionPlans": {"selected": {"omnigentExecutionPlan": {"planRef": "new-child"}, "resolvedSkillsetRef": "new-step-skills"}}}
    assert await digest(generated) == before
    provider.model_tiers = [{"model": "updated-tier-model"}]
    assert await digest(generated) != before
    generated["workflow"]["steps"][0]["runtime"]["modelTier"] = 1
    assert await digest(generated) != before


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("change", ["active_configuration", "step_policy"])
async def test_schedule_cache_tracks_resolved_step_authority(monkeypatch, pinned, change):
    from tests.unit.api_service.test_profile_execution_selection import (
        configuration,
        provider,
    )

    root = provider(profile_id="root")
    step = provider(profile_id="step")
    row, original = configuration("step-configuration")
    updated = deepcopy(original)
    updated.version = 2
    updated.digest = "sha256:" + "b" * 64
    updated.document["execution"]["allowedLaunchPolicyRefs"] = ["isolated@2"]
    if pinned:
        step.execution_configuration = {"profileId": row.profile_id, "version": original.version, "digest": original.digest}
    monkeypatch.setattr("api_service.services.profile_execution_selection.load_execution_configurations", AsyncMock(return_value=[(row, original), (row, updated)]))
    policies = {"root@1": {"policy": "root"}, "isolated@1": {"policy": "step"}, "isolated@2": {"policy": "step-2"}}
    monkeypatch.setattr("api_service.services.omnigent_policies.OmnigentPolicyService.resolve_runtime_snapshot", AsyncMock(side_effect=lambda ref: policies[ref]))
    session = AsyncMock()
    session.get.return_value = step
    service = RecurringWorkflowsService(session)
    parameters = {
        "targetRuntime": "omnigent", "agentProfileSnapshot": {"launchPolicyRef": "root@1"},
        "workflow": {"steps": [{"id": "selected", "runtime": {"providerProfileRef": step.profile_id}}]},
    }
    async def digest():
        return await service._schedule_plan_inputs_digest(
            target={}, initial_parameters=parameters, provider_profile=root,
            binding=SimpleNamespace(task_input_snapshot_digest="sha256:" + "c" * 64),
        )
    before = await digest()
    assert before is not None
    if change == "active_configuration":
        row.active_version = updated.version
    else:
        policies["isolated@1"] = {"policy": "updated-step-policy"}
    after = await digest()
    if pinned and change == "active_configuration":
        assert after == before
    else:
        assert after != before
