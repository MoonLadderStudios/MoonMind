"""Interrupted-step successor histories replay across the #4627 patches.

A Run history recorded before ``run-omnigent-interrupted-step-workspace-v1``,
``run-interrupted-step-deadline-v1`` and
``run-interrupted-step-installed-admission-v1`` retried a host-loss Step with a
fresh sandbox, a fresh budget and the workflow's recorded execution plan. That
history must keep replaying on the current worker, and a history recorded with
the patches must replay as well.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_catalog import WORKFLOW_TASK_QUEUE
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    RUN_INTERRUPTED_STEP_DEADLINE_PATCH,
    RUN_INTERRUPTED_STEP_INSTALLED_ADMISSION_PATCH,
    RUN_OMNIGENT_INTERRUPTED_STEP_WORKSPACE_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _immediate_wait_condition,
    _mock_plan_payload,
)

_STEP = "omnigent-turn"
_ARCHIVE = "artifact://checkpoints/interrupted-step-archive"
_BASE_PATCHES = frozenset(
    {
        RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
        RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    }
)
_WORKSPACE_PATCHES = _BASE_PATCHES | {
    RUN_OMNIGENT_INTERRUPTED_STEP_WORKSPACE_PATCH,
    RUN_INTERRUPTED_STEP_DEADLINE_PATCH,
}
_CURRENT_PATCHES = _WORKSPACE_PATCHES | {RUN_INTERRUPTED_STEP_INSTALLED_ADMISSION_PATCH}
_REAL_PATCHED = workflow.patched
_CHILD_REQUESTS: list[AgentExecutionRequest] = []
_ADMISSIONS: list[dict[str, Any]] = []
_ADMISSION_RESULT: dict[str, Any] = {}


def _binding(digit: str) -> dict[str, str]:
    return {
        "planRef": "omnigent-execution-plan:sha256:" + digit * 64,
        "planDigest": "sha256:" + digit * 64,
        "planArtifactRef": f"art_plan_{digit}",
        "taskInputSnapshotRef": "art_original_task",
        "taskInputSnapshotDigest": "sha256:" + "1" * 64,
    }


# Admitted before the update, on the images installed then.
_RECORDED = _binding("2")
# Admitted for the successor against the newly installed images.
_INSTALLED = _binding("3")


@activity.defn(name="omnigent.admit_step_successor_plan")
async def _admit_successor(payload: dict[str, Any]) -> dict[str, Any]:
    _ADMISSIONS.append(payload)
    return dict(_ADMISSION_RESULT)


@workflow.defn(name="MoonMind.AgentRun")
class _InterruptedAgentRun:
    excluded_wait_seconds = 0

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        _CHILD_REQUESTS.append(request)
        if request.step_execution.execution_ordinal == 1:
            budget = request.execution_budget
            if self.excluded_wait_seconds:
                budget = budget.suspend(workflow.now())
            await workflow.sleep(
                timedelta(seconds=self.excluded_wait_seconds or 1800)
            )
            if self.excluded_wait_seconds:
                budget = budget.resume(workflow.now()).observe_progress(workflow.now())
            return AgentRunResult(
                summary="Omnigent session host was replaced.",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                retryRecommendation="retry_step_execution",
                metadata=(
                    {
                        "executionBudget": budget.model_dump(mode="json", by_alias=True)
                    }
                    if budget is not None
                    else {}
                ),
            )
        return AgentRunResult(summary="Completed on the successor attempt.")


async def _plan(activity_type: str, _payload: Any, **_kwargs: Any) -> bytes:
    assert activity_type == "artifact.read"
    return _mock_plan_payload(
        [
            {
                "id": _STEP,
                "tool": {"type": "agent_runtime", "name": "omnigent"},
                "inputs": {
                    "instructions": "Implement the change.",
                    "runtime": {"mode": "omnigent"},
                    "timeoutPolicy": {
                        "timeout_seconds": 3600,
                        "max_timeout_seconds": 7200,
                    },
                },
            }
        ]
    )


@workflow.defn(name="InterruptedStepReplayFixture")
class _InterruptedStepFixture:
    @workflow.run
    async def run(self) -> int:
        run = MoonMindRunWorkflow()
        run._owner_id = "owner-1"
        run._repo = "org/repo"

        async def bind_session(request):
            return request

        async def record_checkpoint(logical_step_id, *, boundary, **_kwargs):
            # Only the interrupted attempt's after-execution save is durable.
            if boundary != "after_execution":
                return None
            if (run._step_execution_for(logical_step_id) or 0) != 1:
                return None
            ref = "artifact://checkpoints/interrupted-step-manifest"
            run._step_checkpoint_refs[logical_step_id] = ref
            run._step_checkpoint_workspace_evidence_by_boundary.setdefault(
                logical_step_id, {}
            )["after_execution"] = {
                "checkpointRef": ref,
                "workspaceKind": "worktree_archive",
                "workspaceDigest": "sha256:" + "a" * 64,
                "workspaceIdentityDigest": "sha256:" + "b" * 64,
                "checkpointManifestRef": "artifact://checkpoints/manifest",
                "workspaceArchiveRef": _ARCHIVE,
            }
            return ref

        async def record_manifest(_logical_step_id, **_kwargs):
            return None

        run._maybe_bind_workflow_scoped_session = bind_session
        run._record_canonical_step_checkpoint = record_checkpoint
        run._record_step_execution_manifest = record_manifest
        await run._run_execution_stage(
            parameters={
                "publishMode": "none",
                "omnigentExecutionPlan": dict(_RECORDED),
                "resolvedSkillsetRef": "art_skills_recorded",
            },
            plan_ref="plan-ref",
        )
        return run._step_execution_for(_STEP) or 0


def _enable(monkeypatch: pytest.MonkeyPatch, patches: frozenset[str]) -> None:
    # Patches outside this journey stay off in every run and replay, as in the
    # execution-stage tests; the two #4627 ids use real marker semantics.
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id in patches and _REAL_PATCHED(patch_id),
    )


async def _record(env, monkeypatch, patches, name: str):
    _enable(monkeypatch, patches)
    _CHILD_REQUESTS.clear()
    _ADMISSIONS.clear()
    async with Worker(
        env.client,
        task_queue=f"{name}-queue",
        workflows=[_InterruptedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await env.client.start_workflow(
            _InterruptedStepFixture.run, id=name, task_queue=f"{name}-queue"
        )
        assert await asyncio.wait_for(handle.result(), timeout=30) == 2
        return await handle.fetch_history(), list(_CHILD_REQUESTS)


def _plan_refs(requests: list[AgentExecutionRequest]) -> list[str]:
    return [r.step_execution.omnigent_execution_plan.plan_ref for r in requests]


def _patch_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_workflow_module, "execute_typed_activity", _plan)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _m: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _a: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )


def _workers(env):
    admission_queue = run_workflow_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
        "omnigent.admit_step_successor_plan"
    ).task_queue
    return (
        Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[_InterruptedAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(env.client, task_queue=admission_queue, activities=[_admit_successor]),
    )


@pytest.mark.asyncio
async def test_interrupted_step_pre_and_post_patch_histories_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_environment(monkeypatch)
    _ADMISSION_RESULT.clear()
    _ADMISSION_RESULT.update(
        disposition="admitted",
        omnigentExecutionPlan=dict(_INSTALLED),
        resolvedSkillsetRef="art_skills_installed",
    )
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        agent_worker, admission_worker = _workers(env)
        async with agent_worker, admission_worker:
            legacy_history, legacy = await _record(
                env, monkeypatch, _BASE_PATCHES, "interrupted-step-legacy"
            )
            workspace_history, workspace = await _record(
                env, monkeypatch, _WORKSPACE_PATCHES, "interrupted-step-workspace"
            )
            current_history, current = await _record(
                env, monkeypatch, _CURRENT_PATCHES, "interrupted-step-current"
            )
            current_admissions = list(_ADMISSIONS)

    # Recorded before the patches: a fresh sandbox, a fresh budget and the
    # recorded execution plan.
    assert [r.step_execution.execution_ordinal for r in legacy] == [1, 2]
    assert "workspaceCheckpointRestoreRef" not in (legacy[1].workspace_spec or {})
    assert legacy[1].timeout_policy["max_timeout_seconds"] == 7200
    assert _plan_refs(legacy) == [_RECORDED["planRef"]] * 2
    # Recorded with the workspace patches: the verified save and the remaining
    # ceiling, still on the recorded plan.
    for requests in (workspace, current):
        assert [r.step_execution.execution_ordinal for r in requests] == [1, 2]
        assert requests[1].workspace_spec["workspaceCheckpointRestoreRef"] == _ARCHIVE
        assert 5390 <= requests[1].timeout_policy["max_timeout_seconds"] <= 5400
    assert _plan_refs(workspace) == [_RECORDED["planRef"]] * 2
    # Recorded with installed admission: only the successor is admitted again,
    # from the recorded plan's frozen inputs, and launches on the new plan with
    # its Skill snapshot. The interrupted attempt keeps the plan it used.
    assert len(current_admissions) == 1
    assert current_admissions[0]["workflowId"] == "interrupted-step-current"
    assert current_admissions[0]["initialParameters"]["omnigentExecutionPlan"] == (
        _RECORDED
    )
    assert _plan_refs(current) == [_RECORDED["planRef"], _INSTALLED["planRef"]]
    assert current[0].step_execution.resolved_skillset_ref == "art_skills_recorded"
    assert current[1].step_execution.resolved_skillset_ref == "art_skills_installed"

    _enable(monkeypatch, _CURRENT_PATCHES)
    replayer = Replayer(
        workflows=[_InterruptedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(workspace_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded_wait_seconds", [0, 5400])
async def test_parent_successor_carries_logical_budget_instead_of_new_base(
    monkeypatch, excluded_wait_seconds
):
    """The real parent retry owner must carry its admitted clock (#4986)."""
    _patch_environment(monkeypatch)
    monkeypatch.setattr(
        _InterruptedAgentRun, "excluded_wait_seconds", excluded_wait_seconds
    )
    _ADMISSION_RESULT.clear()
    _ADMISSION_RESULT.update(
        disposition="admitted",
        omnigentExecutionPlan=dict(_INSTALLED),
        resolvedSkillsetRef="art_skills_installed",
    )
    patches = _CURRENT_PATCHES | {"run-logical-step-execution-budget-v1"}
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        agent_worker, admission_worker = _workers(env)
        async with agent_worker, admission_worker:
            history, requests = await _record(
                env, monkeypatch, patches, "logical-step-budget"
            )
    first, second = requests
    assert first.execution_budget is not None
    assert second.execution_budget is not None
    assert (
        first.execution_budget.logical_step_id
        == second.execution_budget.logical_step_id
    )
    assert first.execution_budget.budget == second.execution_budget.budget
    if excluded_wait_seconds:
        assert second.execution_budget.consumed_seconds <= 10
        assert second.execution_budget.last_progress_seconds is not None
        assert (
            second.execution_budget.remaining_seconds(
                second.execution_budget.admitted_at
            )
            >= 3590
        )
    else:
        assert 1800 <= second.execution_budget.consumed_seconds <= 1810
    assert second.execution_budget.recovery_count == 1
    assert second.execution_budget.budget.base_seconds == 3600
    if not excluded_wait_seconds:
        assert (
            second.execution_budget.remaining_seconds(second.execution_budget.admitted_at)
            <= 1800
        )
    _enable(monkeypatch, patches)
    await Replayer(
        workflows=[_InterruptedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_successor_keeps_the_recorded_plan_when_admission_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_environment(monkeypatch)
    _ADMISSION_RESULT.clear()
    _ADMISSION_RESULT.update(
        disposition="recorded",
        reason="re-admission would change the recorded request intent: model",
    )
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        agent_worker, admission_worker = _workers(env)
        async with agent_worker, admission_worker:
            _history, requests = await _record(
                env, monkeypatch, _CURRENT_PATCHES, "interrupted-step-refused"
            )
            admissions = list(_ADMISSIONS)

    # A refused admission is truthful: the successor still restarts, on the
    # recorded plan and Skill snapshot, under that plan's launch-time checks.
    assert len(admissions) == 1
    assert [r.step_execution.execution_ordinal for r in requests] == [1, 2]
    assert _plan_refs(requests) == [_RECORDED["planRef"]] * 2
    assert requests[1].step_execution.resolved_skillset_ref == "art_skills_recorded"
