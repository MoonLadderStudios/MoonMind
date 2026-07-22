import inspect
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner, Replayer

from moonmind.workflows.skills.approval_policy import StepGateResult
from moonmind.workflows.temporal.workflows.run import (
    GateTransitionDecision,
    RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH,
    RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
    RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH,
    RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH,
    RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
    RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
    MoonMindRunWorkflow,
    MoonMindUserWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_signals_updates import (
    mock_run_environment,  # noqa: F401
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


def _mm3379_remediation_nodes() -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "verify-initial",
            "inputs": {"selectedSkill": "moonspec-verify"},
        }
    ]
    for attempt in range(1, 3):
        nodes.extend(
            [
                {
                    "id": f"remediate-{attempt}",
                    "inputs": {
                        "annotations": {
                            "issueImplementRole": "moonspec-remediation",
                            "moonSpecRemediationAttempt": attempt,
                            "moonSpecRemediationMaxAttempts": 2,
                        }
                    },
                },
                {
                    "id": f"verify-{attempt}",
                    "inputs": {
                        "selectedSkill": "moonspec-verify",
                        "annotations": {
                            "issueImplementRole": "moonspec-verification-gate",
                            "moonSpecRemediationAttempt": attempt,
                            "moonSpecRemediationMaxAttempts": 2,
                            "moonSpecFinalRemediationGate": attempt == 2,
                        },
                    },
                },
            ]
        )
    return nodes


@workflow.defn(name="MM3379NoProgressBudgetReplayFixture")
class _LegacyNoProgressBudgetReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        # Patch-marker shape of the release that stopped the example after its
        # first unchanged post-remediation verification.
        workflow.patched(RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH)
        workflow.patched(RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH)
        workflow.patched(RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH)
        return ["verify-initial", "stop"]


@workflow.defn(name="MM3379NoProgressBudgetReplayFixture")
class _CurrentNoProgressBudgetReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        run_workflow = MoonMindRunWorkflow()
        nodes = _mm3379_remediation_nodes()
        gate = StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            feedback="Unchanged sparse remaining-work summary.",
        )
        run_workflow._bounded_story_loop_continuation_decision(
            logical_step_id="verify-initial",
            gate_result=gate,
            gate_result_ref="artifact://gate/initial",
            ordered_nodes=nodes,
            current_index=0,
        )
        decision = run_workflow._bounded_story_loop_continuation_decision(
            logical_step_id="verify-1",
            gate_result=gate,
            gate_result_ref="artifact://gate/1",
            ordered_nodes=nodes,
            current_index=2,
        )
        return [
            "verify-initial",
            "remediate-2" if decision["continueLoop"] else "stop",
        ]


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


@workflow.defn(name="MM3453OmnigentCompilerReplayFixture")
class _LegacyOmnigentCompilerReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        return {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agent": {"harnessOverride": "codex-native"},
        }


@workflow.defn(name="MM3453OmnigentCompilerReplayFixture")
class _CurrentOmnigentCompilerReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        authored = {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agent": {"harnessOverride": "codex-native"},
        }
        if not workflow.patched(RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH):
            return authored
        return MoonMindRunWorkflow()._compile_authored_omnigent_selection(
            authored,
            path="workflow.omnigent",
        )

@pytest.mark.asyncio
async def test_workflow_determinism_replay(mock_run_environment):  # noqa: F811
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
async def test_no_progress_budget_pre_and_post_fix_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3379-legacy-replay",
            workflows=[_LegacyNoProgressBudgetReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyNoProgressBudgetReplayFixture.run,
                id="test-mm3379-legacy-history",
                task_queue="test-mm3379-legacy-replay",
            )
            assert await legacy.result() == ["verify-initial", "stop"]
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3379-current-replay",
            workflows=[_CurrentNoProgressBudgetReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentNoProgressBudgetReplayFixture.run,
                id="test-mm3379-current-history",
                task_queue="test-mm3379-current-replay",
            )
            assert await current.result() == ["verify-initial", "remediate-2"]
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentNoProgressBudgetReplayFixture],
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


@pytest.mark.asyncio
async def test_github_3453_pre_change_omnigent_history_replays() -> None:
    expected = {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "agent": {"harnessOverride": "codex-native"},
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3453-legacy-replay",
            workflows=[_LegacyOmnigentCompilerReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyOmnigentCompilerReplayFixture.run,
                id="test-mm3453-legacy-history",
                task_queue="test-mm3453-legacy-replay",
            )
            assert await legacy.result() == expected
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3453-current-replay",
            workflows=[_CurrentOmnigentCompilerReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentOmnigentCompilerReplayFixture.run,
                id="test-mm3453-current-history",
                task_queue="test-mm3453-current-replay",
            )
            assert await current.result() == expected
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentOmnigentCompilerReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)
