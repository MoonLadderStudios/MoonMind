from __future__ import annotations

import pytest

from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    decide_remediation_continuation,
    remediation_step_execution_id,
)


def _spec(max_attempts: int = 2) -> RemediationLoopSpec:
    return RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": "issue-implementation-remediation",
            "remediationTool": {"type": "skill", "name": "auto"},
            "verificationTool": {"type": "skill", "name": "moonspec-verify"},
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {
                "hardMaxAttempts": max_attempts,
                "maxConsecutiveSemanticNoProgress": 2,
                "maxRepeatedFailureSignature": 2,
                "maxEvidenceRetries": 1,
                "maxContractRepairs": 1,
            },
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


def _state(*, attempts: int = 0, evidence_retries: int = 0) -> RemediationLoopState:
    return RemediationLoopState(
        loopId="issue-implementation-remediation",
        attemptOrdinal=attempts,
        phase=RemediationLoopPhase.CONTINUATION_DECIDING,
        workspaceHeadRef="artifact://workspace/C1",
        latestVerificationRef="artifact://verification/V1",
        consumedBudgets=ConsumedRemediationBudgets(
            attempts=attempts, evidenceRetries=evidence_retries
        ),
    )


@pytest.mark.parametrize("maximum", [1, 2, 6])
def test_hard_max_is_runtime_policy_without_plan_expansion(maximum: int) -> None:
    spec = _spec(maximum)
    admitted = decide_remediation_continuation(
        spec=spec,
        state=_state(attempts=maximum - 1),
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate/latest",
    )
    stopped = decide_remediation_continuation(
        spec=spec,
        state=_state(attempts=maximum),
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate/latest",
    )

    assert admitted.continue_loop is True
    assert admitted.next_attempt == maximum
    assert stopped.continue_loop is False
    assert stopped.next_phase == RemediationLoopPhase.STOPPED_REMAINING_WORK


def test_evidence_retry_does_not_consume_or_create_semantic_attempt() -> None:
    decision = decide_remediation_continuation(
        spec=_spec(),
        state=_state(attempts=1),
        verdict="NO_DETERMINATION",
        gate_result_ref="artifact://gate/invalid",
        recoverable_evidence=True,
    )

    assert decision.retry_kind == "evidence"
    assert decision.next_attempt is None
    assert decision.next_phase == RemediationLoopPhase.VERIFICATION_PENDING


def test_acceptance_and_block_are_terminal_workflow_decisions() -> None:
    accepted = decide_remediation_continuation(
        spec=_spec(),
        state=_state(),
        verdict="FULLY_IMPLEMENTED",
        gate_result_ref="artifact://gate/pass",
    )
    blocked = decide_remediation_continuation(
        spec=_spec(),
        state=_state(),
        verdict="BLOCKED",
        gate_result_ref="artifact://gate/blocked",
    )

    assert accepted.next_phase == RemediationLoopPhase.ACCEPTED
    assert blocked.next_phase == RemediationLoopPhase.BLOCKED
    assert not accepted.continue_loop and not blocked.continue_loop


def test_semantic_step_execution_id_is_attempt_scoped() -> None:
    assert remediation_step_execution_id("wf", "run", "loop", "remediation", 2) == (
        "wf:run:loop:remediation:2"
    )
    assert remediation_step_execution_id("wf", "run", "loop", "verification", 2) == (
        "wf:run:loop:verification:2"
    )


def test_continue_as_new_state_rejects_inline_or_filesystem_evidence() -> None:
    with pytest.raises(ValueError, match="artifact://"):
        RemediationLoopState(
            loopId="loop",
            phase="remediation_pending",
            workspaceHeadRef="/tmp/workspace",
            consumedBudgets={},
        )
