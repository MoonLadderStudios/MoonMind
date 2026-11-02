"""Old native parents and new shared pool context handoffs replay unchanged."""

import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.workspace_sources import compile_workspace_source
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.run import (
    RUN_DISPATCH_CHECKPOINT_SOURCE_PATCH,
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
        workspaceSpec={"workspaceSource": {"kind": "scratch"}},
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
            "workspaceCheckpointRestoreRef": "artifact://verified-saved-workspace",
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
            == "artifact://verified-saved-workspace"
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
async def test_retained_native_and_new_pool_context_histories_replay(monkeypatch):
    patched = workflow.patched
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for index, (implementation, normalize_source) in enumerate(
            (
                (_LegacyFixture, False),
                (_CurrentFixture, False),
                (_CurrentFixture, True),
            )
        ):
            queue = f"dispatch-pool-context-{index}"
            with monkeypatch.context() as recorded_version:
                if not normalize_source:
                    # Generate the retained command stream without the new
                    # patch marker, then replay it with current workflow code.
                    recorded_version.setattr(
                        workflow,
                        "patched",
                        lambda marker: False
                        if marker == RUN_DISPATCH_CHECKPOINT_SOURCE_PATCH
                        else patched(marker),
                    )
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
            observed = next(
                json.loads(
                    event.activity_task_scheduled_event_attributes.input.payloads[0].data
                )
                for event in history.events
                if event.HasField("activity_task_scheduled_event_attributes")
                and event.activity_task_scheduled_event_attributes.activity_type.name
                == "pool_context.fixture_observe"
            )
            if normalize_source:
                assert (
                    compile_workspace_source(observed["workspaceSpec"]).kind
                    == "checkpoint"
                )
            else:
                assert observed["workspaceSpec"]["workspaceSource"] == {"kind": "scratch"}
            await Replayer(
                workflows=[_CurrentFixture], workflow_runner=UnsandboxedWorkflowRunner()
            ).replay_workflow(history)
