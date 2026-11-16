"""Replay server-produced planning histories across assessment gate introduction."""

import ast
import inspect
import os
import textwrap

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows import run as implementation


# Reconstruct the immediately preceding planning stage by removing only the new
# guarded block. Every other planning-stage statement remains production code.
# In particular, legacy execution never calls patched() for the new marker; the
# real server therefore records a retained history with no such marker/Activity.
def _pre_gate_planning_stage():
    source = textwrap.dedent(
        inspect.getsource(implementation.MoonMindRunWorkflow._run_planning_stage)
    )
    tree = ast.parse(source)
    method = tree.body[0]
    removed = [
        node
        for node in method.body
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Name)
            and child.id == "RUN_ASSESSMENT_RUNTIME_READINESS_PATCH"
            for child in ast.walk(node.test)
        )
    ]
    assert len(removed) == 1
    method.body.remove(removed[0])
    namespace = dict(vars(implementation))
    exec(  # noqa: S102 -- compile only the inspected production method for legacy history
        compile(
            ast.fix_missing_locations(tree),
            "<pre-assessment-gate-planning-stage>",
            "exec",
        ),
        namespace,
    )
    return namespace["_run_planning_stage"]


class _PreGateRun(implementation.MoonMindRunWorkflow):
    _run_planning_stage = _pre_gate_planning_stage()


async def _planning(run_type, continue_once, resumed):
    instance = run_type()
    instance._owner_type = "user"
    instance._owner_id = "replay-owner"
    plan = await instance._run_planning_stage(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "annotations": {
                            "securityAssessment": {"kind": "repository-scan"}
                        },
                        "skill": {
                            "id": "security-review",
                            "args": {"assessment": {"scanner": "gitleaks"}},
                        },
                    }
                ]
            }
        },
        input_ref=None,
        plan_ref="art_retained_plan",
    )
    if continue_once and not resumed:
        workflow.continue_as_new(args=[continue_once, True])
    # A server command after the stage makes replay command ordering observable.
    await workflow.sleep(1)
    return plan


@workflow.defn(name="AssessmentReadinessPlanningReplay")
class _LegacyPlanning:
    @workflow.run
    async def run(self, continue_once: bool, resumed: bool):
        return await _planning(_PreGateRun, continue_once, resumed)


@workflow.defn(name="AssessmentReadinessPlanningReplay")
class _CurrentPlanning:
    @workflow.run
    async def run(self, continue_once: bool, resumed: bool):
        return await _planning(
            implementation.MoonMindRunWorkflow, continue_once, resumed
        )


@activity.defn(name="container_job.assessment_readiness")
async def _ready_probe(request):
    assert request["owner"] == {"principalId": "replay-owner", "principalType": "user"}
    assert list(request["parameters"]) == ["workflow"]
    return {
        "status": "ready",
        "diagnostics": [],
        "checks": [
            {"component": "scanner", "status": "ready", "code": "scanner_available"},
        ],
    }


def _scheduled_probes(history):
    return [
        event
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name
        == "container_job.assessment_readiness"
    ]


def _has_readiness_patch(history):
    marker = implementation.RUN_ASSESSMENT_RUNTIME_READINESS_PATCH.encode()
    return any(
        marker in payload.data
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
        for details in event.marker_recorded_event_attributes.details.values()
        for payload in details.payloads
    )


@pytest.mark.asyncio
async def test_real_retained_current_and_continued_planning_histories_replay(tmp_path):
    histories = []
    route = implementation.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "container_job.assessment_readiness"
    )
    queue = implementation.get_activity_task_queue(route, "")
    async with (
        await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=os.environ.get("MOONMIND_TEMPORAL_TEST_SERVER"),
        ) as env,
        Worker(env.client, task_queue=queue, activities=[_ready_probe]),
    ):
        for label, fixture, continuation, expected_probes in (
            ("retained", _LegacyPlanning, False, 0),
            ("current", _CurrentPlanning, False, 1),
            ("continuation", _CurrentPlanning, True, 1),
        ):
            task_queue = f"assessment-readiness-replay-{label}"
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[fixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    fixture.run,
                    args=[continuation, False],
                    id=task_queue,
                    task_queue=task_queue,
                )
                assert await handle.result() == "art_retained_plan"
                original = env.client.get_workflow_handle(
                    handle.id, run_id=handle.first_execution_run_id
                )
                history = await original.fetch_history()
                assert len(_scheduled_probes(history)) == expected_probes
                if label == "retained":
                    assert not _has_readiness_patch(history)
                else:
                    assert _has_readiness_patch(history)
                histories.append((label, history))
                if continuation:
                    continued = next(
                        event.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                        for event in history.events
                        if event.HasField(
                            "workflow_execution_continued_as_new_event_attributes"
                        )
                    )
                    continued_history = await env.client.get_workflow_handle(
                        handle.id, run_id=continued
                    ).fetch_history()
                    assert not _scheduled_probes(continued_history)
                    histories.append(("continued", continued_history))
    replayer = Replayer(
        workflows=[_CurrentPlanning], workflow_runner=UnsandboxedWorkflowRunner()
    )
    for label, history in histories:
        (tmp_path / f"{label}-history.json").write_text(history.to_json())
        await replayer.replay_workflow(history)
