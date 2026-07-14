import inspect
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner, Replayer

from moonmind.workflows.temporal.workflows.run import (
    GateTransitionDecision,
    RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH,
    RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
    MoonMindRunWorkflow,
    MoonMindUserWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_signals_updates import (
    mock_run_environment,
)


@workflow.defn(name="MM3238RemediationReplayFixture")
class _LegacyRemediationReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        return ["verify-1", "verify-1"]


@workflow.defn(name="MM3238RemediationReplayFixture")
class _CurrentRemediationReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        if not workflow.patched(RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH):
            legacy_retry_allowed = (
                MoonMindRunWorkflow._gate_transition_allows_review_retry(
                    plan_routed_moonspec_remediation_enabled=False,
                    transition=GateTransitionDecision(
                        disposition="accept",
                        routing_disposition="stop_at_control_gate",
                        reason_code="no_remediation_successor",
                    ),
                )
            )
            return ["verify-1", "verify-1" if legacy_retry_allowed else "stop"]
        nodes = [
            {
                "id": "remediate-1",
                "annotations": {
                    "issueImplementRole": "moonspec-remediation",
                    "moonSpecRemediationAttempt": 1,
                    "moonSpecRemediationMaxAttempts": 2,
                },
            },
            {
                "id": "verify-1",
                "annotations": {
                    "issueImplementRole": "moonspec-verification-gate",
                    "moonSpecRemediationAttempt": 1,
                    "moonSpecRemediationMaxAttempts": 2,
                },
            },
            {
                "id": "remediate-2",
                "annotations": {
                    "issueImplementRole": "moonspec-remediation",
                    "moonSpecRemediationAttempt": 2,
                    "moonSpecRemediationMaxAttempts": 2,
                },
            },
            {
                "id": "verify-2",
                "annotations": {
                    "issueImplementRole": "moonspec-verification-gate",
                    "moonSpecRemediationAttempt": 2,
                    "moonSpecRemediationMaxAttempts": 2,
                    "moonSpecFinalRemediationGate": True,
                },
            },
        ]
        decision = MoonMindRunWorkflow()._resolve_gate_transition(
            verdict=type(
                "VerifierResult",
                (),
                {
                    "verdict": "ADDITIONAL_WORK_NEEDED",
                    "recoverable_in_current_runtime": True,
                },
            )(),
            ordered_nodes=nodes,
            current_index=1,
        )
        assert decision.successor is not None
        return ["verify-1", decision.successor.logical_step_id]


@workflow.defn(name="MMCanonicalNoCommitReplayFixture")
class _LegacyCanonicalNoCommitReplayFixture:
    @workflow.run
    async def run(self) -> list[Any]:
        return ["skipped", "failed", True]


@workflow.defn(name="MMCanonicalNoCommitReplayFixture")
class _CurrentCanonicalNoCommitReplayFixture:
    @workflow.run
    async def run(self) -> list[Any]:
        run_workflow = MoonMindRunWorkflow()
        run_workflow._canonical_no_commit_outcome_enabled = workflow.patched(
            RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH
        )
        parameters = {
            "publishMode": "pr",
            "workflow": {
                "tool": {"type": "skill", "name": "auto"},
                "skill": {"name": "auto"},
                "appliedStepTemplates": [
                    {"slug": "github-issue-implement", "version": "1.0.0"},
                ],
            },
        }
        result = {
            "outputs": {
                "push_status": "no_commits",
                "push_branch": "feature/no-op",
                "push_base_ref": "origin/main",
                "push_commit_count": 0,
            }
        }
        run_workflow._record_execution_context(
            node_id="create-pull-request",
            execution_result=result,
        )
        run_workflow._record_publish_result(
            parameters=parameters,
            execution_result=result,
        )
        status, _message, publish_failure = (
            run_workflow._determine_publish_completion(parameters=parameters)
        )
        return [run_workflow._publish_status, status, publish_failure]

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


def test_canonical_no_commit_patch_is_snapshotted_at_workflow_start() -> None:
    source = inspect.getsource(MoonMindRunWorkflow.run)
    patch_name = "RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH"

    assert RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH.endswith("-v1")
    assert source.count(patch_name) == 1
    assert source.index(patch_name) < source.index(
        "Starting MoonMind.UserWorkflow workflow"
    )


@pytest.mark.asyncio
async def test_moonspec_remediation_pre_and_post_patch_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3238-legacy-replay",
            workflows=[_LegacyRemediationReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyRemediationReplayFixture.run,
                id="test-mm3238-legacy-history",
                task_queue="test-mm3238-legacy-replay",
            )
            assert await legacy.result() == ["verify-1", "verify-1"]
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3238-current-replay",
            workflows=[_CurrentRemediationReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentRemediationReplayFixture.run,
                id="test-mm3238-current-history",
                task_queue="test-mm3238-current-replay",
            )
            current_commands = await current.result()
            current_history = await current.fetch_history()

    assert current_commands == ["verify-1", "remediate-2"]
    assert current_commands.count("verify-1") == 1
    replayer = Replayer(
        workflows=[_CurrentRemediationReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_canonical_no_commit_pre_and_post_patch_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-no-commit-legacy-replay",
            workflows=[_LegacyCanonicalNoCommitReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyCanonicalNoCommitReplayFixture.run,
                id="test-no-commit-legacy-history",
                task_queue="test-no-commit-legacy-replay",
            )
            assert await legacy.result() == ["skipped", "failed", True]
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-no-commit-current-replay",
            workflows=[_CurrentCanonicalNoCommitReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentCanonicalNoCommitReplayFixture.run,
                id="test-no-commit-current-history",
                task_queue="test-no-commit-current-replay",
            )
            assert await current.result() == ["not_required", "no_commit", False]
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentCanonicalNoCommitReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)
