"""A step that retry from a step re-executes is admitted on the installed runtime.

A run admitted its Omnigent plan before an update and failed at a step. Retry
from a step resets its history to that step's start on a real Temporal server.
The retried step admits the run's frozen inputs again, as an interrupted
step's successor does, and launches on the installed plan; the earlier step is
not repeated. The failed history and the retried run's history replay on the
installed code.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal import retry_from_step
from moonmind.workflows.temporal.activity_catalog import WORKFLOW_TASK_QUEUE
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_RETRIED_STEP_INSTALLED_ADMISSION_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _immediate_wait_condition,
    _mock_plan_payload,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_QUEUE = "retried-step-admission"
# Patches outside this journey stay off, as in the execution-stage tests.
_BEFORE_UPDATE = frozenset(
    {RUN_CONDITIONAL_REGISTRY_READ_PATCH, RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH}
)
_INSTALLED = _BEFORE_UPDATE | {RUN_RETRIED_STEP_INSTALLED_ADMISSION_PATCH}
_REAL_PATCHED = workflow.patched
_LEDGER = [
    {"logicalStepId": "prepare", "order": 1, "status": "completed"},
    {"logicalStepId": "implement", "order": 2, "status": "failed"},
]
_CHILD_REQUESTS: list[AgentExecutionRequest] = []
_ADMISSIONS: list[dict[str, Any]] = []
_ADMISSION_RESULT: dict[str, Any] = {}
_FAILING_STEP: dict[str, str | None] = {"step": None}


def _binding(digit: str) -> dict[str, str]:
    return {
        "planRef": "omnigent-execution-plan:sha256:" + digit * 64,
        "planDigest": "sha256:" + digit * 64,
        "planArtifactRef": f"art_plan_{digit}",
        "taskInputSnapshotRef": "art_original_task",
        "taskInputSnapshotDigest": "sha256:" + "1" * 64,
    }


# Admitted when the run started, on the images installed then.
_RECORDED = _binding("2")
# Admitted for the retried step against the newly installed images.
_INSTALLED_PLAN = _binding("3")
_RUNTIME_CHANGES = {
    "hostImageRef": {
        "from": "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
        "to": "ghcr.io/example/omnigent-host@sha256:" + "8" * 64,
    }
}


@activity.defn(name="omnigent.admit_step_successor_plan")
async def _admit(payload: dict[str, Any]) -> dict[str, Any]:
    _ADMISSIONS.append(payload)
    return dict(_ADMISSION_RESULT)


@workflow.defn(name="MoonMind.AgentRun")
class _AgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        _CHILD_REQUESTS.append(request)
        if request.step_execution.logical_step_id == _FAILING_STEP["step"]:
            raise ApplicationError("The implementation failed.", non_retryable=True)
        return AgentRunResult(summary="Completed.")


async def _plan(activity_type: str, _payload: Any, **_kwargs: Any) -> bytes:
    assert activity_type == "artifact.read"
    return _mock_plan_payload(
        [
            {
                "id": step,
                "tool": {"type": "agent_runtime", "name": "omnigent"},
                "inputs": {"instructions": f"{step}.", "runtime": {"mode": "omnigent"}},
            }
            for step in ("prepare", "implement")
        ]
    )


@workflow.defn(name="RetriedStepFixture")
class _RetriedStepFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        run = MoonMindRunWorkflow()
        run._watch_for_history_reset()
        run._owner_id = "owner-1"
        run._repo = "org/repo"

        async def bind_session(request):
            return request

        async def no_checkpoint(*_args, **_kwargs):
            return None

        run._maybe_bind_workflow_scoped_session = bind_session
        run._record_canonical_step_checkpoint = no_checkpoint
        run._record_step_execution_manifest = no_checkpoint
        await run._run_execution_stage(
            parameters={
                "publishMode": "none",
                "omnigentExecutionPlan": dict(_RECORDED),
                "resolvedSkillsetRef": "art_skills_recorded",
            },
            plan_ref="plan-ref",
        )
        # What the retried Step Execution's manifest records about its runtime.
        return run._step_execution_compact_execution_refs("implement")


def _enable(monkeypatch: pytest.MonkeyPatch, patches: frozenset[str]) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id in patches and _REAL_PATCHED(patch_id),
    )


def _patch_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Memo upserts stay real: retry finds each step's start by them.
    monkeypatch.setattr(run_workflow_module, "execute_typed_activity", _plan)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _a: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        retry_from_step, "_replay_workflows", lambda: [_RetriedStepFixture]
    )


def _plan_refs(requests: list[AgentExecutionRequest]) -> list[tuple[str, str]]:
    return [
        (
            r.step_execution.logical_step_id,
            r.step_execution.omnigent_execution_plan.plan_ref,
        )
        for r in requests
    ]


async def _fail_then_retry(env, monkeypatch: pytest.MonkeyPatch):
    """Fail a run before the update and retry it from its failed step after."""

    admission_queue = run_workflow_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "omnigent.admit_step_successor_plan"
    ).task_queue
    workflow_id = f"retried-step-{uuid.uuid4()}"
    async with (
        Worker(
            env.client,
            task_queue=_QUEUE,
            workflows=[_RetriedStepFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
            # Every workflow task replays the history from the start.
            max_cached_workflows=0,
        ),
        Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[_AgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(env.client, task_queue=admission_queue, activities=[_admit]),
    ):
        _enable(monkeypatch, _BEFORE_UPDATE)
        _FAILING_STEP["step"] = "implement"
        handle = await env.client.start_workflow(
            _RetriedStepFixture.run, id=workflow_id, task_queue=_QUEUE
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        failed_history = await handle.fetch_history()
        failed_requests = list(_CHILD_REQUESTS)
        _CHILD_REQUESTS.clear()

        # The update installs code with retried-step admission.
        _enable(monkeypatch, _INSTALLED)
        _FAILING_STEP["step"] = None
        converter = MOONMIND_TEMPORAL_DATA_CONVERTER.payload_converter
        plan = retry_from_step.plan_retry_from_step(
            events=failed_history.events,
            ledger_rows=_LEDGER,
            decode_memo_value=converter.from_payload,
            failed_step_id="implement",
        )
        assert plan.logical_step_id == "implement"
        assert (
            await retry_from_step.replay_failure_before_reset(
                failed_history, until_event_id=plan.replay_until_event_id
            )
            is None
        )
        run_id = await TemporalClientAdapter(env.client).reset_workflow_execution(
            workflow_id,
            run_id=failed_history.run_id,
            workflow_task_finish_event_id=plan.reset_event_id,
            reason="MoonMind retry from step implement",
            request_id=str(uuid.uuid4()),
        )
        retried = env.client.get_workflow_handle(workflow_id, run_id=run_id)
        execution = await retried.result()
        return failed_history, failed_requests, await retried.fetch_history(), execution


async def _replays(*histories) -> None:
    replayer = Replayer(
        workflows=[_RetriedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    for history in histories:
        await replayer.replay_workflow(history)


async def test_retried_step_launches_on_the_installed_runtime_with_its_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_environment(monkeypatch)
    _CHILD_REQUESTS.clear()
    _ADMISSIONS.clear()
    _ADMISSION_RESULT.clear()
    _ADMISSION_RESULT.update(
        disposition="admitted",
        omnigentExecutionPlan=dict(_INSTALLED_PLAN),
        resolvedSkillsetRef="art_skills_installed",
        runtimeChanges=_RUNTIME_CHANGES,
    )
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        failed_history, failed, retried_history, execution = await _fail_then_retry(
            env, monkeypatch
        )
    retried = list(_CHILD_REQUESTS)

    # Before the update both steps ran on the plan admitted at start.
    assert _plan_refs(failed) == [
        ("prepare", _RECORDED["planRef"]),
        ("implement", _RECORDED["planRef"]),
    ]
    # The retry keeps the completed step and admits only the retried one,
    # from the run's frozen inputs and the plan its earlier attempt used.
    assert len(_ADMISSIONS) == 1
    assert _ADMISSIONS[0]["trigger"] == "history_reset"
    assert _ADMISSIONS[0]["initialParameters"]["omnigentExecutionPlan"] == _RECORDED
    assert _plan_refs(retried) == [("implement", _INSTALLED_PLAN["planRef"])]
    assert retried[0].step_execution.resolved_skillset_ref == "art_skills_installed"
    # The retried Step Execution records the runtime it moved to.
    assert execution["runtimeAdmission"] == {
        "trigger": "history_reset",
        "disposition": "admitted",
        "planRef": _INSTALLED_PLAN["planRef"],
        "previousPlanRef": _RECORDED["planRef"],
        "runtimeChanges": _RUNTIME_CHANGES,
    }
    _enable(monkeypatch, _INSTALLED)
    await _replays(failed_history, retried_history)


async def test_retried_step_keeps_the_recorded_plan_when_admission_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_environment(monkeypatch)
    _CHILD_REQUESTS.clear()
    _ADMISSIONS.clear()
    _ADMISSION_RESULT.clear()
    _ADMISSION_RESULT.update(
        disposition="recorded",
        reason="re-admission would change the recorded request intent: model",
    )
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        _failed_history, _failed, retried_history, execution = await _fail_then_retry(
            env, monkeypatch
        )
    retried = list(_CHILD_REQUESTS)

    # A refused admission is truthful: the step still runs again, on the
    # recorded plan under its launch-time checks, and records why.
    assert len(_ADMISSIONS) == 1
    assert _plan_refs(retried) == [("implement", _RECORDED["planRef"])]
    assert retried[0].step_execution.resolved_skillset_ref == "art_skills_recorded"
    assert execution["runtimeAdmission"] == {
        "trigger": "history_reset",
        "disposition": "recorded",
        "planRef": _RECORDED["planRef"],
        "reason": "re-admission would change the recorded request intent: model",
    }
    _enable(monkeypatch, _INSTALLED)
    await _replays(retried_history)


async def test_a_run_without_a_reset_is_not_admitted_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_environment(monkeypatch)
    _CHILD_REQUESTS.clear()
    _ADMISSIONS.clear()
    _enable(monkeypatch, _INSTALLED)
    _FAILING_STEP["step"] = None
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        Worker(
            env.client,
            task_queue=_QUEUE,
            workflows=[_RetriedStepFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[_AgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        execution = await env.client.execute_workflow(
            _RetriedStepFixture.run,
            id=f"unretried-{uuid.uuid4()}",
            task_queue=_QUEUE,
        )

    assert _ADMISSIONS == []
    assert _plan_refs(_CHILD_REQUESTS) == [
        ("prepare", _RECORDED["planRef"]),
        ("implement", _RECORDED["planRef"]),
    ]
    assert "runtimeAdmission" not in execution
