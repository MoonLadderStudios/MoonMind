from __future__ import annotations

import pytest

from moonmind.workflows.skills.approval_policy import StepGateResult
from moonmind.workflows.temporal.bounded_story_loop import LoopAttempt, TypedGateResult
from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    bounded_story_loop_resume_decision,
    bounded_story_loop_scope_guard,
    bounded_story_loop_step_effects,
)


def test_workflow_boundary_records_failed_attempt_refs_without_publication() -> None:
    effects = bounded_story_loop_step_effects(
        LoopAttempt.model_validate(
            {
                "attemptOrdinal": 1,
                "kind": "implementation",
                "stepExecutionId": "workflow:run:implement-story:execution:1",
                "checkpointBeforeRef": "artifact://checkpoint/before",
                "checkpointAfterRef": "artifact://checkpoint/after",
                "candidateDiffRef": "artifact://diff/candidate",
                "gateResultRef": "artifact://gate/attempt-1",
                "terminalDisposition": "failed_with_remaining_work",
            }
        ),
        TypedGateResult.model_validate(
            {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "gateResultRef": "artifact://gate/attempt-1",
                "remainingWorkRef": "artifact://remaining-work",
            }
        ),
    )

    assert effects["commitAllowed"] is False
    assert effects["publicationAllowed"] is False
    assert effects["candidateDiffRef"] == "artifact://diff/candidate"
    assert effects["checkpointRefs"] == [
        "artifact://checkpoint/before",
        "artifact://checkpoint/after",
    ]


def test_workflow_boundary_allows_publication_for_accepted_latest_attempt() -> None:
    effects = bounded_story_loop_step_effects(
        LoopAttempt.model_validate(
            {
                "attemptOrdinal": 2,
                "kind": "remediation",
                "stepExecutionId": "workflow:run:remediate-story:execution:2",
                "checkpointBeforeRef": "artifact://checkpoint/before-2",
                "checkpointAfterRef": "artifact://checkpoint/after-2",
                "acceptedOutputRef": "artifact://accepted-output",
                "gateResultRef": "artifact://gate/attempt-2",
                "terminalDisposition": "accepted",
            }
        ),
        TypedGateResult.model_validate(
            {
                "verdict": "FULLY_IMPLEMENTED",
                "terminalDisposition": "accepted",
                "gateResultRef": "artifact://gate/attempt-2",
            }
        ),
    )

    assert effects["commitAllowed"] is True
    assert effects["publicationAllowed"] is True
    assert effects["acceptedOutputRef"] == "artifact://accepted-output"


def test_resume_decision_requires_checkpoint_and_digest_match() -> None:
    decision = bounded_story_loop_resume_decision(
        {
            "loopId": "loop-1",
            "recoveryCheckpointRef": "artifact://checkpoint/recovery",
            "selectedItemDigest": "sha256:item-1",
            "resumeFromAttemptOrdinal": 2,
        },
        current_selected_item_digest="sha256:item-1",
    )

    assert decision["allowed"] is True
    assert decision["mode"] == "checkpoint_backed_resume"

    denied = bounded_story_loop_resume_decision(
        {
            "loopId": "loop-1",
            "recoveryCheckpointRef": "artifact://checkpoint/recovery",
            "selectedItemDigest": "sha256:item-1",
            "resumeFromAttemptOrdinal": 2,
        },
        current_selected_item_digest="sha256:other",
    )
    assert denied["allowed"] is False
    assert denied["reason"] == "selected_item_digest_mismatch"
    assert denied["fallback"] != "full_rerun"


def test_scope_guard_keeps_full_autonomous_supervisor_gated() -> None:
    allowed = bounded_story_loop_scope_guard(
        selected_item_digest="sha256:item-1",
        candidate_item_digests=["sha256:item-1"],
        full_supervisor_enabled=False,
    )
    assert allowed["allowed"] is True

    denied = bounded_story_loop_scope_guard(
        selected_item_digest="sha256:item-1",
        candidate_item_digests=["sha256:item-1", "sha256:item-2"],
        full_supervisor_enabled=False,
    )
    assert denied["allowed"] is False
    assert denied["reason"] == "unrelated_work_selection_rejected"

    supervisor = bounded_story_loop_scope_guard(
        selected_item_digest="sha256:item-1",
        candidate_item_digests=["sha256:item-1"],
        full_supervisor_enabled=True,
    )
    assert supervisor["allowed"] is False
    assert supervisor["reason"] == "full_autonomous_supervisor_gated"


def test_workflow_payload_records_compiled_bounded_story_loop_context() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_bounded_story_loop_context(
        {},
        {
            "boundedStoryLoop": {
                "selectedItemRef": "artifact://story/item-1",
                "selectedItemDigest": "sha256:item-1",
                "publishMode": "pr",
                "mergeAutomationEnabled": True,
                "budgets": {
                    "maxAttempts": 3,
                    "maxConsecutiveNoProgressAttempts": 2,
                    "maxRepeatedFailedCommands": 2,
                    "maxUnsafeOrPolicyDeniedAttempts": 0,
                },
            }
        },
    )

    context = workflow._publish_context["boundedStoryLoop"]
    assert context == {
        "selectedItemRef": "artifact://story/item-1",
        "selectedItemDigest": "sha256:item-1",
        "nodeKinds": [
            "implementation",
            "verification",
            "remediation",
            "post_remediation_verification",
            "publication_evaluation",
        ],
        "publishMode": "pr",
        "mergeAutomationEnabled": True,
        "scopeGuard": {"allowed": True, "reason": "selected_item_only"},
    }


def test_workflow_payload_rejects_full_supervisor_for_bounded_story_loop() -> None:
    workflow = MoonMindRunWorkflow()

    with pytest.raises(ValueError, match="full_autonomous_supervisor_gated"):
        workflow._record_bounded_story_loop_context(
            {},
            {
                "boundedStoryLoop": {
                    "selectedItemRef": "artifact://story/item-1",
                    "selectedItemDigest": "sha256:item-1",
                    "fullSupervisorEnabled": True,
                    "budgets": {
                        "maxAttempts": 3,
                        "maxConsecutiveNoProgressAttempts": 2,
                        "maxRepeatedFailedCommands": 2,
                        "maxUnsafeOrPolicyDeniedAttempts": 0,
                    },
                }
            },
        )


def test_parent_loop_continues_from_structured_gate_when_remediation_remains() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-1", "attempt": 1, "artifacts": {}}
    ]
    workflow._step_terminal_dispositions["verify-1"] = "accepted"

    decision = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            validated_refs={"diffRef": "artifact://diff/attempt-1"},
            invalidated_refs=("artifact://superseded/output-1",),
            remaining_work_ref="artifact://remaining-work/attempt-1",
            recommended_next_action="reattempt_current_step",
            target_logical_step_id="remediate-1",
            workspace_policy_recommendation=(
                "apply_previous_execution_diff_to_clean_baseline"
            ),
        ),
        gate_result_ref="artifact://gate/attempt-1",
        ordered_nodes=[
            {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
            {
                "id": "remediate-1",
                "inputs": {
                    "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
                },
            },
            {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        ],
        current_index=0,
    )

    assert decision["continueLoop"] is True
    assert decision["nextAttemptKind"] == "remediation"
    assert decision["remainingWorkRef"] == "artifact://remaining-work/attempt-1"
    assert decision["hasRemainingRemediationStep"] is True
    assert (
        workflow._publish_context["boundedStoryLoop"]["continuationDecision"]
        == decision
    )


def test_parent_loop_stops_on_structured_gate_when_remediation_budget_exhausted() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-final", "attempt": 1, "artifacts": {}}
    ]
    workflow._step_terminal_dispositions["verify-final"] = "accepted"

    decision = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-final",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            remaining_work_ref="artifact://remaining-work/final",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/final",
        ordered_nodes=[
            {"id": "verify-final", "inputs": {"selectedSkill": "moonspec-verify"}},
            {
                "id": "code-review",
                "inputs": {
                    "annotations": {"jiraOrchestrateRole": "code-review-handoff"}
                },
            },
        ],
        current_index=0,
    )

    assert decision["continueLoop"] is False
    assert decision["state"] == "failed_with_remaining_work"
    assert decision["reason"] == "max_attempts_exhausted"
    assert decision["remainingWorkRef"] == "artifact://remaining-work/final"
    assert decision["hasRemainingRemediationStep"] is False


def test_moonspec_gate_context_records_invalidated_refs_as_typed_data() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_moonspec_verify_gate(
        node_id="verify-1",
        outputs={
            "moonSpecVerify": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "confidence": "medium",
                "validatedRefs": {"testReportRef": "artifact://tests/report"},
                "invalidatedRefs": ["artifact://step/output-v1"],
                "remainingWorkRef": "artifact://remaining-work/attempt-1",
                "recommendedNextAction": "reattempt_current_step",
                "targetLogicalStepId": "remediate-1",
                "workspacePolicyRecommendation": (
                    "apply_previous_execution_diff_to_clean_baseline"
                ),
            }
        },
    )

    context = workflow._publish_context["moonSpecGate"]
    assert context["invalidatedRefs"] == ["artifact://step/output-v1"]
    assert context["remainingWorkRef"] == "artifact://remaining-work/attempt-1"
    assert context["recommendedNextAction"] == "reattempt_current_step"
    assert context["targetLogicalStepId"] == "remediate-1"
