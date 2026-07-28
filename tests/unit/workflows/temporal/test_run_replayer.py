import inspect
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner, Replayer

from moonmind.workflows.skills.approval_policy import StepGateResult
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationContinuationDecision,
    RemediationLoopPhase,
    RemediationLoopState,
    apply_continuation_decision,
)
from moonmind.workflows.temporal.workflows.run import (
    GateTransitionDecision,
    RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH,
    RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
    RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH,
    RUN_MANAGED_SESSION_CHECKPOINT_LOCATOR_PATCH,
    RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH,
    RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
    RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
    RUN_REMEDIATION_MANAGED_SESSION_SOURCE_IDENTITY_PATCH,
    RUN_REMEDIATION_CONTINUE_MANAGED_SESSION_PATCH,
    RUN_REMEDIATION_LOOP_ARTIFACT_REF_NORMALIZATION_PATCH,
    RUN_REMEDIATION_LOOP_CONTINUE_AS_NEW_PATCH,
    RUN_REFRESH_MOONSPEC_BLOCK_AFTER_REMEDIATION_DECISION_PATCH,
    RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH,
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


@workflow.defn(name="MM3475StaticLoopCutoverReplayFixture")
class _LegacyStaticLoopCutoverReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        return ["verify-initial", "remediate-1", "verify-1"]


@workflow.defn(name="MM3475StaticLoopCutoverReplayFixture")
class _CurrentStaticLoopCutoverReplayFixture:
    @workflow.run
    async def run(self) -> list[str]:
        commands = ["verify-initial", "remediate-1", "verify-1"]
        if workflow.patched(RUN_REMEDIATION_LOOP_CONTINUE_AS_NEW_PATCH):
            return ["controller", "remediation:1", "verification:1"]
        return commands


@workflow.defn(name="MMRemediationArtifactRefReplayFixture")
class _LegacyRemediationArtifactRefReplayFixture:
    @workflow.run
    async def run(self) -> str:
        return "art_gate_result"


@workflow.defn(name="MMRemediationArtifactRefReplayFixture")
class _CurrentRemediationArtifactRefReplayFixture:
    @workflow.run
    async def run(self) -> str:
        artifact_ref = "art_gate_result"
        if workflow.patched(
            RUN_REMEDIATION_LOOP_ARTIFACT_REF_NORMALIZATION_PATCH
        ):
            return (
                MoonMindRunWorkflow._bounded_story_loop_artifact_ref(artifact_ref)
                or ""
            )
        return artifact_ref


@workflow.defn(name="MMWorkflowOwnedRemediationHeadReplayFixture")
class _LegacyWorkflowOwnedRemediationHeadReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        return {"state": {"workspaceHeadRef": "artifact://workspace/C1"}}


@workflow.defn(name="MMWorkflowOwnedRemediationHeadReplayFixture")
class _CurrentWorkflowOwnedRemediationHeadReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        continuation: dict[str, Any] = {
            "state": {"workspaceHeadRef": "artifact://workspace/C1"}
        }
        if workflow.patched(RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH):
            continuation["workspaceHead"] = {
                "headCheckpointRef": "artifact://workspace/C1",
                "headWorkspaceDigest": "sha256:c1",
            }
        return continuation


@workflow.defn(name="MMManagedSessionCheckpointLocatorReplayFixture")
class _LegacyManagedSessionCheckpointLocatorReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        return {
            "locator": "locator_deferred",
            "bindingCarried": False,
            "sourceIdentityCarried": False,
        }


@workflow.defn(name="MMManagedSessionCheckpointLocatorReplayFixture")
class _CurrentManagedSessionCheckpointLocatorReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        locator = (
            "binding_locator"
            if workflow.patched(RUN_MANAGED_SESSION_CHECKPOINT_LOCATOR_PATCH)
            else "locator_deferred"
        )
        binding_carried = workflow.patched(
            RUN_REMEDIATION_CONTINUE_MANAGED_SESSION_PATCH
        )
        source_identity_carried = workflow.patched(
            RUN_REMEDIATION_MANAGED_SESSION_SOURCE_IDENTITY_PATCH
        )
        return {
            "locator": locator,
            "bindingCarried": binding_carried,
            "sourceIdentityCarried": source_identity_carried,
        }


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


def _mm3542_apply_final_verifier_decision(
    run_workflow: MoonMindRunWorkflow,
) -> str | None:
    """Apply the final verifier while retaining the pre-decision block."""

    run_workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
    run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="issue-implementation-remediation",
        attemptOrdinal=1,
        phase=RemediationLoopPhase.CONTINUATION_DECIDING,
        consumedBudgets=ConsumedRemediationBudgets(attempts=1),
        continuationDecisionRef="artifact://decision/previous",
        latestVerdict="ADDITIONAL_WORK_NEEDED",
    )
    blocking_reason = run_workflow._blocking_moonspec_gate_reason()
    decision = RemediationContinuationDecision(
        loopId="issue-implementation-remediation",
        currentAttempt=1,
        verdict="FULLY_IMPLEMENTED",
        continueLoop=False,
        reason="The final verifier approved publication.",
        nextPhase=RemediationLoopPhase.ACCEPTED,
        gateResultRef="artifact://verification/final",
    )
    run_workflow._remediation_loop_state = apply_continuation_decision(
        run_workflow._remediation_loop_state,
        decision=decision,
        decision_ref="artifact://decision/final",
    )
    return blocking_reason


@workflow.defn(name="MM3542FinalVerifierGateReplayFixture")
class _LegacyFinalVerifierGateReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        run_workflow = MoonMindRunWorkflow()
        blocking_reason = _mm3542_apply_final_verifier_decision(run_workflow)
        return {
            "latestVerdict": run_workflow._remediation_loop_state.latest_verdict,
            "publicationBlocked": bool(blocking_reason),
        }


@workflow.defn(name="MM3542FinalVerifierGateReplayFixture")
class _CurrentFinalVerifierGateReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        run_workflow = MoonMindRunWorkflow()
        blocking_reason = _mm3542_apply_final_verifier_decision(run_workflow)
        if workflow.patched(
            RUN_REFRESH_MOONSPEC_BLOCK_AFTER_REMEDIATION_DECISION_PATCH
        ):
            blocking_reason = run_workflow._blocking_moonspec_gate_reason()
        return {
            "latestVerdict": run_workflow._remediation_loop_state.latest_verdict,
            "publicationBlocked": bool(blocking_reason),
        }


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
async def test_static_remediation_history_replays_across_loop_schema_cutover() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3475-static-history",
            workflows=[_LegacyStaticLoopCutoverReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyStaticLoopCutoverReplayFixture.run,
                id="test-mm3475-static-history",
                task_queue="test-mm3475-static-history",
            )
            assert await legacy.result() == [
                "verify-initial",
                "remediate-1",
                "verify-1",
            ]
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3475-controller-history",
            workflows=[_CurrentStaticLoopCutoverReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentStaticLoopCutoverReplayFixture.run,
                id="test-mm3475-controller-history",
                task_queue="test-mm3475-controller-history",
            )
            assert await current.result() == [
                "controller",
                "remediation:1",
                "verification:1",
            ]
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentStaticLoopCutoverReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_remediation_artifact_ref_normalization_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-remediation-artifact-ref-legacy-replay",
            workflows=[_LegacyRemediationArtifactRefReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_handle = await env.client.start_workflow(
                _LegacyRemediationArtifactRefReplayFixture.run,
                id="test-remediation-artifact-ref-legacy",
                task_queue="test-remediation-artifact-ref-legacy-replay",
            )
            assert await legacy_handle.result() == "art_gate_result"
            legacy_history = await legacy_handle.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-remediation-artifact-ref-current-replay",
            workflows=[_CurrentRemediationArtifactRefReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current_handle = await env.client.start_workflow(
                _CurrentRemediationArtifactRefReplayFixture.run,
                id="test-remediation-artifact-ref-current",
                task_queue="test-remediation-artifact-ref-current-replay",
            )
            assert await current_handle.result() == "artifact://art_gate_result"
            current_history = await current_handle.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentRemediationArtifactRefReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_workflow_owned_remediation_head_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-remediation-head-legacy-replay",
            workflows=[_LegacyWorkflowOwnedRemediationHeadReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_handle = await env.client.start_workflow(
                _LegacyWorkflowOwnedRemediationHeadReplayFixture.run,
                id="test-remediation-head-legacy",
                task_queue="test-remediation-head-legacy-replay",
            )
            legacy_result = await legacy_handle.result()
            legacy_history = await legacy_handle.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-remediation-head-current-replay",
            workflows=[_CurrentWorkflowOwnedRemediationHeadReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current_handle = await env.client.start_workflow(
                _CurrentWorkflowOwnedRemediationHeadReplayFixture.run,
                id="test-remediation-head-current",
                task_queue="test-remediation-head-current-replay",
            )
            current_result = await current_handle.result()
            current_history = await current_handle.fetch_history()

    assert "workspaceHead" not in legacy_result
    assert current_result["workspaceHead"]["headCheckpointRef"] == (
        "artifact://workspace/C1"
    )
    replayer = Replayer(
        workflows=[_CurrentWorkflowOwnedRemediationHeadReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_managed_session_checkpoint_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-managed-session-locator-legacy-replay",
            workflows=[_LegacyManagedSessionCheckpointLocatorReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_handle = await env.client.start_workflow(
                _LegacyManagedSessionCheckpointLocatorReplayFixture.run,
                id="test-managed-session-locator-legacy",
                task_queue="test-managed-session-locator-legacy-replay",
            )
            assert await legacy_handle.result() == {
                "locator": "locator_deferred",
                "bindingCarried": False,
                "sourceIdentityCarried": False,
            }
            legacy_history = await legacy_handle.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-managed-session-locator-current-replay",
            workflows=[_CurrentManagedSessionCheckpointLocatorReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current_handle = await env.client.start_workflow(
                _CurrentManagedSessionCheckpointLocatorReplayFixture.run,
                id="test-managed-session-locator-current",
                task_queue="test-managed-session-locator-current-replay",
            )
            assert await current_handle.result() == {
                "locator": "binding_locator",
                "bindingCarried": True,
                "sourceIdentityCarried": True,
            }
            current_history = await current_handle.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentManagedSessionCheckpointLocatorReplayFixture],
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


@pytest.mark.asyncio
async def test_final_verifier_gate_pre_and_post_patch_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3542-final-verifier-legacy",
            workflows=[_LegacyFinalVerifierGateReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_handle = await env.client.start_workflow(
                _LegacyFinalVerifierGateReplayFixture.run,
                id="test-mm3542-final-verifier-legacy",
                task_queue="test-mm3542-final-verifier-legacy",
            )
            legacy_result = await legacy_handle.result()
            legacy_history = await legacy_handle.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3542-final-verifier-current",
            workflows=[_CurrentFinalVerifierGateReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current_handle = await env.client.start_workflow(
                _CurrentFinalVerifierGateReplayFixture.run,
                id="test-mm3542-final-verifier-current",
                task_queue="test-mm3542-final-verifier-current",
            )
            current_result = await current_handle.result()
            current_history = await current_handle.fetch_history()

    assert legacy_result == {
        "latestVerdict": "FULLY_IMPLEMENTED",
        "publicationBlocked": True,
    }
    assert current_result == {
        "latestVerdict": "FULLY_IMPLEMENTED",
        "publicationBlocked": False,
    }
    replayer = Replayer(
        workflows=[_CurrentFinalVerifierGateReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)
