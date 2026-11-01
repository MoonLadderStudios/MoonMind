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
