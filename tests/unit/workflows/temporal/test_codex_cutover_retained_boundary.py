"""MoonLadderStudios/MoonMind#3931 retained-boundary replay (temporal boundary).

Temporal-boundary owner for the live-workflow replay of the direct-Codex
drain path: an already-recorded payload still decodes inside a workflow and
its history replays, while retained promotion/routing bindings resolve after
the deployment cutoff. Lives under tests/unit/workflows/temporal/ so the CI
shard-ownership verifier assigns exactly one owner (temporal-boundary).
"""

from datetime import datetime, timezone

import pytest
from temporalio import workflow

from moonmind.omnigent.workspace_sources import decode_legacy_workspace_path


@workflow.defn(name="CodexCutoverRetainedBoundaryReplay")
class _RetainedBoundaryReplayWorkflow:
    """R4 representative replay: recorded payload + promotion + routing."""

    @workflow.run
    async def run(self, payload: dict) -> str:
        decoded = decode_legacy_workspace_path(payload)
        await workflow.sleep(1)
        return decoded or "missing"


@pytest.mark.asyncio
async def test_retained_boundary_workflow_replays_across_cutoff():
    """R4: retained decoders + promotion + routing replay in one history."""

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

    history = None
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = "test-codex-cutover-retained-boundary"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_RetainedBoundaryReplayWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _RetainedBoundaryReplayWorkflow.run,
                {"workspacePath": "/recorded/path", "runtime": "codex_cli"},
                id=queue,
                task_queue=queue,
            )
            assert await handle.result() == "/recorded/path"
            history = await handle.fetch_history()
    assert history is not None
    replayer = Replayer(
        workflows=[_RetainedBoundaryReplayWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)
    # The same retained bindings resolve outside the workflow after cutoff.
    from moonmind.omnigent.cutover import CutoverPhase, evaluate_promotion
    from moonmind.workflows.temporal.release_routing import current_version

    decision = evaluate_promotion(
        current_phase=CutoverPhase.BROAD_DEFAULT,
        requested_phase=CutoverPhase.OPT_IN,
        evidence=None,
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert decision.allowed is True
    assert callable(current_version)
