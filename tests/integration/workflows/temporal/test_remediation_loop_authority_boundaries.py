"""Controlling restart boundaries for cumulative remediation authority."""

from moonmind.workflows.temporal.remediation_context import (
    build_remediation_final_summary,
    build_remediation_prevention_outcome,
)
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopState,
    capture_remediation_candidate,
    record_semantic_progress,
    start_remediation_attempt,
)


def test_restart_preserves_cumulative_head_and_changing_evidence_resets_no_progress():
    state = RemediationLoopState.model_validate(
        {
            "loopId": "loop",
            "attemptOrdinal": 1,
            "phase": "continuation_deciding",
            "workspaceHeadRef": "artifact://workspace/C1",
            "latestProgressRef": "artifact://remaining/R1",
            "latestProgressSignature": "sha256:" + "a" * 64,
            "consumedBudgets": {
                "attempts": 1,
                "consecutiveSemanticNoProgress": 1,
                "repeatedFailureSignature": 1,
            },
        }
    )
    restarted = RemediationLoopState.model_validate(
        state.model_dump(by_alias=True, mode="json")
    ).model_copy(update={"phase": "remediation_pending"})
    running = start_remediation_attempt(restarted)
    captured = capture_remediation_candidate(
        running, workspace_head_ref="artifact://workspace/C2"
    )
    progressed = record_semantic_progress(
        captured,
        progress_ref="artifact://remaining/R2",
        progress_signature="sha256:" + "b" * 64,
    )

    assert progressed.attempt_ordinal == 2
    assert progressed.workspace_head_ref == "artifact://workspace/C2"
    assert progressed.consumed_budgets.consecutive_semantic_no_progress == 0
    assert progressed.consumed_budgets.repeated_failure_signature == 0


def test_prevention_verification_is_separate_from_original_target_outcome():
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="test_gap",
        summary="Published a reviewable prevention change.",
        pull_request_url="https://github.com/example/repo/pull/1",
        verification_status="failed",
        verification_ref="artifact://verification/prevention-failed",
    )
    summary = build_remediation_final_summary(
        summary={
            "schemaVersion": "v1",
            "target": {"workflowId": "mm:target", "runId": "run-1"},
            "phase": "completed",
            "mode": "snapshot_then_follow",
            "authorityMode": "approval_gated",
            "resolution": "still_failed",
            "escalated": True,
        },
        repair={
            "schemaVersion": "v1",
            "decision": "attempted",
            "decisionReason": "repair did not resolve target",
            "repairOutcome": "still_failed",
            "target": {"workflowId": "mm:target", "pinnedRunId": "run-1"},
        },
        prevention=prevention,
        lock_release="released",
    )

    assert summary["resolution"] == "still_failed"
    assert summary["repair"]["repairOutcome"] == "still_failed"
    assert summary["prevention"]["status"] == "reviewable_change_created"
    assert summary["prevention"]["verification"]["status"] == "failed"
