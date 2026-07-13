import inspect

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner, Replayer

from moonmind.workflows.temporal.workflows.run import (
    RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
    MoonMindRunWorkflow,
    MoonMindUserWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_signals_updates import (
    mock_run_environment,
)

@pytest.mark.asyncio
async def test_workflow_determinism_replay(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-replay",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                {
                    "workflow_type": "MoonMind.UserWorkflow",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-replay",
                task_queue="test-task-queue-replay",
            )
            
            result = await handle.result()
            assert result["status"] == "success"
            
            # Fetch history
            history = await handle.fetch_history()
            
            # Replay history
            replayer = Replayer(
                workflows=[MoonMindUserWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
            await replayer.replay_workflow(history)


def test_plan_routed_moonspec_patch_is_snapshotted_before_node_execution() -> None:
    """MoonLadderStudios/MoonMind#3238 keeps the cutover replay-stable."""

    source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)
    patch_name = "RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH"
    assert RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH.endswith("-v1")
    assert source.count(patch_name) == 1
    snapshot_index = source.index(patch_name)
    node_loop_index = source.index("for index, node in enumerate(ordered_nodes")
    assert snapshot_index < node_loop_index
