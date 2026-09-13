"""Replay the compact workspace receipt that stranded a closed scheduled run."""

from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.omnigent.test_capacity_readmission_turn import admitted_request
from tests.unit.omnigent.test_generic_platform_production_services import (
    _exact_plan,
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)


def workspace_request(request, plan):
    payload = admitted_request(request, plan, 1).model_dump(by_alias=True, mode="json")
    payload["parameters"]["publishMode"] = "none"
    payload["stepExecution"] = {
        "workflowId": "workflow-1", "runId": "run-1",
        "logicalStepId": "assess", "executionOrdinal": 1,
        "stepExecutionId": "workflow-1:run-1:assess:execution:1",
        "runtimeContextPolicy": "fresh_agent_run",
        "omnigentExecutionPlan": payload["omnigentExecutionPlan"],
    }
    payload["workspaceSpec"]["workspaceLocator"] = {
        "kind": "sandbox", "workspaceId": "workspace-1", "relativePath": "repo",
    }
    return AgentExecutionRequest.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["current", "historical", "conflicting_step", "conflicting_request"])
async def test_janitor_saves_workspace_before_releasing_host(shape):
    harness = await _generic_publication_harness({})
    plan = _exact_plan("opencode-go/model")
    request = workspace_request(harness.publish_request, plan)
    await _prime_attested_host_binding(harness, plan)
    binding = await harness.runtime_store.get(stable_binding_id(
        execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key,
    ))
    payload = request.model_dump(by_alias=True, mode="json", exclude_none=True)
    if shape == "historical":
        payload.pop("omnigentExecutionPlan")
    elif shape.startswith("conflicting"):
        target = payload["stepExecution"] if shape == "conflicting_step" else payload
        target["omnigentExecutionPlan"]["planRef"] = "omnigent-execution-plan:sha256:" + "f" * 64
        target["omnigentExecutionPlan"]["planDigest"] = "sha256:" + "f" * 64
    await harness.runtime_store.update(
        binding.bindingId, expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        updates={"phaseResults": {"workspace": payload}},
    )
    async def save(observed):
        assert observed.omnigent_execution_plan == request.omnigent_execution_plan
        assert observed.step_execution == request.step_execution
        assert "host-cleaned" not in harness.events
        return {"checkpointRef": "artifact:verified-workspace"}

    save_call = AsyncMock(side_effect=save)
    harness.realizer._workspace_publisher.save_request_workspace = save_call
    harness.realizer._provider_leases.release_from_binding = AsyncMock()
    if shape.startswith("conflicting"):
        with pytest.raises((HarnessPlatformError, ValueError)):
            await harness.realizer.reconcile(plan.planRef, binding.bindingId)
        save_call.assert_not_awaited()
        assert "host-cleaned" not in harness.events
        return
    await harness.realizer.reconcile(plan.planRef, binding.bindingId)
    await harness.realizer.reconcile(plan.planRef, binding.bindingId)
    after = await harness.runtime_store.get(binding.bindingId)
    assert after.state is RuntimeBindingState.cleaned
    assert after.phaseResults["saved"]["checkpointRef"] == "artifact:verified-workspace"
    save_call.assert_awaited_once()
    assert harness.events.count("host-cleaned") == 1


@pytest.mark.asyncio
async def test_live_session_persists_valid_recovery_request():
    harness = await _generic_publication_harness({})
    plan = _plan("opencode-go/model")
    request = workspace_request(harness.publish_request, plan)
    harness.realizer._workspace_publisher.save_request_workspace = AsyncMock(
        return_value={"checkpointRef": "artifact:saved"},
    )
    await harness.realizer.execute(request, plan)
    binding = await harness.runtime_store.get(stable_binding_id(
        execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key,
        admission_epoch=1,
    ))
    restored = AgentExecutionRequest.model_validate(binding.phaseResults["workspace"])
    assert restored.omnigent_execution_plan == request.omnigent_execution_plan
    assert restored.step_execution == request.step_execution
