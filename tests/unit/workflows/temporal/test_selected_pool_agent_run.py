"""Selected native pool steps enter the existing shared AgentRun loop."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from moonmind.omnigent.workspace_sources import compile_workspace_source
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import agent_run as module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    PLAN_REF,
    _admission,
    _configure_workflow_runtime,
    _plan_binding,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("cooldown", [False, True])
@pytest.mark.parametrize("account_selection", ["pool", "fixed", "implicit"])
@pytest.mark.parametrize("parent_step_recovery", [False, True], ids=["retained-parent", "step-owner"])
@pytest.mark.parametrize("explicit_scratch", [False, True])
async def test_pool_step_uses_shared_loop_and_preserves_retry_authority(
    monkeypatch, runtime, cooldown, account_selection, parent_step_recovery, explicit_scratch
):
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    signals, prepared, executions = [], [], []
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId=runtime,
        profileSelector={"tagsAll": ["selected-pool"]} if account_selection == "pool" else {},
        executionProfileRef="selected-account" if account_selection == "fixed" else None,
        correlationId="parent",
        idempotencyKey="accepted-step",
        instructionRef="Preserve the accepted task.",
        parameters={"publishMode": "none"},
        workspaceSpec=(
            {"workspaceSource": {"kind": "scratch"}} if explicit_scratch else {}
        ),
        stepExecution={
            "workflowId": "parent",
            "runId": "parent-run",
            "logicalStepId": "work",
            "executionOrdinal": 1,
            "stepExecutionId": "parent:parent-run:work:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )

    async def signal(name, payload):
        signals.append((name, deepcopy(payload)))
        if name == "prepare_shared_execution_plan":
            selected = deepcopy(payload["request"])
            prepared.append(selected)
            source = compile_workspace_source(selected["workspaceSpec"])
            if cooldown and runtime == "codex_cli" and len(prepared) > 1:
                assert source.kind == "checkpoint"
            selected.update(
                agentKind="external",
                agentId="omnigent",
                omnigentExecutionPlan=_plan_binding(PLAN_REF),
            )
            selected["stepExecution"]["omnigentExecutionPlan"] = _plan_binding(PLAN_REF)
            run.pool_plan_prepared(
                {
                    "disposition": "shared",
                    "request": selected,
                    "requestId": selected["idempotencyKey"],
                    "fencingGeneration": payload["fencing_generation"],
                }
            )

    manager = SimpleNamespace(signal=signal)

    async def ensure(_manager_id, _runtime, *, request_slot=True, **kwargs):
        if request_slot:
            signals.append(("request_slot", deepcopy(kwargs)))
            run.slot_assigned(
                {"profile_id": "selected-account", "fencing_generation": 7}
            )
        return manager

    async def sync(**kwargs):
        return 1

    async def activity(name, payload=None, **kwargs):
        if name == "integration.resolve_adapter_metadata":
            return {"agent_id": "omnigent", "execution_style": "streaming_gateway"}
        if name == "omnigent.evaluate_session_admission":
            return (
                _admission(
                    profile_ref="selected-account",
                    runtime_id=runtime,
                    capacity_scope_ref="selected-scope",
                    host_class_ref="omnigent-native-shared@1",
                )
                .model_copy(
                    update={
                        "admitted": False,
                        "reason_code": "realizer_managed_lifecycle",
                    }
                )
                .model_dump(mode="json", by_alias=True)
            )
        if name == "omnigent.admit_generic_host_capacity":
            return {"admitted": True}
        if name.startswith("integration.omnigent."):
            executions.append(payload.model_copy(deep=True))
            result = {
                "summary": "Shared success",
                "metadata": {"admittedProviderCapacityCleanupCompleted": True},
            }
            if runtime == "codex_cli":
                step = payload.step_execution
                result["metadata"].update(
                    runtimeSessionContext={
                        "workflowId": step.workflow_id,
                        "runId": step.run_id,
                        "stepExecutionId": step.step_execution_id,
                        "epoch": step.step_execution_id,
                        "runtimeBindingRef": "omnigent-runtime-binding:sha256:"
                        + "a" * 64,
                    },
                    savedWorkspaceCheckpoint={
                        "archiveRef": "artifact://accepted-workspace"
                    },
                )
            if cooldown and len(executions) == 1:
                result.update(
                    summary="Provider temporarily busy",
                    failureClass="integration_error",
                    providerErrorCode="provider_capacity",
                    retryRecommendation="retry_after_cooldown",
                )
            return result
        if name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected Activity: {name}")

    run._ensure_manager_and_signal = ensure
    run._sync_manager_profiles = sync
    run._execute_routed_activity = activity
    monkeypatch.setattr(
        module.workflow, "get_external_workflow_handle", lambda *args, **kwargs: manager
    )

    async def wait_condition(predicate, **kwargs):
        assert predicate(), "the transport must deliver the fenced response"

    monkeypatch.setattr(module.workflow, "wait_condition", wait_condition)
    from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

    request = MoonMindUserWorkflow()._bind_shared_runtime_context(request)
    if not parent_step_recovery:
        request.parameters["metadata"]["moonmind"].pop("sharedStepRecovery")
    result = await run.run(request)

    if cooldown and parent_step_recovery:
        assert result.summary == "Provider temporarily busy"
        assert result.failure_class == "integration_error"
        assert result.provider_error_code == "provider_capacity"
        assert result.retry_recommendation == "retry_step_execution"
    else:
        assert result.summary == "Shared success" and result.failure_class is None
    if runtime == "codex_cli":
        assert (
            result.metadata["dispatchPoolContext"]["executionProfileRef"]
            == "selected-account"
        )
        assert (
            result.metadata["dispatchPoolContext"]["workspaceCheckpointRestoreRef"]
            == "artifact://accepted-workspace"
        )
        if cooldown and not parent_step_recovery:
            assert (
                executions[1].workspace_spec["workspaceCheckpointRestoreRef"]
                == "artifact://accepted-workspace"
            )
    assert len(executions) == len(prepared) == (2 if cooldown and not parent_step_recovery else 1)
    assert all(
        item.agent_kind == "external" and item.agent_id == "omnigent"
        for item in executions
    )
    assert all(item.execution_profile_ref == "selected-account" for item in executions)
    assert all(
        item.admitted_provider_capacity.lease_owner_id == "agent-run-1"
        for item in executions
    )
    assert all(
        item.profile_selector.tags_all == (["selected-pool"] if account_selection == "pool" else [])
        for item in executions
    )
    assert all(
        item.instruction_ref == request.instruction_ref
        and item.idempotency_key == request.idempotency_key
        for item in executions
    )
    assert len({item.execution_budget.admitted_at for item in executions}) == 1
    assert all(item.execution_budget.logical_step_id == "work" for item in executions)
    assert run._omnigent_capacity_state == "released"
    assert len([name for name, _ in signals if name == "report_cooldown"]) == int(
        cooldown
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("has_shared_context", [False, True])
async def test_untranslated_settings_cannot_drop_an_admitted_shared_context(
    monkeypatch, has_shared_context,
):
    from temporalio.exceptions import ApplicationError

    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    run._assigned_slot_fencing_generation = 7
    context = {
        "workflowId": "parent", "runId": "parent-run",
        "stepExecutionId": "parent:parent-run:previous:execution:1",
        "epoch": "previous", "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "a" * 64,
    }
    request = AgentExecutionRequest(
        agentKind="managed", agentId="codex_cli", executionProfileRef="selected-account",
        correlationId="parent", idempotencyKey="next-step",
        stepExecution={
            "workflowId": "parent", "runId": "parent-run", "logicalStepId": "next",
            "executionOrdinal": 1, "stepExecutionId": "parent:parent-run:next:execution:1",
            "runtimeContextPolicy": "reuse_session_same_epoch" if has_shared_context else "fresh_agent_run",
            **({"runtimeSessionContext": context} if has_shared_context else {}),
        },
    )

    async def signal(_name, payload):
        run.pool_plan_prepared({
            "requestId": payload["request"]["idempotencyKey"],
            "fencingGeneration": payload["fencing_generation"],
            "disposition": "native", "reason": "untranslated_profile_options",
        })

    async def wait_condition(predicate, **kwargs):
        assert predicate()

    monkeypatch.setattr(module.workflow, "wait_condition", wait_condition)
    call = run._prepare_selected_pool_shared_request(
        request=request, runtime_id="codex_cli", manager_handle=SimpleNamespace(signal=signal),
        original_profile_ref="selected-account",
    )
    if has_shared_context:
        with pytest.raises(ApplicationError, match="shared context"):
            await call
        assert request.step_execution.runtime_session_context.runtime_binding_ref == context["runtimeBindingRef"]
    else:
        assert await call is request
