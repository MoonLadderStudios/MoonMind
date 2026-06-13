from __future__ import annotations

from moonmind.workflows.temporal.bounded_story_loop import LoopAttempt, TypedGateResult
from moonmind.workflows.temporal.workflows.run import (
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
