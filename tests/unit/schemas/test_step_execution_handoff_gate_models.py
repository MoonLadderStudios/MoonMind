from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.step_execution_models import (
    HandoffGateInput,
    StructuredGateSourceModel,
)
from moonmind.workflows.temporal.step_executions import evaluate_handoff_gate


def _accepted_input(**overrides: object) -> HandoffGateInput:
    payload: dict[str, object] = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "handoffClass": "jira_comment",
        "actor": {"type": "workflow", "id": "MoonMindRunWorkflow"},
        "action": "jira.add_comment",
        "target": {"type": "jira_issue", "id": "MM-123"},
        "idempotencyKey": "wf-1:run-1:verify:execution:1:jira.add_comment:MM-123",
        "producingStepExecution": {
            "workflowId": "wf-1",
            "runId": "run-1",
            "logicalStepId": "verify",
            "executionOrdinal": 1,
            "stepExecutionId": "wf-1:run-1:verify:execution:1",
            "terminalDisposition": "accepted",
            "manifestRef": "artifact://step-executions/verify-1-terminal",
        },
        "gateSource": {
            "gateType": "moonspec_verify",
            "verdict": "FULLY_IMPLEMENTED",
            "passed": True,
            "logicalStepId": "verify",
            "evidenceRef": "artifact://verify/report",
        },
        "evidenceRefs": ["artifact://verify/report"],
    }
    payload.update(overrides)
    return HandoffGateInput.model_validate(payload)


def test_accepted_disposition_and_passing_gate_allows_handoff() -> None:
    decision = evaluate_handoff_gate(_accepted_input())

    dumped = decision.model_dump(by_alias=True)
    assert dumped["allowed"] is True
    assert dumped["decision"] == "allow"
    assert dumped["policyDecision"]["reason"] == (
        "accepted_step_execution_and_passing_gate"
    )
    assert dumped["dispositionSource"]["terminalDisposition"] == "accepted"
    assert dumped["gateSource"]["passed"] is True
    assert dumped["idempotencyKey"]


@pytest.mark.parametrize(
    "terminal_disposition",
    [
        "candidate",
        "failed",
        "blocked",
        "needs-human",
        "needs_human",
        "unknown",
        "",
        None,
        "degraded",
        "failed_with_remaining_work",
    ],
)
def test_unaccepted_dispositions_block_handoffs(terminal_disposition: object) -> None:
    producing = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "logicalStepId": "verify",
        "executionOrdinal": 1,
        "stepExecutionId": "wf-1:run-1:verify:execution:1",
        "terminalDisposition": terminal_disposition,
        "manifestRef": "artifact://step-executions/verify-1-terminal",
    }

    decision = evaluate_handoff_gate(
        _accepted_input(producingStepExecution=producing)
    )

    assert decision.allowed is False
    assert decision.policy_decision["reason"] == "producing_step_execution_not_accepted"
    assert any("terminal disposition" in item for item in decision.diagnostics)


@pytest.mark.parametrize(
    "gate_source",
    [
        None,
        {"gateType": "moonspec_verify", "verdict": "", "passed": False},
        {"gateType": "moonspec_verify", "verdict": "FAILED", "passed": False},
        {"gateType": "moonspec_verify", "verdict": "UNKNOWN", "passed": False},
    ],
)
def test_missing_or_failed_structured_gate_blocks_gate_dependent_handoff(
    gate_source: object,
) -> None:
    decision = evaluate_handoff_gate(_accepted_input(gateSource=gate_source))

    assert decision.allowed is False
    assert decision.policy_decision["reason"] == "structured_gate_not_passed"
    assert any("structured gate" in item for item in decision.diagnostics)


def test_idempotent_handoffs_without_stable_key_fail_closed() -> None:
    decision = evaluate_handoff_gate(_accepted_input(idempotencyKey=None))

    assert decision.allowed is False
    assert decision.policy_decision["reason"] == "missing_idempotency_key"


def test_non_idempotent_external_handoffs_require_policy_approval() -> None:
    decision = evaluate_handoff_gate(
        _accepted_input(
            handoffClass="merge_automation",
            action="repo.merge",
            idempotencyKey=None,
            requiresIdempotencyKey=False,
            requiresExplicitPolicyApproval=True,
            explicitPolicyApproval=False,
        )
    )

    assert decision.allowed is False
    assert decision.policy_decision["reason"] == "explicit_policy_approval_required"


def test_structured_gate_source_rejects_blank_gate_type() -> None:
    with pytest.raises(ValidationError):
        StructuredGateSourceModel.model_validate(
            {"gateType": " ", "verdict": "FULLY_IMPLEMENTED", "passed": True}
        )
