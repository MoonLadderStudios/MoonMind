"""Retained native pool histories keep their commands; new handoffs replay."""

import json
from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.workspace_sources import compile_workspace_source
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_POOL_CHECKPOINT_SOURCE_PATCH_ID,
    AGENT_RUN_POOL_PARENT_HANDOFF_PATCH_ID,
    AGENT_RUN_SELECTED_FIXED_SHARED_PLAN_PATCH_ID,
    AGENT_RUN_UNTRANSLATED_SHARED_CONTEXT_PATCH_ID,
    AGENT_RUN_SELECTED_POOL_SHARED_PLAN_PATCH_ID,
    MoonMindAgentRun,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    PLAN_REF,
    _plan_binding,
)


class _BeforePoolHandoff(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_SELECTED_POOL_SHARED_PLAN_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


class _BeforeParentHandoff(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_POOL_PARENT_HANDOFF_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


class _BeforeFixedHandoff(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_SELECTED_FIXED_SHARED_PLAN_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


class _BeforeContextFallback(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_UNTRANSLATED_SHARED_CONTEXT_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


class _BeforeCooldownCheckpointSource(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_POOL_CHECKPOINT_SOURCE_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


@workflow.defn
class _PreparationTransport:
    def __init__(self):
        self.finished = False

    @workflow.signal
    async def prepare_shared_execution_plan(self, payload):
        request = dict(payload["request"])
        native_fallback = request["parameters"].get("native_fallback_fixture", False)
        request.update(
            agentKind="external",
            agentId="omnigent",
            omnigentExecutionPlan=_plan_binding(PLAN_REF),
        )
        request["stepExecution"]["omnigentExecutionPlan"] = _plan_binding(PLAN_REF)
        await workflow.get_external_workflow_handle(
            payload["owner_id"], run_id=payload["runId"]
        ).signal(
            "pool_plan_prepared",
            {
                "disposition": "native" if native_fallback else "shared",
                "request": request,
                "requestId": request["idempotencyKey"],
                "fencingGeneration": payload["fencing_generation"],
            },
        )

    @workflow.signal
    def finish(self):
        self.finished = True

    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: self.finished)


class _PoolFixture:
    implementation = MoonMindAgentRun
    fixed_account = False
    native_fallback = False
    cooldown = False

    def __init__(self):
        self.agent = self.implementation()

    @workflow.signal
    def pool_plan_prepared(self, payload):
        self.agent.pool_plan_prepared(payload)

    async def execute(self):
        request = AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            executionProfileRef="selected-account",
            profileSelector={} if self.fixed_account else {"tagsAll": ["pool"]},
            correlationId="parent",
            idempotencyKey="request-1",
            instructionRef="Preserve the accepted task.",
            workspaceSpec=(
                {"workspaceSource": {"kind": "scratch"}} if self.cooldown else {}
            ),
            parameters={"native_fallback_fixture": self.native_fallback},
            stepExecution={
                "workflowId": "parent",
                "runId": "parent-run",
                "logicalStepId": "work",
                "executionOrdinal": 1,
                "stepExecutionId": "parent:parent-run:work:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        )
        from moonmind.workflows.temporal.workflows.run import (
            MoonMindUserWorkflow,
            RUN_SHARED_RUNTIME_CONTEXT_PATCH,
        )

        if (
            self.implementation is not MoonMindAgentRun
            and not self.native_fallback
            and not self.cooldown
        ):
            workflow.patched(RUN_SHARED_RUNTIME_CONTEXT_PATCH)
        else:
            request = MoonMindUserWorkflow()._bind_shared_runtime_context(request)
        self.agent._assigned_profile_id = request.execution_profile_ref
        self.agent._assigned_slot_fencing_generation = 7
        if self.native_fallback:
            request.step_execution.runtime_context_policy = "reuse_session_same_epoch"
            from moonmind.schemas.agent_runtime_models import RuntimeSessionContextReference

            request.step_execution.runtime_session_context = RuntimeSessionContextReference(
                workflowId="parent", runId="parent-run",
                stepExecutionId="parent:parent-run:previous:execution:1", epoch="previous",
                runtimeBindingRef="omnigent-runtime-binding:sha256:" + "a" * 64,
            )
        if self.agent._uses_selected_pool_shared_admission(request, "codex_cli"):
            native_request = request.model_copy(deep=True)
            try:
                request = await self.agent._prepare_selected_pool_shared_request(
                    request=request,
                    runtime_id="codex_cli",
                    original_profile_ref=None,
                    manager_handle=workflow.get_external_workflow_handle(
                        "pool-preparation-transport"
                    ),
                )
            except ApplicationError as error:
                assert self.native_fallback
                assert error.type == "SelectedProfileSharedContextUnavailable"
                return "blocked-shared-context"
            if self.cooldown:
                self.agent._execution_budget = native_request.execution_budget
                self.agent.final_result = AgentRunResult(
                    summary="Provider temporarily busy",
                    metadata={
                        "savedWorkspaceCheckpoint": {
                            "archiveRef": "artifact://accepted-workspace"
                        }
                    },
                )
                request = self.agent._pool_cooldown_retry_request(native_request)
                request = await self.agent._prepare_selected_pool_shared_request(
                    request=request,
                    runtime_id="codex_cli",
                    original_profile_ref=None,
                    manager_handle=workflow.get_external_workflow_handle(
                        "pool-preparation-transport"
                    ),
                )
        await workflow.sleep(1)
        return request.agent_id


@workflow.defn(name="SelectedPoolPlanReplayFixture")
class _LegacyFixture(_PoolFixture):
    implementation = _BeforePoolHandoff

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedPoolPlanReplayFixture")
class _CurrentFixture(_PoolFixture):
    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedPoolPlanReplayFixture")
class _ConvertedLegacyFixture(_PoolFixture):
    implementation = _BeforeParentHandoff

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedFixedPlanReplayFixture")
class _LegacyFixedFixture(_PoolFixture):
    implementation = _BeforeFixedHandoff
    fixed_account = True

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedFixedPlanReplayFixture")
class _CurrentFixedFixture(_PoolFixture):
    fixed_account = True

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SharedContextFallbackReplayFixture")
class _LegacyContextFallbackFixture(_PoolFixture):
    implementation = _BeforeContextFallback
    native_fallback = True

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SharedContextFallbackReplayFixture")
class _CurrentContextFallbackFixture(_PoolFixture):
    native_fallback = True

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedPoolCooldownReplayFixture")
class _LegacyCooldownFixture(_PoolFixture):
    implementation = _BeforeCooldownCheckpointSource
    cooldown = True

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedPoolCooldownReplayFixture")
class _CurrentCooldownFixture(_PoolFixture):
    cooldown = True

    @workflow.run
    async def run(self):
        return await self.execute()


@pytest.mark.asyncio
async def test_retained_native_and_shared_pool_handoffs_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pool-preparation",
            workflows=[_PreparationTransport],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            manager = await env.client.start_workflow(
                _PreparationTransport.run,
                id="pool-preparation-transport",
                task_queue="pool-preparation",
            )
            for label, fixture, expected in (
                ("retained", _LegacyFixture, "codex_cli"),
                (
                    "converted-before-parent-handoff",
                    _ConvertedLegacyFixture,
                    "omnigent",
                ),
                ("current", _CurrentFixture, "omnigent"),
                ("retained-fixed", _LegacyFixedFixture, "codex_cli"),
                ("current-fixed", _CurrentFixedFixture, "omnigent"),
                ("retained-context-fallback", _LegacyContextFallbackFixture, "codex_cli"),
                ("current-context-fallback", _CurrentContextFallbackFixture, "blocked-shared-context"),
                ("retained-cooldown", _LegacyCooldownFixture, "omnigent"),
                ("current-cooldown", _CurrentCooldownFixture, "omnigent"),
            ):
                queue = f"pool-replay-{label}"
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[fixture],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ):
                    handle = await env.client.start_workflow(
                        fixture.run,
                        id=queue,
                        task_queue=queue,
                        execution_timeout=timedelta(seconds=30),
                    )
                    # Keep the idle transport alive between fixture runs; global
                    # time skipping could otherwise advance to its execution timeout.
                    with env.auto_time_skipping_disabled():
                        result = await handle.result()
                    assert result == expected
                    history = await handle.fetch_history()
                    histories.append(history)
                    if fixture.cooldown:
                        preparations = []
                        for event in history.events:
                            if not event.HasField(
                                "signal_external_workflow_execution_initiated_event_attributes"
                            ):
                                continue
                            signal = event.signal_external_workflow_execution_initiated_event_attributes
                            if signal.signal_name == "prepare_shared_execution_plan":
                                preparations.append(
                                    json.loads(signal.input.payloads[0].data)["request"]
                                )
                        assert len(preparations) == 2
                        retry_workspace = preparations[1]["workspaceSpec"]
                        assert (
                            retry_workspace["workspaceCheckpointRestoreRef"]
                            == "artifact://accepted-workspace"
                        )
                        if fixture is _CurrentCooldownFixture:
                            assert (
                                compile_workspace_source(retry_workspace).kind
                                == "checkpoint"
                            )
                        else:
                            assert retry_workspace["workspaceSource"] == {
                                "kind": "scratch"
                            }
            await manager.signal("finish")
            await manager.result()
    replayer = Replayer(
        workflows=[
            _CurrentFixture,
            _CurrentFixedFixture,
            _CurrentContextFallbackFixture,
            _CurrentCooldownFixture,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
