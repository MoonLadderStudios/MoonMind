"""Reset replay keeps a completed child's controller-owned Step identity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows import run as module


def _boundary(monkeypatch, source_run="original-run"):
    workflow_id, reset_run, step_id = "parent", "reset-run", "assess"
    child_id = f"{workflow_id}:agent:{step_id}"
    monkeypatch.setattr(
        module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=workflow_id,
            run_id=reset_run,
            first_execution_run_id="first-run-in-chain",
        ),
    )
    patches = []

    def patched(marker):
        patches.append(marker)
        return True

    monkeypatch.setattr(module.workflow, "patched", patched)
    owner = module.MoonMindUserWorkflow()
    monkeypatch.setattr(owner, "_workflow_is_replaying", lambda: True)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=workflow_id,
        idempotencyKey="assess",
        stepExecution={
            "workflowId": workflow_id,
            "runId": reset_run,
            "logicalStepId": step_id,
            "executionOrdinal": 1,
            "stepExecutionId": f"{workflow_id}:{reset_run}:{step_id}:execution:1",
            "runtimeContextPolicy": "external_provider_continuation",
        },
    )
    binding = "omnigent-runtime-binding:sha256:" + "a" * 64
    result = AgentRunResult(
        summary="Confirmed assessment result",
        executionState="completed",
        objectiveOutcome="succeeded",
        metadata={
            "parentWorkflowId": workflow_id,
            "parentRunId": source_run,
            "parentTemporalRunId": source_run,
            "childWorkflowId": child_id,
            "childRunId": "recorded-child-run",
            "runtimeBindingRef": binding,
            "runtimeSessionContext": {
                "workflowId": workflow_id,
                "runId": source_run,
                "stepExecutionId": f"{workflow_id}:{source_run}:{step_id}:execution:1",
                "epoch": "recorded-epoch",
                "runtimeBindingRef": binding,
            },
        },
    )
    return owner, request, result, child_id, patches


@pytest.mark.parametrize("source_run", ["original-run", "prior-reset-run"])
def test_completed_context_replays_before_the_recorded_typed_outcome_marker(
    monkeypatch, source_run
):
    owner, request, result, child_id, patches = _boundary(monkeypatch, source_run)
    with pytest.raises(ValueError, match="runtime context conflicts"):
        owner._record_shared_runtime_context(request, result)
    assert "run-typed-agent-outcomes-v1" not in patches

    restored = owner._restore_replayed_agent_step_identity(
        request, result, child_workflow_id=child_id
    )
    owner._record_shared_runtime_context(restored, result)
    assert owner._map_agent_run_result(result)["status"] == "COMPLETED"
    assert "run-typed-agent-outcomes-v1" in patches
    assert restored.step_execution.run_id == source_run
    assert restored.step_execution.step_execution_id == f"parent:{source_run}:assess:execution:1"
    assert owner._shared_runtime_context.run_id == source_run
    assert request.step_execution.run_id == "reset-run"


@pytest.mark.parametrize("fault", [
    "live", "no-context", "parent-workflow", "parent-run", "parent-alias",
    "missing-parent-ancestry", "child-workflow", "missing-child-run",
    "blank-child-run", "invalid-child-run",
    "context-workflow", "context-run", "context-step", "context-ordinal",
    "context-binding", "request-workflow", "request-run",
])
def test_replay_identity_requires_the_exact_recorded_child_and_context(
    monkeypatch, fault
):
    owner, request, result, child_id, _ = _boundary(monkeypatch)
    metadata = result.metadata
    context = metadata["runtimeSessionContext"]
    if fault == "live":
        monkeypatch.setattr(owner, "_workflow_is_replaying", lambda: False)
    elif fault == "no-context":
        metadata.pop("runtimeSessionContext")
    elif fault == "parent-workflow":
        metadata["parentWorkflowId"] = "foreign-workflow"
    elif fault == "parent-run":
        metadata.pop("parentRunId")
    elif fault == "parent-alias":
        metadata["parentTemporalRunId"] = "different-parent-run"
    elif fault == "missing-parent-ancestry":
        metadata.pop("parentTemporalRunId")
    elif fault == "child-workflow":
        metadata["childWorkflowId"] = "parent:agent:another-step"
    elif fault == "missing-child-run":
        metadata.pop("childRunId")
    elif fault == "blank-child-run":
        metadata["childRunId"] = " "
    elif fault == "invalid-child-run":
        metadata["childRunId"] = True
    elif fault == "context-workflow":
        context["workflowId"] = "foreign-workflow"
    elif fault == "context-run":
        context["runId"] = "foreign-run"
    elif fault == "context-step":
        context["stepExecutionId"] = "parent:original-run:another-step:execution:1"
    elif fault == "context-ordinal":
        context["stepExecutionId"] = "parent:original-run:assess:execution:2"
    elif fault == "context-binding":
        context["runtimeBindingRef"] = "omnigent-runtime-binding:sha256:" + "b" * 64
    elif fault == "request-workflow":
        request.step_execution.workflow_id = "foreign-workflow"
    elif fault == "request-run":
        request.step_execution.run_id = "another-run"
    original = deepcopy(result.model_dump())
    assert owner._restore_replayed_agent_step_identity(
        request, result, child_workflow_id=child_id
    ) is request
    assert result.model_dump() == original


def test_new_work_uses_the_reset_run_identity(monkeypatch):
    owner, request, result, child_id, _ = _boundary(monkeypatch, "reset-run")
    monkeypatch.setattr(owner, "_workflow_is_replaying", lambda: False)
    assert owner._restore_replayed_agent_step_identity(
        request, result, child_workflow_id=child_id
    ) is request
    owner._record_shared_runtime_context(request, result)
    assert owner._shared_runtime_context.run_id == "reset-run"
