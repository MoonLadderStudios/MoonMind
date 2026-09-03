import inspect
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.cutover import CutoverPhase, select_runtime
from moonmind.workflows.executions.repository_contract import (
    repository_name_from_value,
)
from moonmind.workflows.skills.approval_policy import StepGateResult
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationContinuationDecision,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    apply_continuation_decision,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import (
    RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH,
    RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
    RUN_CANONICAL_GIT_REPOSITORY_PROJECTION_PATCH,
    RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH,
    RUN_LATE_REMEDIATION_HEAD_ATTEMPT_ORDINAL_PATCH,
    RUN_MANAGED_SESSION_CHECKPOINT_LOCATOR_PATCH,
    RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH,
    RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH,
    RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
    RUN_OMNIGENT_INITIAL_VERIFICATION_CHECKPOINT_RESTORE_PATCH,
    RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
    RUN_REMEDIATION_CONTINUE_MANAGED_SESSION_PATCH,
    RUN_REMEDIATION_LOOP_ARTIFACT_REF_NORMALIZATION_PATCH,
    RUN_REMEDIATION_LOOP_CONTINUE_AS_NEW_PATCH,
    RUN_REMEDIATION_MANAGED_SESSION_SOURCE_IDENTITY_PATCH,
    RUN_REFRESH_MOONSPEC_BLOCK_AFTER_REMEDIATION_DECISION_PATCH,
    RUN_WORKFLOW_HEADLESS_REMEDIATION_PATCH,
    RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH,
    GateTransitionDecision,
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


def _late_remediation_head_fixture() -> MoonMindRunWorkflow:
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._remediation_loop_spec = RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": "loop",
            "remediationTool": {
                "type": "agent_runtime",
                "name": "codex_cli",
                "inputs": {"instructions": "Remediate."},
            },
            "verificationTool": {
                "type": "agent_runtime",
                "name": "codex_cli",
                "inputs": {"instructions": "Verify."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 2},
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )
    workflow_instance._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        attemptOrdinal=1,
        phase=RemediationLoopPhase.VERIFICATION_PENDING,
        consumedBudgets=ConsumedRemediationBudgets(attempts=1),
    )
    workflow_instance._step_checkpoint_workspace_evidence_by_boundary = {
        "verification-1": {
            "before_publication": {
                "checkpointRef": "artifact://workspace/C1",
                "workspaceKind": "worktree_archive",
                "workspaceDigest": "sha256:c1",
                "workspaceIdentityDigest": "sha256:" + ("c" * 64),
                "checkpointManifestRef": "artifact://manifest/C1",
            }
        }
    }
    return workflow_instance


@workflow.defn(name="MMLateRemediationHeadAttemptReplayFixture")
class _LegacyLateRemediationHeadAttemptReplayFixture:
    @workflow.run
    async def run(self) -> int:
        workflow.patched(RUN_OMNIGENT_INITIAL_VERIFICATION_CHECKPOINT_RESTORE_PATCH)
        return 0


@workflow.defn(name="MMLateRemediationHeadAttemptReplayFixture")
class _CurrentLateRemediationHeadAttemptReplayFixture:
    @workflow.run
    async def run(self) -> int:
        workflow_instance = _late_remediation_head_fixture()
        head = workflow_instance._initialize_remediation_head_from_canonical_checkpoint(
            logical_step_id="verification-1",
            gate_result_ref="artifact://verification/V1",
            verdict="ADDITIONAL_WORK_NEEDED",
        )
        assert head is not None
        return head.head_attempt_ordinal


@workflow.defn(name="MMHeadlessRemediationExecutionReplayFixture")
class _LegacyHeadlessRemediationExecutionReplayFixture:
    @workflow.run
    async def run(self) -> bool:
        workflow.patched(RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH)
        workflow.patched(RUN_WORKFLOW_HEADLESS_REMEDIATION_PATCH)
        return True


@workflow.defn(name="MMHeadlessRemediationExecutionReplayFixture")
class _CurrentHeadlessRemediationExecutionReplayFixture:
    @workflow.run
    async def run(self) -> bool:
        workflow.patched(RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH)
        workflow.patched(RUN_WORKFLOW_HEADLESS_REMEDIATION_PATCH)
        workflow_instance = MoonMindRunWorkflow()
        workflow_instance._remediation_workspace_head = None
        return workflow_instance._remediation_workspace_materialization_required(
            {
                "id": "remediation-1",
                "annotations": {
                    "issueImplementRole": "moonspec-remediation",
                },
            }
        )


@workflow.defn(name="MMManagedStatusRolloutTimeoutReplayFixture")
class _LegacyManagedStatusRolloutTimeoutReplayFixture:
    @workflow.run
    async def run(self) -> list[int]:
        return [180, 180]


@workflow.defn(name="MMManagedStatusRolloutTimeoutReplayFixture")
class _CurrentManagedStatusRolloutTimeoutReplayFixture:
    @workflow.run
    async def run(self) -> list[int]:
        uncapped = MoonMindAgentRun._managed_status_schedule_to_close_override()
        capped = MoonMindAgentRun._managed_status_schedule_to_close_override(
            remaining_budget_seconds=45,
        )
        return [
            int(uncapped.total_seconds()) if uncapped is not None else 600,
            int(capped.total_seconds()) if capped is not None else 600,
        ]


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


@workflow.defn(name="MMCanonicalGitRepositoryProjectionReplayFixture")
class _LegacyCanonicalGitRepositoryProjectionReplayFixture:
    @workflow.run
    async def run(self, _repository: dict[str, Any]) -> str:
        return ""


@workflow.defn(name="MMCanonicalGitRepositoryProjectionReplayFixture")
class _CurrentCanonicalGitRepositoryProjectionReplayFixture:
    @workflow.run
    async def run(self, repository: dict[str, Any]) -> str:
        if not workflow.patched(RUN_CANONICAL_GIT_REPOSITORY_PROJECTION_PATCH):
            return ""
        return repository_name_from_value(repository, provider="git")


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


def _agent_profile_snapshot_replay_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agentProfileRef": "omnigent-bootstrap-default@1",
            "executionProfileRef": "omnigent-codex@1",
            "agent": {"agentId": "upstream-codex-agent"},
        },
        {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": "omnigent-bootstrap-default",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
            "providerProfileRef": "codex-openai-oauth",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agentId": "upstream-codex-agent",
            "document": {
                "endpointRef": "default",
                "harness": "codex-native",
            },
        },
    )


@workflow.defn(name="OmnigentAgentProfileSnapshotCompilerReplayFixture")
class _LegacyOmnigentAgentProfileSnapshotCompilerReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        authored, _snapshot = _agent_profile_snapshot_replay_inputs()
        return authored


@workflow.defn(name="OmnigentAgentProfileSnapshotCompilerReplayFixture")
class _CurrentOmnigentAgentProfileSnapshotCompilerReplayFixture:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        authored, snapshot = _agent_profile_snapshot_replay_inputs()
        if not workflow.patched(
            RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH
        ):
            return authored
        return MoonMindRunWorkflow()._compile_agent_profile_snapshot_omnigent_selection(
            authored,
            snapshot=snapshot,
            execution_profile_ref="codex-openai-oauth",
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


@pytest.mark.asyncio
async def test_repository_target_input_shapes_execute_and_replay(
    mock_run_environment,  # noqa: F811
) -> None:
    """Canonical repository targets run while legacy scalar histories still replay."""

    repository_parameters = [
        {"repository": "MoonLadderStudios/Tactics"},
        {
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "MoonLadderStudios/Tactics"},
                "branch": {"name": "main"},
            }
        },
    ]
    histories = []

    async with await WorkflowEnvironment.start_time_skipping() as env:
        for index, initial_parameters in enumerate(repository_parameters):
            task_queue = f"test-repository-target-replay-{index}"
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[MoonMindUserWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "initial_parameters": initial_parameters,
                        "plan_artifact_ref": "ref-123",
                    },
                    id=f"test-repository-target-history-{index}",
                    task_queue=task_queue,
                )
                assert (await handle.result())["status"] == "success"
                histories.append(await handle.fetch_history())

    replayer = Replayer(
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_canonical_repository_projection_pre_and_post_patch_histories_replay(
) -> None:
    repository = {
        "provider": "git",
        "connectionRef": "repository-connection:git-default",
        "repository": {"name": "MoonLadderStudios/Tactics"},
        "branch": {"name": "main"},
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-canonical-repository-projection-legacy",
            workflows=[_LegacyCanonicalGitRepositoryProjectionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyCanonicalGitRepositoryProjectionReplayFixture.run,
                repository,
                id="test-canonical-repository-projection-legacy",
                task_queue="test-canonical-repository-projection-legacy",
            )
            assert await legacy.result() == ""
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-canonical-repository-projection-current",
            workflows=[_CurrentCanonicalGitRepositoryProjectionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentCanonicalGitRepositoryProjectionReplayFixture.run,
                repository,
                id="test-canonical-repository-projection-current",
                task_queue="test-canonical-repository-projection-current",
            )
            assert await current.result() == "MoonLadderStudios/Tactics"
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentCanonicalGitRepositoryProjectionReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


def test_canonical_repository_projection_patch_is_snapshotted_before_input_parse(
) -> None:
    source = inspect.getsource(MoonMindRunWorkflow.run)
    patch_name = "RUN_CANONICAL_GIT_REPOSITORY_PROJECTION_PATCH"

    assert RUN_CANONICAL_GIT_REPOSITORY_PROJECTION_PATCH.endswith("-v1")
    assert source.count(patch_name) == 1
    assert source.index(patch_name) < source.index("_initialize_from_payload")


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
async def test_late_remediation_head_attempt_ordinal_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-late-remediation-head-legacy-replay",
            workflows=[_LegacyLateRemediationHeadAttemptReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy_handle = await env.client.start_workflow(
                _LegacyLateRemediationHeadAttemptReplayFixture.run,
                id="test-late-remediation-head-legacy",
                task_queue="test-late-remediation-head-legacy-replay",
            )
            legacy_result = await legacy_handle.result()
            legacy_history = await legacy_handle.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-late-remediation-head-current-replay",
            workflows=[_CurrentLateRemediationHeadAttemptReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current_handle = await env.client.start_workflow(
                _CurrentLateRemediationHeadAttemptReplayFixture.run,
                id="test-late-remediation-head-current",
                task_queue="test-late-remediation-head-current-replay",
            )
            current_result = await current_handle.result()
            current_history = await current_handle.fetch_history()

    assert legacy_result == 0
    assert current_result == 1
    replayer = Replayer(
        workflows=[_CurrentLateRemediationHeadAttemptReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_headless_remediation_execution_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-headless-remediation-legacy-replay",
            workflows=[_LegacyHeadlessRemediationExecutionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyHeadlessRemediationExecutionReplayFixture.run,
                id="test-headless-remediation-legacy",
                task_queue="test-headless-remediation-legacy-replay",
            )
            assert await legacy.result() is True
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-headless-remediation-current-replay",
            workflows=[_CurrentHeadlessRemediationExecutionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentHeadlessRemediationExecutionReplayFixture.run,
                id="test-headless-remediation-current",
                task_queue="test-headless-remediation-current-replay",
            )
            assert await current.result() is False
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentHeadlessRemediationExecutionReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


@pytest.mark.asyncio
async def test_managed_status_rollout_timeout_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-managed-status-timeout-legacy-replay",
            workflows=[_LegacyManagedStatusRolloutTimeoutReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyManagedStatusRolloutTimeoutReplayFixture.run,
                id="test-managed-status-timeout-legacy",
                task_queue="test-managed-status-timeout-legacy-replay",
            )
            assert await legacy.result() == [180, 180]
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-managed-status-timeout-current-replay",
            workflows=[_CurrentManagedStatusRolloutTimeoutReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentManagedStatusRolloutTimeoutReplayFixture.run,
                id="test-managed-status-timeout-current",
                task_queue="test-managed-status-timeout-current-replay",
            )
            assert await current.result() == [600, 45]
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentManagedStatusRolloutTimeoutReplayFixture],
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
async def test_agent_profile_snapshot_compiler_histories_replay() -> None:
    authored, _snapshot = _agent_profile_snapshot_replay_inputs()
    compiled = {
        "endpointRef": "default",
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "agent": {
            "harnessOverride": "codex-native",
            "agentId": "upstream-codex-agent",
        },
        "agentProfileRef": {
            "profileId": "omnigent-bootstrap-default",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        },
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-agent-profile-snapshot-legacy-replay",
            workflows=[_LegacyOmnigentAgentProfileSnapshotCompilerReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacyOmnigentAgentProfileSnapshotCompilerReplayFixture.run,
                id="test-agent-profile-snapshot-legacy-history",
                task_queue="test-agent-profile-snapshot-legacy-replay",
            )
            assert await legacy.result() == authored
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-agent-profile-snapshot-current-replay",
            workflows=[_CurrentOmnigentAgentProfileSnapshotCompilerReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentOmnigentAgentProfileSnapshotCompilerReplayFixture.run,
                id="test-agent-profile-snapshot-current-history",
                task_queue="test-agent-profile-snapshot-current-replay",
            )
            assert await current.result() == compiled
            current_history = await current.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentOmnigentAgentProfileSnapshotCompilerReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


def test_github_3518_cutover_selection_never_runs_inside_workflow_code() -> None:
    """MoonLadderStudios/MoonMind#3518: keep runtime selection replay-safe.

    ``select_runtime``/``effective_phase`` read process env and a mounted
    evidence file, so they are non-deterministic submission-boundary side
    effects.  Recording ``runtimeCutover`` into the workflow start payload is
    correct; invoking the cutover decision from replayed workflow code would
    both break determinism and reintroduce an in-workflow fallback path that
    could silently override an explicit Omnigent selection (AC8 + AC12).  This
    guard fails fast if a future change moves that decision into the workflow.
    """

    from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
    from moonmind.workflows.temporal.workflows import run as run_module

    for module in (run_module, agent_run_module):
        source = inspect.getsource(module)
        assert "select_runtime" not in source, module.__name__
        assert "effective_phase" not in source, module.__name__
        assert "omnigent.cutover" not in source, module.__name__


@pytest.mark.asyncio
async def test_github_3518_cutover_runtime_parameter_histories_replay(
    mock_run_environment,  # noqa: F811
) -> None:
    """MoonLadderStudios/MoonMind#3518: recorded histories replay across cutover.

    The cutover adds a ``runtimeCutover`` evidence block to the workflow start
    payload and can change the resolved ``targetRuntime`` default from
    ``codex_cli`` to ``omnigent``.  In-flight runs started before the cutover
    landed have neither key; runs started after it carry both.  A single
    (mixed-version) current worker must replay both recorded histories without a
    non-determinism error, proving the start-payload shape difference is passive
    metadata rather than a divergent command source.
    """

    # Faithful post-cutover evidence: the exact dict persisted into
    # ``initial_parameters['runtimeCutover']`` by the executions router when the
    # Create default has advanced to Omnigent.
    post_cutover_selection = select_runtime(
        authored_runtime=None,
        configured_default="codex_cli",
        phase=CutoverPhase.CREATE_DEFAULT,
        submission_kind="create",
    )
    assert post_cutover_selection.runtime_id == "omnigent"

    pre_cutover_parameters: dict[str, Any] = {"targetRuntime": "codex_cli"}
    post_cutover_parameters: dict[str, Any] = {
        "targetRuntime": post_cutover_selection.runtime_id,
        "runtimeCutover": post_cutover_selection.as_dict(),
    }

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3518-pre-cutover-replay",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            pre_cutover = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                {
                    "workflow_type": "MoonMind.UserWorkflow",
                    "initial_parameters": pre_cutover_parameters,
                    "plan_artifact_ref": "ref-123",
                },
                id="test-mm3518-pre-cutover-history",
                task_queue="test-mm3518-pre-cutover-replay",
            )
            assert (await pre_cutover.result())["status"] == "success"
            pre_cutover_history = await pre_cutover.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3518-post-cutover-replay",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            post_cutover = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                {
                    "workflow_type": "MoonMind.UserWorkflow",
                    "initial_parameters": post_cutover_parameters,
                    "plan_artifact_ref": "ref-123",
                },
                id="test-mm3518-post-cutover-history",
                task_queue="test-mm3518-post-cutover-replay",
            )
            assert (await post_cutover.result())["status"] == "success"
            post_cutover_history = await post_cutover.fetch_history()

    # The current worker replays both the pre-cutover (no runtimeCutover) and
    # post-cutover (runtimeCutover present) histories.
    replayer = Replayer(
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(pre_cutover_history)
    await replayer.replay_workflow(post_cutover_history)


@activity.defn(name="mm3518_record_runtime_selection")
async def _mm3518_record_runtime_selection(target_runtime: str) -> str:
    """Faithful recorded command: both the deployed pre-cutover worker and the
    current worker persist the resolved ``targetRuntime`` through this same
    activity, so the runtime value lands in recorded history as a passive
    command input rather than being mocked away."""

    return f"recorded:{target_runtime}"


@workflow.defn(name="MM3518RuntimeCutoverReplayFixture")
class _LegacyPreCutoverRuntimeReplayFixture:
    """Faithful *deployed pre-cutover* worker.

    The start payload it receives has no ``runtimeCutover`` block (the cutover
    had not landed); the runtime comes only from ``targetRuntime`` and is
    recorded through a real, unmocked activity command.  Workflow code never
    consults the submission-boundary cutover decision.
    """

    @workflow.run
    async def run(self, start: dict[str, Any]) -> dict[str, Any]:
        params = dict(start.get("initial_parameters") or {})
        assert "runtimeCutover" not in params
        recorded = await workflow.execute_activity(
            _mm3518_record_runtime_selection,
            params.get("targetRuntime", "codex_cli"),
            start_to_close_timeout=timedelta(seconds=10),
        )
        return {"recordedRuntime": recorded}


@workflow.defn(name="MM3518RuntimeCutoverReplayFixture")
class _CurrentRuntimeCutoverReplayFixture:
    """Current worker: identical workflow code and recorded command sequence.

    The post-cutover start payload additionally carries the ``runtimeCutover``
    evidence block, which stays passive metadata — it is never used to drive a
    command or branch inside workflow code.
    """

    @workflow.run
    async def run(self, start: dict[str, Any]) -> dict[str, Any]:
        params = dict(start.get("initial_parameters") or {})
        recorded = await workflow.execute_activity(
            _mm3518_record_runtime_selection,
            params.get("targetRuntime", "codex_cli"),
            start_to_close_timeout=timedelta(seconds=10),
        )
        return {"recordedRuntime": recorded}


@pytest.mark.asyncio
async def test_github_3518_pre_cutover_runtime_history_replays_on_current_worker() -> None:
    """MoonLadderStudios/MoonMind#3518: a faithful pre-cutover history replays.

    ``test_github_3518_cutover_runtime_parameter_histories_replay`` generates
    both histories from the current ``MoonMindUserWorkflow`` with the planning
    and execution stages mocked out, so it proves only that the current mocked
    workflow replays its own histories.  This test closes that gap: the legacy
    history is generated by a dedicated *pre-cutover* fixture in the deployed
    shape (no ``runtimeCutover`` in the start payload) whose recorded command
    sequence is driven by a real, unmocked activity.  Replaying that faithful
    pre-cutover history on the current worker proves the cutover start-payload
    difference is passive metadata, not a divergent recorded command source.
    """

    post_cutover_selection = select_runtime(
        authored_runtime=None,
        configured_default="codex_cli",
        phase=CutoverPhase.CREATE_DEFAULT,
        submission_kind="create",
    )
    assert post_cutover_selection.runtime_id == "omnigent"

    pre_cutover_start: dict[str, Any] = {
        "workflow_type": "MoonMind.UserWorkflow",
        "initial_parameters": {"targetRuntime": "codex_cli"},
        "plan_artifact_ref": "ref-123",
    }
    post_cutover_start: dict[str, Any] = {
        "workflow_type": "MoonMind.UserWorkflow",
        "initial_parameters": {
            "targetRuntime": post_cutover_selection.runtime_id,
            "runtimeCutover": post_cutover_selection.as_dict(),
        },
        "plan_artifact_ref": "ref-123",
    }

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3518-pre-cutover-fixture",
            workflows=[_LegacyPreCutoverRuntimeReplayFixture],
            activities=[_mm3518_record_runtime_selection],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            pre_cutover = await env.client.start_workflow(
                _LegacyPreCutoverRuntimeReplayFixture.run,
                pre_cutover_start,
                id="test-mm3518-pre-cutover-fixture-history",
                task_queue="test-mm3518-pre-cutover-fixture",
            )
            assert (await pre_cutover.result())["recordedRuntime"] == "recorded:codex_cli"
            pre_cutover_history = await pre_cutover.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3518-current-fixture",
            workflows=[_CurrentRuntimeCutoverReplayFixture],
            activities=[_mm3518_record_runtime_selection],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            post_cutover = await env.client.start_workflow(
                _CurrentRuntimeCutoverReplayFixture.run,
                post_cutover_start,
                id="test-mm3518-current-fixture-history",
                task_queue="test-mm3518-current-fixture",
            )
            assert (await post_cutover.result())["recordedRuntime"] == "recorded:omnigent"
            post_cutover_history = await post_cutover.fetch_history()

    # The current worker replays the faithful pre-cutover history (generated by
    # the deployed-shape worker) and its own post-cutover history without a
    # non-determinism error.
    replayer = Replayer(
        workflows=[_CurrentRuntimeCutoverReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(pre_cutover_history)
    await replayer.replay_workflow(post_cutover_history)


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
