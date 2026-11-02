"""Old native parents and new shared pool context handoffs replay unchanged."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_POOL_CANONICAL_WORKSPACE_HANDOFF_PATCH_ID,
    MoonMindAgentRun,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_SHARED_RUNTIME_CONTEXT_PATCH,
    MoonMindUserWorkflow,
)


class _BeforePoolContext(MoonMindUserWorkflow):
    def _bind_shared_runtime_context(self, request):
        workflow.patched(RUN_SHARED_RUNTIME_CONTEXT_PATCH)
        return request

    def _record_shared_runtime_context(self, request, result):
        workflow.patched(RUN_SHARED_RUNTIME_CONTEXT_PATCH)


def _request(step, policy):
    info = workflow.info()
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        profileSelector={"tagsAll": ["selected-pool"]},
        correlationId=info.workflow_id,
        idempotencyKey=step,
        stepExecution={
            "workflowId": info.workflow_id,
            "runId": info.run_id,
            "logicalStepId": step,
            "executionOrdinal": 1,
            "stepExecutionId": f"{info.workflow_id}:{info.run_id}:{step}:execution:1",
            "runtimeContextPolicy": policy,
        },
    )


@activity.defn(name="pool_context.fixture_result")
async def _result(request):
    step = request["stepExecution"]
    reference = {
        "workflowId": step["workflowId"],
        "runId": step["runId"],
        "stepExecutionId": step["stepExecutionId"],
        "epoch": step["stepExecutionId"],
        "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "1" * 64,
    }
    metadata = {
        "runtimeSessionContext": reference,
        "runtimeBindingRef": reference["runtimeBindingRef"],
    }
    if (
        request.get("parameters", {})
        .get("metadata", {})
        .get("moonmind", {})
        .get("poolContextHandoff")
    ):
        metadata["dispatchPoolContext"] = {
            "executionProfileRef": "selected-account",
            "workspaceCheckpointRestoreRef": "artifact:verified-saved-workspace",
        }
    return {"metadata": metadata}


@activity.defn(name="pool_context.fixture_observe")
async def _observe(request):
    reference = request["stepExecution"].get("runtimeSessionContext")
    if reference is not None:
        assert reference["workflowId"] == request["stepExecution"]["workflowId"]
        assert ":first:execution:1" in reference["stepExecutionId"]
        assert request["executionProfileRef"] == "selected-account"
        assert (
            request["workspaceSpec"]["workspaceCheckpointRestoreRef"]
            == "artifact:verified-saved-workspace"
        )
    return reference is not None


class _PoolContextFixture:
    owner_class = MoonMindUserWorkflow

    async def execute(self):
        owner = self.owner_class()
        first = owner._bind_shared_runtime_context(_request("first", "fresh_agent_run"))
        result = await workflow.execute_activity(
            _result,
            first.model_dump(mode="json", by_alias=True),
            start_to_close_timeout=timedelta(seconds=5),
        )
        owner._record_shared_runtime_context(first, result)
        second = owner._bind_shared_runtime_context(
            _request("second", "reuse_session_same_epoch")
        )
        return await workflow.execute_activity(
            _observe,
            second.model_dump(mode="json", by_alias=True),
            start_to_close_timeout=timedelta(seconds=5),
        )


@workflow.defn(name="DispatchPoolContextReplayFixture")
class _LegacyFixture(_PoolContextFixture):
    owner_class = _BeforePoolContext

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="DispatchPoolContextReplayFixture")
class _CurrentFixture(_PoolContextFixture):
    @workflow.run
    async def run(self):
        return await self.execute()


@pytest.mark.asyncio
async def test_retained_native_and_new_pool_context_histories_replay():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for index, implementation in enumerate((_LegacyFixture, _CurrentFixture)):
            queue = f"dispatch-pool-context-{index}"
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[_result, _observe],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await environment.client.start_workflow(
                    implementation.run,
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=20),
                )
                assert await handle.result() is bool(index)
                history = await handle.fetch_history()
            await Replayer(
                workflows=[_CurrentFixture], workflow_runner=UnsandboxedWorkflowRunner()
            ).replay_workflow(history)


class _BeforeCanonicalSave(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        return marker != AGENT_RUN_POOL_CANONICAL_WORKSPACE_HANDOFF_PATCH_ID and MoonMindAgentRun._workflow_patch_enabled(marker)


@activity.defn(name="pool_save.archive_input")
async def _archive_input(reference):
    assert reference == "artifact:worktree-archive"
    return reference


@activity.defn(name="pool_save.canonical_input")
async def _canonical_input(reference):
    assert reference == "artifact:complete-checkpoint"
    return reference


class _PoolSaveFixture:
    child_class = MoonMindAgentRun

    async def execute(self):
        selected = _request("first", "fresh_agent_run").model_copy(update={"execution_profile_ref": "selected-account"})
        child = self.child_class()
        child._pool_native_request = selected
        payload = await workflow.execute_activity(_result, selected.model_dump(mode="json", by_alias=True), start_to_close_timeout=timedelta(seconds=5))
        payload["metadata"]["savedWorkspaceCheckpoint"] = {
            "archiveRef": "artifact:worktree-archive", "checkpointRef": "artifact:complete-checkpoint", "baseCommit": "a" * 40,
        }
        delivered = child._with_dispatch_pool_context(AgentRunResult.model_validate(payload))
        reference = delivered.metadata["dispatchPoolContext"]["workspaceCheckpointRestoreRef"]
        # Different real commands make a changed recorded handoff observable
        # to replay; activity payload equality alone is not a replay check.
        observe = _canonical_input if reference == "artifact:complete-checkpoint" else _archive_input
        return await workflow.execute_activity(observe, reference, start_to_close_timeout=timedelta(seconds=5))


@workflow.defn(name="DispatchPoolSaveReplayFixture")
class _OldSaveFixture(_PoolSaveFixture):
    child_class = _BeforeCanonicalSave

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="DispatchPoolSaveReplayFixture")
class _CurrentSaveFixture(_PoolSaveFixture):
    @workflow.run
    async def run(self):
        return await self.execute()


@pytest.mark.asyncio
async def test_recorded_archive_and_new_canonical_save_handoffs_replay():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for index, implementation in enumerate((_OldSaveFixture, _CurrentSaveFixture)):
            queue = f"dispatch-pool-save-{index}"
            async with Worker(environment.client, task_queue=queue, workflows=[implementation], activities=[_result, _archive_input, _canonical_input], workflow_runner=UnsandboxedWorkflowRunner()):
                handle = await environment.client.start_workflow(implementation.run, id=queue, task_queue=queue, execution_timeout=timedelta(seconds=20))
                assert await handle.result() == ("artifact:complete-checkpoint" if index else "artifact:worktree-archive")
                history = await handle.fetch_history()
            await Replayer(workflows=[_CurrentSaveFixture], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)
