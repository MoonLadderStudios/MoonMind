"""Retained root-plan requests replay while new steps use admitted step plans."""

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_OMNIGENT_REMEDIATION_STEP_PLANS_PATCH,
    RUN_OMNIGENT_STEP_EXECUTION_PLANS_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_omnigent_plan_authority import _binding
from tests.unit.workflows.temporal.test_remediation_loop import _spec


class _BeforeStepPlans(MoonMindRunWorkflow):
    @staticmethod
    def _workflow_patch_enabled(patch_id):
        if patch_id == RUN_OMNIGENT_STEP_EXECUTION_PLANS_PATCH:
            return False
        return MoonMindRunWorkflow._workflow_patch_enabled(patch_id)


async def _request(implementation, *, forged=False):
    runtime = {"mode": "omnigent", "model": "step-model", "effort": "high"}
    if forged:
        runtime["omnigentExecutionPlan"] = _binding("3")
    request = implementation._build_agent_execution_request(
        node_inputs={"runtime": runtime}, node_id="verify", tool_name="auto",
        workflow_parameters={
            "omnigentExecutionPlan": _binding("1"),
            "workflow": {"runtime": {"model": "root-model", "effort": "medium"}},
            "omnigentStepExecutionPlans": {
                "verify": {
                    "omnigentExecutionPlan": _binding("2"),
                    "resolvedSkillsetRef": "art_step_skills",
                    "model": "step-model", "effort": "high",
                },
            },
        },
    )
    await workflow.sleep(1)
    return request.omnigent_execution_plan.plan_ref


@workflow.defn(name="LogicalStepPlanReplayFixture")
class _LegacyFixture:
    @workflow.run
    async def run(self):
        return await _request(_BeforeStepPlans())


@workflow.defn(name="LogicalStepPlanReplayFixture")
class _CurrentFixture:
    @workflow.run
    async def run(self):
        return await _request(MoonMindRunWorkflow())


@pytest.mark.asyncio
async def test_retained_root_plan_and_new_step_plan_histories_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for label, fixture, expected in (
            ("retained", _LegacyFixture, _binding("1")["planRef"]),
            ("current", _CurrentFixture, _binding("2")["planRef"]),
        ):
            queue = f"test-logical-step-plan-{label}"
            async with Worker(env.client, task_queue=queue, workflows=[fixture], workflow_runner=UnsandboxedWorkflowRunner()):
                handle = await env.client.start_workflow(fixture.run, id=queue, task_queue=queue)
                assert await handle.result() == expected
                histories.append(await handle.fetch_history())
    replayer = Replayer(workflows=[_CurrentFixture], workflow_runner=UnsandboxedWorkflowRunner())
    for history in histories:
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_authored_plan_cannot_replace_the_admitted_step_plan(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(namespace="default", workflow_id="mm:step-plans", run_id="run-1", parent=None))
    monkeypatch.setattr(workflow, "patched", lambda _patch: True)
    with pytest.raises(ValueError, match="omnigentExecutionPlan.*admitted"):
        await _request(MoonMindRunWorkflow(), forged=True)


def _loop_requests(implementation, *, independent_profile=False):
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopPhase,
        materialize_evidence_retry_node,
    )

    runtime = {"mode": "omnigent", "model": "step-model", "effort": "high"}
    if independent_profile:
        runtime["providerProfileRef"] = "step-account"
    implementation._initialize_remediation_loop_controller(ordered_nodes=[{
        "id": "controller", "inputs": {"runtime": runtime},
        "annotations": {"remediationLoop": _spec().model_dump(by_alias=True, mode="json")},
    }])
    implementation._remediation_loop_state = implementation._remediation_loop_state.model_copy(update={
        "attempt_ordinal": 1, "phase": RemediationLoopPhase.VERIFICATION_PENDING,
        "workspace_head_ref": "artifact://workspace/candidate",
        "consumed_budgets": ConsumedRemediationBudgets(attempts=1, evidenceRetries=1),
    })
    nodes = list(implementation._materialize_remediation_attempt(ordinal=1))
    nodes.append(materialize_evidence_retry_node(
        source_node=nodes[1], state=implementation._remediation_loop_state,
        gate_result_ref="artifact://verification/result",
    ))
    parameters = {
        "profileId": "root-account", "omnigentExecutionPlan": _binding("1"),
        "workflow": {"runtime": {"model": "root-model", "effort": "medium"}},
        "omnigentStepExecutionPlans": {"controller": {
            "profileId": "step-account" if independent_profile else "root-account",
            "omnigentExecutionPlan": _binding("2"), "resolvedSkillsetRef": "art_step_skills",
            "model": "step-model", "effort": "high",
        }},
    }
    requests = [implementation._build_agent_execution_request(
        node_inputs=node["inputs"], node_annotations=node["annotations"],
        node_id=node["id"], tool_name=node["tool"]["name"], workflow_parameters=parameters,
    ) for node in nodes]
    assert set(parameters["omnigentStepExecutionPlans"]) == {"controller"}
    assert parameters["omnigentExecutionPlan"] == _binding("1")
    return requests


@pytest.mark.parametrize("independent_profile", [False, True])
def test_materialized_loop_and_evidence_retry_use_controller_plan(monkeypatch, independent_profile):
    from types import SimpleNamespace

    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(namespace="default", workflow_id="mm:loop-step-plans", run_id="run-1", parent=None))
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    for request in _loop_requests(MoonMindRunWorkflow(), independent_profile=independent_profile):
        assert request.omnigent_execution_plan.plan_ref == _binding("2")["planRef"]
        assert request.step_execution.omnigent_execution_plan == request.omnigent_execution_plan
        assert request.resolved_skillset_ref == "art_step_skills"
        assert request.parameters["model"] == "step-model"
        assert request.parameters["effort"] == "high"
        assert request.execution_profile_ref == ("step-account" if independent_profile else "root-account")


@pytest.mark.parametrize("node_id", ["unrelated", "issue-implementation-remediation:remediation:99"])
def test_authored_loop_labels_cannot_borrow_controller_plan(monkeypatch, node_id):
    from types import SimpleNamespace

    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(namespace="default", workflow_id="mm:loop-step-plans", run_id="run-1", parent=None))
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    implementation = MoonMindRunWorkflow()
    _loop_requests(implementation)
    request = implementation._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}, "remediationLoopId": _spec().loop_id},
        node_annotations={"remediationLoopId": _spec().loop_id}, node_id=node_id, tool_name="auto",
        workflow_parameters={"omnigentExecutionPlan": _binding("1"), "omnigentStepExecutionPlans": {
            "controller": {"omnigentExecutionPlan": _binding("2"), "resolvedSkillsetRef": "art_step_skills"},
        }},
    )
    assert request.omnigent_execution_plan.plan_ref == _binding("1")["planRef"]


class _BeforeLoopStepPlans(MoonMindRunWorkflow):
    @staticmethod
    def _workflow_patch_enabled(patch_id):
        if patch_id == RUN_OMNIGENT_REMEDIATION_STEP_PLANS_PATCH:
            return False
        return MoonMindRunWorkflow._workflow_patch_enabled(patch_id)


async def _loop_plan_refs(implementation):
    refs = [request.omnigent_execution_plan.plan_ref for request in _loop_requests(implementation)]
    await workflow.sleep(1)
    return refs


@workflow.defn(name="LoopStepPlanReplayFixture")
class _LegacyLoopFixture:
    @workflow.run
    async def run(self):
        return await _loop_plan_refs(_BeforeLoopStepPlans())


@workflow.defn(name="LoopStepPlanReplayFixture")
class _CurrentLoopFixture:
    @workflow.run
    async def run(self):
        return await _loop_plan_refs(MoonMindRunWorkflow())


@pytest.mark.asyncio
async def test_retained_loop_requests_and_controller_plan_histories_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for label, fixture, expected in (
            ("retained", _LegacyLoopFixture, _binding("1")["planRef"]),
            ("current", _CurrentLoopFixture, _binding("2")["planRef"]),
        ):
            queue = f"test-loop-step-plan-{label}"
            async with Worker(env.client, task_queue=queue, workflows=[fixture], workflow_runner=UnsandboxedWorkflowRunner()):
                handle = await env.client.start_workflow(fixture.run, id=queue, task_queue=queue)
                assert await handle.result() == [expected] * 3
                histories.append(await handle.fetch_history())
    replayer = Replayer(workflows=[_CurrentLoopFixture], workflow_runner=UnsandboxedWorkflowRunner())
    for history in histories:
        await replayer.replay_workflow(history)
