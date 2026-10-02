"""Retained checkpoint commands keep the pre-hardening capability decision."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH,
    MoonMindRunWorkflow,
)


@activity.defn(name="dispatch_authority.record_capture")
async def record_capture(runtime_id: str) -> str:
    return runtime_id


@workflow.defn(name="DispatchAuthorityCheckpointFixture")
class LegacyCheckpointFixture:
    @workflow.run
    async def run(self) -> str:
        # Before hardening, the result snapshot wins over the prelaunch runtime.
        workflow.patched(RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH)
        return await workflow.execute_activity(
            record_capture, "jules", start_to_close_timeout=timedelta(seconds=5)
        )


@workflow.defn(name="DispatchAuthorityCheckpointFixture")
class CurrentCheckpointFixture:
    @workflow.run
    async def run(self) -> str:
        parent = MoonMindRunWorkflow()
        parent._step_workspace_capture_inputs["execute"] = {
            "runtimeCapabilities": resolve_runtime_execution_capabilities(
                "codex_cli"
            ).model_dump(by_alias=True, mode="json")
        }
        parent._record_step_workspace_capture_input(
            "execute",
            {
                "runtimeCapabilities": resolve_runtime_execution_capabilities(
                    "jules"
                ).model_dump(by_alias=True, mode="json")
            },
        )
        captured = parent._step_workspace_capture_inputs["execute"]
        return await workflow.execute_activity(
            record_capture,
            captured["runtimeCapabilities"]["runtimeId"],
            start_to_close_timeout=timedelta(seconds=5),
        )


@pytest.mark.asyncio
async def test_checkpoint_authority_pre_and_post_patch_histories_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for kind, implementation, expected in (
            ("legacy", LegacyCheckpointFixture, "jules"),
            ("current", CurrentCheckpointFixture, "codex_cli"),
        ):
            queue = f"dispatch-authority-checkpoint-{kind}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_capture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run, id=queue, task_queue=queue
                )
                assert await handle.result() == expected
                histories.append(await handle.fetch_history())
    replayer = Replayer(
        workflows=[CurrentCheckpointFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
