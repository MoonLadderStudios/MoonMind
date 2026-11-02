"""Dispatch-time shared conversion preserves context at the parent boundary."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import run as module


def request(step="first", policy="fresh_agent_run"):
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        profileSelector={"tagsAll": ["accepted-pool"]},
        correlationId="parent",
        idempotencyKey=step,
        instructionRef="Preserve accepted work.",
        stepExecution={
            "workflowId": "parent",
            "runId": "parent-run",
            "logicalStepId": step,
            "executionOrdinal": 1,
            "stepExecutionId": f"parent:parent-run:{step}:execution:1",
            "runtimeContextPolicy": policy,
        },
    )


def result():
    return {
        "metadata": {
            "runtimeSessionContext": {
                "workflowId": "parent",
                "runId": "parent-run",
                "stepExecutionId": "parent:parent-run:first:execution:1",
                "epoch": "accepted-epoch",
                "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "a" * 64,
            },
            "dispatchPoolContext": {
                "executionProfileRef": "selected-account",
                "workspaceCheckpointRestoreRef": "artifact:verified-workspace",
            },
        }
    }


def test_parent_carries_selected_pool_account_context_and_workspace(monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    owner = module.MoonMindUserWorkflow()
    owner._record_shared_runtime_context(request(), result())
    next_request = request("second", "reuse_session_same_epoch")
    bound = owner._bind_shared_runtime_context(next_request)
    assert bound.step_execution.runtime_session_context is not None
    assert bound.step_execution.runtime_session_context.epoch == "accepted-epoch"
    assert bound.execution_profile_ref == "selected-account"
    assert bound.profile_selector == next_request.profile_selector
    assert (
        bound.workspace_spec["workspaceCheckpointRestoreRef"]
        == "artifact:verified-workspace"
    )
    assert (
        next_request.execution_profile_ref is None and next_request.workspace_spec == {}
    )
    fresh = owner._bind_shared_runtime_context(request("third"))
    assert fresh.execution_profile_ref is None
    assert fresh.step_execution.runtime_session_context is None


def test_ordinary_native_results_cannot_invent_a_shared_context(monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    owner = module.MoonMindUserWorkflow()
    unconverted = deepcopy(result())
    unconverted["metadata"].pop("dispatchPoolContext")
    owner._record_shared_runtime_context(request(), unconverted)
    assert (
        owner._bind_shared_runtime_context(
            request("second", "reuse_session_same_epoch")
        ).step_execution.runtime_session_context
        is None
    )


def test_pre_transition_pool_histories_keep_native_context_decisions(monkeypatch):
    monkeypatch.setattr(
        module.workflow,
        "patched",
        lambda marker: marker != "run-dispatch-pool-shared-context-v1",
    )
    owner = module.MoonMindUserWorkflow()
    owner._record_shared_runtime_context(request(), result())
    assert (
        owner._bind_shared_runtime_context(
            request("second", "reuse_session_same_epoch")
        ).step_execution.runtime_session_context
        is None
    )


def test_published_history_compaction_keeps_the_context_handoff():
    from moonmind.workflows.temporal.agent_result_payloads import (
        compact_published_agent_run_result_payload,
    )

    payload = result()
    payload["metadata"]["savedWorkspaceCheckpoint"] = {
        "archiveRef": "artifact:verified-workspace"
    }
    payload["metadata"].update(
        {f"report{index}Ref": "artifact:" + "x" * 1000 for index in range(100)}
    )
    payload["metadata"]["outputAgentResultRef"] = "artifact:full-result"
    compact = compact_published_agent_run_result_payload(payload)
    for key in (
        "runtimeSessionContext",
        "dispatchPoolContext",
        "savedWorkspaceCheckpoint",
    ):
        assert compact["metadata"][key] == payload["metadata"][key]


def test_older_converted_result_cannot_silently_become_a_fresh_native_context(
    monkeypatch,
):
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    older = result()
    older["metadata"].pop("dispatchPoolContext")
    older["metadata"]["runtimeBindingRef"] = older["metadata"]["runtimeSessionContext"][
        "runtimeBindingRef"
    ]
    owner = module.MoonMindUserWorkflow()
    owner._record_shared_runtime_context(request(), older)
    with pytest.raises(ValueError, match="selected-account handoff"):
        owner._bind_shared_runtime_context(
            request("second", "reuse_session_same_epoch")
        )


def test_queued_old_parent_pool_requests_keep_native_dispatch(monkeypatch):
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    child = MoonMindAgentRun()
    child._assigned_slot_fencing_generation = 7
    old_request = request()
    assert not child._uses_selected_pool_shared_admission(old_request, "codex_cli")
    current_request = module.MoonMindUserWorkflow()._bind_shared_runtime_context(
        old_request
    )
    assert child._uses_selected_pool_shared_admission(current_request, "codex_cli")


def test_pool_context_rejects_a_conflicting_selected_account(monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    owner = module.MoonMindUserWorkflow()
    selected = request().model_copy(
        update={"execution_profile_ref": "authored-account"}
    )
    with pytest.raises(ValueError, match="selected account"):
        owner._record_shared_runtime_context(selected, result())
    owner._record_shared_runtime_context(request(), result())
    conflicting = request("second", "reuse_session_same_epoch").model_copy(
        update={"execution_profile_ref": "another-account"}
    )
    with pytest.raises(ValueError, match="selected account"):
        owner._bind_shared_runtime_context(conflicting)


def test_sessionless_pool_result_keeps_the_last_verified_context(monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    owner = module.MoonMindUserWorkflow()
    owner._record_shared_runtime_context(request(), result())
    owner._record_shared_runtime_context(
        request("second", "reuse_session_same_epoch"),
        {"metadata": {"runtimeSessionContext": None}},
    )
    following = owner._bind_shared_runtime_context(
        request("third", "reuse_session_same_epoch")
    )
    assert following.execution_profile_ref == "selected-account"
    assert following.step_execution.runtime_session_context.epoch == "accepted-epoch"
    assert (
        following.workspace_spec["workspaceCheckpointRestoreRef"]
        == "artifact:verified-workspace"
    )


def test_pool_context_and_selected_workspace_survive_continue_as_new(monkeypatch):
    from moonmind.workflows.temporal.remediation_loop import (
        RemediationLoopSpec,
        RemediationLoopState,
    )
    from tests.unit.workflows.temporal.workflows.test_run_integration import (
        _dynamic_loop_spec_payload,
    )

    monkeypatch.setattr(module.workflow, "patched", lambda marker: True)
    monkeypatch.setattr(
        module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="parent", run_id="next-parent-run"),
    )
    monkeypatch.setattr(module.workflow, "now", lambda: datetime.now(timezone.utc))
    owner = module.MoonMindUserWorkflow()
    owner._record_shared_runtime_context(request(), result())
    owner._original_input_payload = {}
    owner._remediation_loop_spec = RemediationLoopSpec.model_validate(
        _dynamic_loop_spec_payload()
    )
    owner._remediation_loop_state = RemediationLoopState(
        loopId=owner._remediation_loop_spec.loop_id,
        attemptOrdinal=1,
        phase="verification_pending",
        consumedBudgets={"attempts": 1},
    )
    carried = owner._build_remediation_loop_continue_as_new_input(ordered_nodes=[])
    continued = module.MoonMindUserWorkflow()
    continued._remediation_loop_spec = owner._remediation_loop_spec
    continued._remediation_loop_continuation = carried["remediation_loop_continuation"]
    monkeypatch.setattr(continued, "_sync_progress_snapshot", lambda **kwargs: None)
    continued._restore_remediation_loop_continuation(ordered_nodes=[])
    successor = request("second", "reuse_session_same_epoch")
    successor.step_execution.run_id = "next-parent-run"
    successor = continued._bind_shared_runtime_context(successor)
    assert successor.execution_profile_ref == "selected-account"
    assert (
        successor.workspace_spec["workspaceCheckpointRestoreRef"]
        == "artifact:verified-workspace"
    )
    assert successor.step_execution.runtime_session_context.epoch == "accepted-epoch"
    assert successor.step_execution.runtime_session_context.run_id == "parent-run"
