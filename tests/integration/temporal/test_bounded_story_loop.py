from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.temporal.bounded_story_loop import (
    LoopAttempt,
    LoopBudget,
    PublicationAction,
    TypedGateResult,
    evaluate_attempt_continuation,
    evaluate_attempt_preflight,
    evaluate_provider_lease,
    evaluate_publication_decision,
)
from moonmind.workflows.temporal.workflows.run import (
    bounded_story_loop_resume_decision,
)
from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    invalidate_downstream_steps_for_changed_output,
    record_dependency_inputs_for_step,
    update_step_row,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _budget(**overrides: object) -> LoopBudget:
    payload = {
        "maxAttempts": 2,
        "maxConsecutiveNoProgressAttempts": 1,
        "maxRepeatedFailedCommands": 1,
        "maxUnsafeOrPolicyDeniedAttempts": 0,
    }
    payload.update(overrides)
    return LoopBudget.model_validate(payload)


def _attempt(ordinal: int, **overrides: object) -> LoopAttempt:
    payload = {
        "attemptOrdinal": ordinal,
        "kind": "implementation" if ordinal == 1 else "remediation",
        "stepExecutionId": f"workflow:run:story:execution:{ordinal}",
        "checkpointBeforeRef": f"artifact://checkpoint/before-{ordinal}",
        "checkpointAfterRef": f"artifact://checkpoint/after-{ordinal}",
        "gateResultRef": f"artifact://gate/{ordinal}",
        "terminalDisposition": "failed_with_remaining_work",
    }
    payload.update(overrides)
    return LoopAttempt.model_validate(payload)


def test_failed_first_attempt_retains_candidate_refs_and_starts_bounded_remediation() -> None:
    first = _attempt(1, candidateDiffRef="artifact://diff/failed-1")
    gate = TypedGateResult.model_validate(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "gateResultRef": "artifact://gate/1",
            "remainingWorkRef": "artifact://remaining-work/1",
            "progressSignature": "sig-1",
        }
    )

    decision = evaluate_attempt_continuation(
        attempt=first,
        gate=gate,
        budget=_budget(consumed={"attempts": 1}),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert first.commit_allowed is False
    assert first.publication_allowed is False
    assert first.candidate_diff_ref == "artifact://diff/failed-1"
    assert decision.continue_loop is True
    assert decision.next_attempt_kind == "remediation"


def test_later_accepted_attempt_allows_publication_only_for_latest_step() -> None:
    failed = _attempt(1, candidateDiffRef="artifact://diff/failed-1")
    accepted = _attempt(
        2,
        acceptedOutputRef="artifact://accepted/2",
        terminalDisposition="accepted",
    )

    denied = evaluate_publication_decision(
        action=PublicationAction.PR,
        latest_attempt=failed,
        gate=TypedGateResult.model_validate(
            {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "gateResultRef": "artifact://gate/1",
                "remainingWorkRef": "artifact://remaining-work/1",
            }
        ),
    )
    allowed = evaluate_publication_decision(
        action=PublicationAction.PR,
        latest_attempt=accepted,
        gate=TypedGateResult.model_validate(
            {
                "verdict": "FULLY_IMPLEMENTED",
                "terminalDisposition": "accepted",
                "gateResultRef": "artifact://gate/2",
            }
        ),
    )

    assert denied.allowed is False
    assert allowed.allowed is True
    assert allowed.latest_producing_step_execution_id == accepted.step_execution_id


def test_invalid_environment_preflight_blocks_before_budget_burn() -> None:
    decision = evaluate_attempt_preflight(
        {
            "sidecar": {"ok": True},
            "runtime": {"ok": True},
            "workspace": {"ok": True},
            "skillProjection": {
                "ok": False,
                "diagnosticsRef": "artifact://skill-projection",
            },
            "role": {"ok": True},
            "exceptionalWorkload": {"ok": True},
            "policy": {"ok": True},
        },
        budget=_budget(consumed={"attempts": 1}),
    )

    assert decision.allowed is False
    assert decision.state == "blocked"
    assert decision.reason == "skill_projection_preflight_failed"
    assert decision.consumes_attempt_budget is False


def test_provider_lease_unavailable_blocks_or_queues_before_launch() -> None:
    decision = evaluate_provider_lease(
        {
            "status": "denied",
            "leaseRef": "artifact://lease/denied",
            "diagnosticsRef": "artifact://lease/diagnostics",
        }
    )

    assert decision.allowed is False
    assert decision.reason == "provider_lease_denied"
    assert decision.lease_ref == "artifact://lease/denied"


def test_checkpoint_backed_resume_uses_checkpoint_without_full_rerun() -> None:
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
    assert decision["fallback"] != "full_rerun"


def test_budget_exhaustion_preserves_remaining_work_refs() -> None:
    decision = evaluate_attempt_continuation(
        attempt=_attempt(2, candidateDiffRef="artifact://diff/2"),
        gate=TypedGateResult.model_validate(
            {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "gateResultRef": "artifact://gate/2",
                "remainingWorkRef": "artifact://remaining-work/2",
            }
        ),
        budget=_budget(consumed={"attempts": 2}),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert decision.continue_loop is False
    assert decision.state == "failed_with_remaining_work"
    assert decision.reason == "max_attempts_exhausted"
    assert decision.remaining_work_ref == "artifact://remaining-work/2"


def test_accepted_upstream_output_change_invalidates_dependent_story_steps() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "story-a", "title": "Story A"},
            {"id": "story-b", "title": "Story B"},
        ],
        dependency_map={"story-b": ["story-a"]},
        updated_at=now,
    )
    update_step_row(
        rows,
        "story-a",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://story-a/accepted-v1"},
    )
    record_dependency_inputs_for_step(
        rows,
        "story-b",
        workflow_id="workflow",
        run_id="run",
        updated_at=now,
    )
    update_step_row(
        rows,
        "story-b",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://story-b/accepted-v1"},
    )
    update_step_row(
        rows,
        "story-a",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://story-a/accepted-v2"},
    )

    invalidated = invalidate_downstream_steps_for_changed_output(
        rows,
        "story-a",
        workflow_id="workflow",
        run_id="run",
        updated_at=now,
    )

    assert invalidated == ["story-b"]
    assert rows[1]["status"] == "pending"
    assert rows[1]["waitingReason"] == "requires_revalidation"
    assert rows[1]["dependencyReuseGate"]["status"] == "requires_revalidation"
