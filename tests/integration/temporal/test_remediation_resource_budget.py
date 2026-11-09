"""Runtime usage, rollover and retained-history replay for MoonMind#5177."""

import hashlib
import json
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.temporal.remediation_loop import RemediationLoopPhase
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.temporal.test_remediation_loop import _spec, _state

_PATCH = "run-remediation-aggregate-resource-budget-v1"
_REAL_PATCHED = workflow.patched

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@activity.defn(name="resource-budget.record")
async def record(payload: dict[str, Any]) -> str:
    # Recording artifact transport; the production controller owns every payload.
    return (
        "artifact://"
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )


@workflow.defn(name="ResourceBudgetRuntime")
class MeteredRuntime:
    @workflow.run
    async def run(self, tokens: int) -> dict[str, Any]:
        await workflow.sleep(timedelta(seconds=1))
        return AgentRunResult(
            metadata={"usage": {"totalTokens": tokens, "costUsd": 0.25}}
        ).model_dump(mode="json")


@workflow.defn(name="RemediationResourceBudgetJourney")
class ResourceBudgetJourney:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = run_module.MoonMindRunWorkflow()
        run._original_input_payload = dict(payload)
        run._remediation_loop_runtime = {"mode": "codex_cli"}
        run._remediation_loop_controller_node_id = "verify"
        spec = _spec(6)
        run._remediation_loop_spec = spec.model_copy(
            update={"budgets": spec.budgets.model_copy(update={"token_budget": 100})}
        )
        run._remediation_loop_state = _state(attempts=1).model_copy(
            update={"phase": RemediationLoopPhase.VERIFICATION_RUNNING}
        )

        async def persist(**kwargs):
            return await workflow.execute_activity(
                "resource-budget.record",
                kwargs,
                start_to_close_timeout=timedelta(seconds=10),
            )

        run._write_json_artifact = persist
        stage = payload.get("stage", 0)
        nodes = [
            {"id": "verify", "inputs": {"instructions": "Verify the saved candidate"}}
        ]
        if stage:
            run._remediation_loop_continuation = payload[
                "remediation_loop_continuation"
            ]
            run._restore_remediation_loop_continuation(ordered_nodes=nodes)
            step = next(
                node["id"] for node in nodes if "verification-evidence" in node["id"]
            )
        else:
            step = "verify"
            run._step_ledger_rows = [
                {"logicalStepId": step, "attempt": 1, "status": "executing"}
            ]
            run._rebuild_step_ledger_index()
        assert await run._admit_remediation_resource_execution(step)
        result = await workflow.execute_child_workflow(
            MeteredRuntime.run,
            40 if stage == 0 else 60,
            id=f"{workflow.info().workflow_id}:runtime:{stage}",
        )
        run._record_step_result_evidence(
            step,
            execution_result=run._map_legacy_agent_run_result(result),
            updated_at=workflow.now(),
        )
        run._remediation_loop_state = run._remediation_loop_state.model_copy(
            update={"phase": RemediationLoopPhase.VERIFICATION_RUNNING}
        )
        admitted = await run._evaluate_dynamic_remediation_verification(
            ordered_nodes=nodes,
            logical_step_id=step,
            verdict="NO_DETERMINATION" if stage == 0 else "ADDITIONAL_WORK_NEEDED",
            gate_result_ref="artifact://verification/current",
            remaining_work_ref=payload["originalEvidenceRef"],
            recoverable_evidence=True,
        )
        if stage == 0:
            assert admitted
            run._original_input_payload["stage"] = 1
            run._continue_remediation_loop_as_new(ordered_nodes=nodes)
        return {
            "admitted": admitted,
            "state": run._remediation_loop_state.model_dump(by_alias=True, mode="json"),
            "originalEvidenceRef": payload["originalEvidenceRef"],
        }


@pytest.mark.asyncio
async def test_resource_receipts_cross_real_rollover_and_old_and_new_histories_replay(
    monkeypatch,
):
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for enabled in (False, True):
            selected = {"run-materialize-evidence-retry-v1"}
            if enabled:
                selected.add(_PATCH)
            monkeypatch.setattr(
                run_module.workflow,
                "patched",
                lambda patch: patch in selected and _REAL_PATCHED(patch),
            )
            name = f"resource-budget-{enabled}"
            async with Worker(
                env.client,
                task_queue=name,
                workflows=[ResourceBudgetJourney, MeteredRuntime],
                activities=[record],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    ResourceBudgetJourney.run,
                    {"originalEvidenceRef": "artifact://finding/original"},
                    id=name,
                    task_queue=name,
                )
                result = await handle.result()
                assert result["admitted"] is (not enabled)
                assert result["state"]["workspaceHeadRef"] == "artifact://workspace/C1"
                assert result["state"]["continueAsNewCount"] == 1
                assert result["originalEvidenceRef"] == "artifact://finding/original"
                if enabled:
                    assert (
                        result["state"]["continuationReason"]
                        == "token_budget_exhausted"
                    )
                    assert (
                        sum(
                            receipt["tokens"]
                            for receipt in result["state"]["resourceUsage"][
                                "executions"
                            ].values()
                        )
                        == 100
                    )
                    assert result["state"]["consumedBudgets"]["attempts"] == 1
                histories.append(
                    await env.client.get_workflow_handle(
                        name, run_id=handle.first_execution_run_id
                    ).fetch_history()
                )
                histories.append(
                    await env.client.get_workflow_handle(name).fetch_history()
                )
        replayer = Replayer(
            workflows=[ResourceBudgetJourney],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        for history in histories:
            await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_interrupted_runtime_exhaustion_prevents_successor_admission_and_replays(
    monkeypatch,
):
    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    from tests.unit.workflows.temporal import (
        test_run_interrupted_step_replay as interrupted,
    )

    interrupted._patch_environment(monkeypatch)
    original_plan = interrupted._plan

    async def plan(*args, **kwargs):
        payload = json.loads(await original_plan(*args, **kwargs))
        spec = _spec(6)
        spec = spec.model_copy(
            update={"budgets": spec.budgets.model_copy(update={"provider_budget": 1})}
        )
        payload["nodes"][0]["inputs"]["annotations"] = {
            "remediationLoop": spec.model_dump(by_alias=True, mode="json")
        }
        return json.dumps(payload).encode()

    async def persist(_self, **kwargs):
        return await workflow.execute_activity(
            "resource-budget.record",
            kwargs,
            start_to_close_timeout=timedelta(seconds=10),
        )

    monkeypatch.setattr(run_module, "execute_typed_activity", plan)
    monkeypatch.setattr(run_module.MoonMindRunWorkflow, "_write_json_artifact", persist)
    patches = interrupted._CURRENT_PATCHES | {
        _PATCH,
        run_module.RUN_DYNAMIC_REMEDIATION_LOOP_CONTROLLER_PATCH,
    }
    interrupted._enable(monkeypatch, patches)
    interrupted._ADMISSION_RESULT.clear()
    interrupted._ADMISSION_RESULT.update(
        disposition="admitted",
        omnigentExecutionPlan=dict(interrupted._INSTALLED),
        resolvedSkillsetRef="art_skills_installed",
    )
    interrupted._ADMISSIONS.clear()
    interrupted._CHILD_REQUESTS.clear()
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        agent_worker, admission_worker = interrupted._workers(env)
        async with agent_worker, admission_worker, Worker(
            env.client,
            task_queue="exhausted-interruption",
            workflows=[interrupted._InterruptedStepFixture],
            activities=[record],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                interrupted._InterruptedStepFixture.run,
                id="exhausted-interruption",
                task_queue="exhausted-interruption",
            )
            assert await handle.result() == 1
            assert len(interrupted._CHILD_REQUESTS) == 1
            assert not interrupted._ADMISSIONS
            history = await handle.fetch_history()
        await Replayer(
            workflows=[interrupted._InterruptedStepFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history)
