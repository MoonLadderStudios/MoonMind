"""Interrupted-step successor histories replay across the #4627 patches.

A Run history recorded before ``run-omnigent-interrupted-step-workspace-v1``
and ``run-interrupted-step-deadline-v1`` retried a host-loss Step with a fresh
sandbox and a fresh budget. That history must keep replaying on the current
worker, and a history recorded with the patches must replay as well.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from temporalio import workflow
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
_CURRENT_PATCHES = _BASE_PATCHES | {
    RUN_OMNIGENT_INTERRUPTED_STEP_WORKSPACE_PATCH,
    RUN_INTERRUPTED_STEP_DEADLINE_PATCH,
}
_REAL_PATCHED = workflow.patched
_CHILD_REQUESTS: list[AgentExecutionRequest] = []


@workflow.defn(name="MoonMind.AgentRun")
class _InterruptedAgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        _CHILD_REQUESTS.append(request)
        if request.step_execution.execution_ordinal == 1:
            # The update replaces the host half an hour into the Step.
            await workflow.sleep(timedelta(seconds=1800))
            return AgentRunResult(
                summary="Omnigent session host was replaced.",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
                retryRecommendation="retry_step_execution",
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
        run._integration = None

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
            parameters={"publishMode": "none"}, plan_ref="plan-ref"
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
    async with Worker(
        env.client,
        task_queue=f"{name}-queue",
        workflows=[_InterruptedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await env.client.start_workflow(
            _InterruptedStepFixture.run, id=name, task_queue=f"{name}-queue"
        )
        assert await handle.result() == 2
        return await handle.fetch_history(), list(_CHILD_REQUESTS)


@pytest.mark.asyncio
async def test_interrupted_step_pre_and_post_patch_histories_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module, "execute_typed_activity", _plan)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _m: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _a: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        async with Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[_InterruptedAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_history, legacy = await _record(
                env, monkeypatch, _BASE_PATCHES, "interrupted-step-legacy"
            )
            current_history, current = await _record(
                env, monkeypatch, _CURRENT_PATCHES, "interrupted-step-current"
            )

    # Recorded before the patches: a fresh sandbox and a fresh budget.
    assert [r.step_execution.execution_ordinal for r in legacy] == [1, 2]
    assert "workspaceCheckpointRestoreRef" not in (legacy[1].workspace_spec or {})
    assert legacy[1].timeout_policy["max_timeout_seconds"] == 7200
    # Recorded with the patches: the verified save and the remaining ceiling.
    assert [r.step_execution.execution_ordinal for r in current] == [1, 2]
    assert current[1].workspace_spec["workspaceCheckpointRestoreRef"] == _ARCHIVE
    assert 5390 <= current[1].timeout_policy["max_timeout_seconds"] <= 5400

    _enable(monkeypatch, _CURRENT_PATCHES)
    replayer = Replayer(
        workflows=[_InterruptedStepFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)
