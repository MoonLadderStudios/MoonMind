from __future__ import annotations

import pytest

from moonmind.workflows.skills.approval_policy import StepGateResult
from moonmind.workflows.temporal.bounded_story_loop import LoopAttempt, TypedGateResult
from moonmind.workflows.temporal.workflows.run import (
    RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH,
    RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
    RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH,
    MoonMindRunWorkflow,
    bounded_story_loop_resume_decision,
    bounded_story_loop_scope_guard,
    bounded_story_loop_step_effects,
)


def _explicit_remediation_chain(max_attempts: int) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = [
        {
            "id": "verify-initial",
            "inputs": {"selectedSkill": "moonspec-verify"},
        }
    ]
    for attempt in range(1, max_attempts + 1):
        nodes.extend(
            [
                {
                    "id": f"remediate-{attempt}",
                    "inputs": {
                        "annotations": {
                            "issueImplementRole": "moonspec-remediation",
                            "moonSpecRemediationAttempt": attempt,
                            "moonSpecRemediationMaxAttempts": max_attempts,
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
                            "moonSpecRemediationMaxAttempts": max_attempts,
                            "moonSpecFinalRemediationGate": (
                                attempt == max_attempts
                            ),
                        },
                    },
                },
            ]
        )
    return nodes


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
        "budgets": {
            "maxAttempts": 3,
            "maxConsecutiveNoProgressAttempts": 2,
            "maxRepeatedFailedCommands": 2,
            "maxUnsafeOrPolicyDeniedAttempts": 0,
            "maxEvidenceRetries": 2,
            "maxInfrastructureRetries": 2,
            "maxContractRepairAttempts": 1,
            "providerBudget": None,
            "tokenBudget": None,
            "costBudget": None,
            "consumed": {},
        },
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


def test_parent_loop_continues_from_structured_gate_when_remediation_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
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
    assert decision["gate"]["progressSignature"]


def test_parent_loop_stops_when_verification_makes_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-1", "attempt": 1, "artifacts": {}},
        {"logicalStepId": "verify-2", "attempt": 1, "artifacts": {}},
    ]
    workflow._step_terminal_dispositions.update(
        {"verify-1": "accepted", "verify-2": "accepted"}
    )
    gate_result = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        confidence="medium",
        issues=({"requirement": "still failing", "status": "unmet"},),
        remaining_work_ref="artifact://remaining-work/unchanged",
        recommended_next_action="reattempt_current_step",
        target_logical_step_id="remediate-2",
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]

    first = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate_result,
        gate_result_ref="artifact://gate/attempt-1",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=gate_result,
        gate_result_ref="artifact://gate/attempt-2",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert first["continueLoop"] is True
    assert second["continueLoop"] is True
    assert second["reason"] == "verification_requested_remediation"
    assert second["budget"]["consumed"]["consecutiveNoProgressAttempts"] == 1
    assert second["hasRemainingRemediationStep"] is True


def test_semantic_no_progress_budget_stops_before_hard_attempt_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for mm:bd3dedc3-6cf3-4801-95b4-be177b70ef6b."""
    run = MoonMindRunWorkflow()
    monkeypatch.setattr(run, "_patched_or_false_outside_workflow", lambda _: True)
    ordered_nodes = _explicit_remediation_chain(max_attempts=6)
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        feedback="The verifier emitted the same sparse remaining-work summary.",
        recommended_next_action="reattempt_current_step",
    )

    decisions = []
    for attempt in range(0, 7):
        logical_step_id = "verify-initial" if attempt == 0 else f"verify-{attempt}"
        run._step_terminal_dispositions[logical_step_id] = "accepted"
        decisions.append(
            run._bounded_story_loop_continuation_decision(
                logical_step_id=logical_step_id,
                gate_result=gate,
                gate_result_ref=f"artifact://gate/{attempt}",
                ordered_nodes=ordered_nodes,
                current_index=attempt * 2,
            )
        )

    first_post_remediation = decisions[1]
    assert first_post_remediation["continueLoop"] is True
    assert first_post_remediation["reason"] == "verification_requested_remediation"
    assert first_post_remediation["nextLogicalStepId"] == "remediate-2"
    assert first_post_remediation["budget"][
        "maxConsecutiveNoProgressAttempts"
    ] == 2
    assert first_post_remediation["budget"]["consumed"][
        "consecutiveNoProgressAttempts"
    ] == 1

    assert decisions[1]["continueLoop"] is True
    assert decisions[2]["continueLoop"] is False
    assert decisions[2]["reason"] == "semantic_no_progress_exhausted"
    assert decisions[2]["remediationBudget"]["maxAttempts"] == 6
    assert decisions[2]["remediationBudget"]["currentAttempt"] == 2


def test_explicit_no_progress_policy_overrides_remediation_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindRunWorkflow()
    monkeypatch.setattr(run, "_patched_or_false_outside_workflow", lambda _: True)
    run._publish_context["boundedStoryLoop"] = {
        "budgets": {"maxConsecutiveNoProgressAttempts": 1}
    }
    ordered_nodes = _explicit_remediation_chain(max_attempts=6)
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        feedback="Unchanged remaining work.",
    )

    run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-initial",
        gate_result=gate,
        gate_result_ref="artifact://gate/initial",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    decision = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/1",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert decision["continueLoop"] is False
    assert decision["reason"] == "semantic_no_progress_exhausted"
    assert decision["budget"]["maxConsecutiveNoProgressAttempts"] == 1
    assert decision["remediationBudget"]["remainingAttempts"] == 6


def test_continuation_decision_preserves_consumption_and_applies_explicit_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindRunWorkflow()
    monkeypatch.setattr(run, "_patched_or_false_outside_workflow", lambda _: True)
    run._publish_context["boundedStoryLoop"] = {
        "budgets": {
            "maxAttempts": 6,
            "maxConsecutiveNoProgressAttempts": 2,
            "maxRepeatedFailedCommands": 2,
            "maxUnsafeOrPolicyDeniedAttempts": 0,
            "maxEvidenceRetries": 2,
        },
        "durableLoopState": {
            "schemaVersion": "remediation-loop-state/v1",
            "policy": {
                "maxAttempts": 6,
                "maxConsecutiveNoProgressAttempts": 2,
                "maxRepeatedFailedCommands": 2,
                "maxUnsafeOrPolicyDeniedAttempts": 0,
                "maxEvidenceRetries": 2,
                "consumed": {"attempts": 4, "evidenceRetries": 2},
            },
            "consumed": {"attempts": 4, "evidenceRetries": 2},
            "grants": {},
            "priorExhaustionReason": "evidence_retry_budget_exhausted",
        },
        "budgetGrant": {"evidenceRetries": 1},
        "retryAccounting": {"evidenceRetries": 2},
    }
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        feedback="A bounded gap remains.",
    )

    decision = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/1",
        ordered_nodes=_explicit_remediation_chain(max_attempts=6),
        current_index=2,
    )

    state = decision["durableLoopState"]
    assert state["consumed"]["attempts"] == 4
    assert state["consumed"]["evidenceRetries"] == 2
    assert state["policy"]["maxEvidenceRetries"] == 3
    assert state["grants"] == {"evidenceRetries": 1}
    assert state["priorExhaustionReason"] == "evidence_retry_budget_exhausted"
    assert "budgetGrant" not in run._publish_context["boundedStoryLoop"]


def test_continuation_seeds_no_progress_count_from_durable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindRunWorkflow()
    monkeypatch.setattr(run, "_patched_or_false_outside_workflow", lambda _: True)
    ordered_nodes = _explicit_remediation_chain(max_attempts=6)
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        issues=({"requirement": "same gap", "status": "unmet"},),
    )
    first = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/1",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )
    durable_state = first["durableLoopState"]
    durable_state["consumed"]["consecutiveNoProgressAttempts"] = 1
    durable_state["policy"]["consumed"]["consecutiveNoProgressAttempts"] = 1
    run._publish_context["boundedStoryLoop"] = {
        "budgets": {"maxConsecutiveNoProgressAttempts": 2},
        "durableLoopState": durable_state,
    }

    decision = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=gate,
        gate_result_ref="artifact://gate/2",
        ordered_nodes=ordered_nodes,
        current_index=4,
    )

    assert decision["continueLoop"] is False
    assert decision["reason"] == "semantic_no_progress_exhausted"
    assert decision["budget"]["consumed"]["consecutiveNoProgressAttempts"] == 2


def test_invalid_infrastructure_retry_telemetry_is_ignored() -> None:
    run = MoonMindRunWorkflow()
    decision = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-initial",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            validated_refs={"infrastructureRetries": "unknown"},
        ),
        gate_result_ref="artifact://gate/1",
        ordered_nodes=_explicit_remediation_chain(max_attempts=2),
        current_index=0,
    )

    assert decision["budget"]["consumed"]["infrastructureRetries"] == 0


def test_default_no_progress_policy_is_independent_of_remediation_budget_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindRunWorkflow()
    monkeypatch.setattr(
        run,
        "_patched_or_false_outside_workflow",
        lambda patch_id: patch_id
        in {
            RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
            RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH,
        },
    )
    ordered_nodes = _explicit_remediation_chain(max_attempts=6)
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        feedback="Unchanged remaining work.",
    )

    run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-initial",
        gate_result=gate,
        gate_result_ref="artifact://gate/initial",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    decision = run._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/1",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH.endswith("-v1")
    assert decision["continueLoop"] is True
    assert decision["reason"] == "verification_requested_remediation"
    assert decision["budget"]["maxConsecutiveNoProgressAttempts"] == 2


def test_parent_loop_continues_when_verification_progress_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-1", "attempt": 1, "artifacts": {}},
        {"logicalStepId": "verify-2", "attempt": 1, "artifacts": {}},
    ]
    workflow._step_terminal_dispositions.update(
        {"verify-1": "accepted", "verify-2": "accepted"}
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]

    first = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            issues=({"requirement": "first gap", "status": "unmet"},),
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/attempt-1",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            issues=({"requirement": "different gap", "status": "unmet"},),
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/attempt-2",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert first["gate"]["progressSignature"] != second["gate"]["progressSignature"]
    assert second["continueLoop"] is True
    assert second["reason"] == "verification_requested_remediation"


def test_parent_loop_does_not_treat_changed_verifier_feedback_as_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sparse structured gates still carry progress in verifier feedback."""
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
    workflow._step_terminal_dispositions.update(
        {"verify-1": "accepted", "verify-2": "accepted"}
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]

    first = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            feedback="Proxy events are buffered until terminal capture.",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/initial",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            feedback="Active journals exist; retry-stable cursors remain.",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/remediation-1",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert first["gate"]["progressVector"]["unresolvedGapDigest"] == second["gate"]["progressVector"]["unresolvedGapDigest"]
    assert second["gate"]["progressVector"]["classification"] == "no_progress"
    assert second["continueLoop"] is True
    assert second["nextAttemptKind"] == "remediation"


def test_feedback_progress_patch_preserves_prior_history_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(
        workflow,
        "_patched_or_false_outside_workflow",
        lambda patch_id: patch_id
        == RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH,
    )

    first = workflow._bounded_story_loop_gate_from_step_gate(
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            feedback="Initial remaining work.",
        ),
        gate_result_ref="artifact://gate/one",
        logical_step_id="verify-1",
        progress_budget_enabled=True,
    )
    second = workflow._bounded_story_loop_gate_from_step_gate(
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            feedback="Changed remaining work.",
        ),
        gate_result_ref="artifact://gate/two",
        logical_step_id="verify-2",
        progress_budget_enabled=True,
    )

    assert first.progress_signature == second.progress_signature


def test_parent_loop_does_not_treat_changed_remaining_work_refs_as_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
    workflow._step_terminal_dispositions.update(
        {"verify-1": "accepted", "verify-2": "accepted"}
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]
    first = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            remaining_work_ref="artifact://remaining/one",
        ),
        gate_result_ref="artifact://gate/one",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            remaining_work_ref="artifact://remaining/two",
        ),
        gate_result_ref="artifact://gate/two",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert first["gate"]["progressVector"]["unresolvedGapDigest"] == second["gate"]["progressVector"]["unresolvedGapDigest"]
    assert second["gate"]["progressVector"]["classification"] == "no_progress"
    assert second["continueLoop"] is True


def test_parent_loop_honors_configured_no_progress_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow, "_patched_or_false_outside_workflow", lambda _: True)
    workflow._publish_context["boundedStoryLoop"] = {
        "budgets": {"maxConsecutiveNoProgressAttempts": 2}
    }
    workflow._step_terminal_dispositions.update(
        {
            "verify-1": "accepted",
            "verify-2": "accepted",
            "verify-3": "accepted",
        }
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-3", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-3",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]
    gate = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        remaining_work_ref="artifact://remaining/same",
    )
    workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/one",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=gate,
        gate_result_ref="artifact://gate/two",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )
    third = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-3",
        gate_result=gate,
        gate_result_ref="artifact://gate/three",
        ordered_nodes=ordered_nodes,
        current_index=4,
    )

    assert second["continueLoop"] is True
    assert third["continueLoop"] is False
    assert third["reason"] == "semantic_no_progress_exhausted"


def test_parent_loop_replay_before_progress_patch_keeps_legacy_continuation() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_terminal_dispositions.update(
        {"verify-1": "accepted", "verify-2": "accepted"}
    )
    ordered_nodes = [
        {"id": "verify-1", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-1",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
        {"id": "verify-2", "inputs": {"selectedSkill": "moonspec-verify"}},
        {
            "id": "remediate-2",
            "inputs": {
                "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
            },
        },
    ]
    gate = StepGateResult(verdict="ADDITIONAL_WORK_NEEDED")
    workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=gate,
        gate_result_ref="artifact://gate/attempt-1",
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    second = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-2",
        gate_result=gate,
        gate_result_ref="artifact://gate/attempt-2",
        ordered_nodes=ordered_nodes,
        current_index=2,
    )

    assert second["continueLoop"] is True


def test_parent_loop_uses_gate_report_as_durable_remaining_work_evidence() -> None:
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
            recommended_next_action="reattempt_current_step",
            target_logical_step_id="remediate-1",
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
    assert decision["remainingWorkRef"] == "artifact://gate/attempt-1"
    assert decision["hasRemainingRemediationStep"] is True
    assert decision["gate"]["remainingWorkRef"] == "artifact://gate/attempt-1"


def test_parent_loop_accepts_canonical_artifact_id_as_gate_evidence() -> None:
    workflow = MoonMindRunWorkflow()

    gate = workflow._bounded_story_loop_gate_from_step_gate(
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="art_gate_result",
        logical_step_id="verify-1",
        progress_budget_enabled=False,
    )

    assert gate.gate_result_ref == "artifact://art_gate_result"
    assert gate.remaining_work_ref == "artifact://art_gate_result"


def test_parent_loop_continues_to_unannotated_issue_implement_remediation_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(
        workflow,
        "_moonspec_title_remediation_detection_enabled",
        lambda: True,
    )
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-implementation", "attempt": 1, "artifacts": {}}
    ]
    workflow._step_terminal_dispositions["verify-implementation"] = "accepted"

    decision = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-implementation",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="high",
            recommended_next_action="reattempt_current_step",
            target_logical_step_id="remediate-1",
        ),
        gate_result_ref="artifact://gate/implementation",
        ordered_nodes=[
            {
                "id": "verify-implementation",
                "inputs": {"selectedSkill": "moonspec-verify"},
            },
            {
                "id": "remediate-1",
                "skill": {"id": "auto"},
                "inputs": {"title": "Remediate verification gaps — attempt 1 of 6"},
            },
            {
                "id": "verify-remediation-1",
                "skill": {"id": "moonspec-verify"},
                "inputs": {"title": "Verify remediation attempt 1 of 6"},
            },
        ],
        current_index=0,
    )

    assert decision["continueLoop"] is True
    assert decision["nextAttemptKind"] == "remediation"
    assert decision["hasRemainingRemediationStep"] is True


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
    assert decision["reason"] == "no_remediation_successor"
    assert decision["remainingWorkRef"] == "artifact://remaining-work/final"
    assert decision["hasRemainingRemediationStep"] is False


def test_parent_loop_preserves_legacy_remediation_scan_before_plan_routing_patch() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-1", "attempt": 1, "artifacts": {}}
    ]
    workflow._step_terminal_dispositions["verify-1"] = "accepted"
    ordered_nodes = [
        {
            "id": "verify-1",
            "inputs": {"selectedSkill": "moonspec-verify"},
            "annotations": {
                "issueImplementRole": "moonspec-verification-gate",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 2,
            },
        },
        {
            "id": "remediate-2",
            "inputs": {"selectedSkill": "moonspec-implement"},
            "annotations": {
                "issueImplementRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": 2,
                "moonSpecRemediationMaxAttempts": 2,
            },
        },
    ]

    decision = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-1",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/legacy",
        ordered_nodes=ordered_nodes,
        current_index=0,
        plan_routed_moonspec_remediation_enabled=False,
    )

    assert decision["continueLoop"] is True
    assert decision["hasRemainingRemediationStep"] is True
    assert decision["remediationRoutingReason"] == (
        "legacy_remaining_remediation_scan"
    )


def test_parent_loop_guards_legacy_one_based_final_index() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [
        {"logicalStepId": "verify-final", "attempt": 1, "artifacts": {}}
    ]
    workflow._step_terminal_dispositions["verify-final"] = "accepted"
    ordered_nodes = [
        {"id": "verify-final", "inputs": {"selectedSkill": "moonspec-verify"}}
    ]

    decision = workflow._bounded_story_loop_continuation_decision(
        logical_step_id="verify-final",
        gate_result=StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            recommended_next_action="reattempt_current_step",
        ),
        gate_result_ref="artifact://gate/legacy-final",
        ordered_nodes=ordered_nodes,
        current_index=len(ordered_nodes),
        plan_routed_moonspec_remediation_enabled=False,
    )

    assert decision["continueLoop"] is False
    assert decision["hasRemainingRemediationStep"] is False
    assert decision["remediationBudget"]["currentAttempt"] is None


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
