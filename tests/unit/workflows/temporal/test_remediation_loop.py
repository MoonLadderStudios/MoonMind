from __future__ import annotations

import pytest

from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    apply_continuation_decision,
    capture_remediation_candidate,
    decide_remediation_continuation,
    materialize_attempt_nodes,
    project_remediation_loop,
    record_semantic_progress,
    record_verification_evidence,
    remediation_step_execution_id,
    resolve_loop_runtime,
    should_continue_as_new,
    start_remediation_attempt,
    start_verification,
)

_LOOP_RUNTIME = {
    "mode": "codex_cli",
    "model": "gpt-5.6-sol",
    "effort": "high",
    "executionProfileRef": "codex_openai_oauth",
}


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


def test_environment_contamination_is_a_terminal_block() -> None:
    decision = decide_remediation_continuation(
        spec=_spec(),
        state=_state(),
        verdict="ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
        gate_result_ref="artifact://gate/contaminated",
    )

    assert decision.continue_loop is False
    assert decision.next_phase == RemediationLoopPhase.BLOCKED


def test_repeated_progress_evidence_updates_no_progress_budgets() -> None:
    first = record_semantic_progress(
        _state(), progress_ref="artifact://remaining/R1"
    )
    repeated = record_semantic_progress(
        first, progress_ref="artifact://remaining/R1"
    )

    assert repeated.consumed_budgets.consecutive_semantic_no_progress == 1
    assert repeated.consumed_budgets.repeated_failure_signature == 1


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


def test_attempt_lifecycle_advances_head_before_read_only_verification() -> None:
    pending = _state(attempts=0).model_copy(
        update={"phase": RemediationLoopPhase.REMEDIATION_PENDING}
    )
    running = start_remediation_attempt(pending)
    captured = capture_remediation_candidate(
        running, workspace_head_ref="artifact://workspace/C2"
    )
    verifying = start_verification(captured)
    evaluating = record_verification_evidence(
        verifying, verification_ref="artifact://verification/V2"
    )

    assert running.attempt_ordinal == 1
    assert captured.workspace_head_ref == "artifact://workspace/C2"
    assert verifying.consumed_budgets.attempts == 1
    assert evaluating.phase == RemediationLoopPhase.CONTINUATION_DECIDING


def test_decision_is_persisted_once_and_drives_projection() -> None:
    state = _state(attempts=1)
    decision = decide_remediation_continuation(
        spec=_spec(),
        state=state,
        verdict="FULLY_IMPLEMENTED",
        gate_result_ref="artifact://gate/pass",
    )
    accepted = apply_continuation_decision(
        state,
        decision=decision,
        decision_ref="artifact://decision/D1",
    )
    projection = project_remediation_loop(spec=_spec(), state=accepted)

    assert accepted.phase == RemediationLoopPhase.ACCEPTED
    assert projection["latestVerdict"] == "FULLY_IMPLEMENTED"
    assert projection["continuationDecisionRef"] == "artifact://decision/D1"
    assert projection["continuationReason"] == "verification_accepted"


def test_materialization_creates_only_the_admitted_pair() -> None:
    remediation, verification = materialize_attempt_nodes(
        spec=_spec(6),
        workflow_id="wf",
        run_id="run",
        ordinal=2,
        workspace_head_ref="artifact://workspace/C1",
        runtime=_LOOP_RUNTIME,
    )

    assert remediation["id"] == (
        "wf:run:issue-implementation-remediation:remediation:2"
    )
    assert verification["id"] == (
        "wf:run:issue-implementation-remediation:verification:2"
    )
    assert verification["dependsOn"] == [remediation["id"]]
    assert remediation["inputs"]["selectedSkill"] == "auto"
    assert verification["inputs"]["selectedSkill"] == "moonspec-verify"
    assert verification["inputs"]["readOnlyWorkspaceHead"] is True


def test_materialized_attempts_route_to_the_runs_resolved_runtime() -> None:
    """``auto`` loop tools inherit the run's runtime; they never route to adapters."""

    remediation, verification = materialize_attempt_nodes(
        spec=_spec(6),
        workflow_id="wf",
        run_id="run",
        ordinal=1,
        workspace_head_ref="artifact://workspace/C1",
        runtime=_LOOP_RUNTIME,
    )

    for node in (remediation, verification):
        assert node["tool"] == {"type": "agent_runtime", "name": "codex_cli"}
        assert node["inputs"]["runtime"] == _LOOP_RUNTIME


@pytest.mark.parametrize("runtime", [None, {}, {"mode": "auto"}, {"mode": " "}])
def test_materialization_requires_a_resolved_runtime(runtime) -> None:
    with pytest.raises(ValueError, match="resolved agent runtime"):
        materialize_attempt_nodes(
            spec=_spec(6),
            workflow_id="wf",
            run_id="run",
            ordinal=1,
            workspace_head_ref="artifact://workspace/C1",
            runtime=runtime,
        )


def test_resolve_loop_runtime_normalizes_agent_id_aliases() -> None:
    runtime_id, block = resolve_loop_runtime({"agentId": "claude_code"})

    assert runtime_id == "claude_code"
    assert block == {"agentId": "claude_code", "mode": "claude_code"}


def test_continue_as_new_preserves_consumed_budget_threshold() -> None:
    spec = _spec(6).model_copy(update={"continue_as_new_attempt_threshold": 3})
    state = _state(attempts=3).model_copy(
        update={"phase": RemediationLoopPhase.REMEDIATION_PENDING}
    )

    assert should_continue_as_new(spec=spec, state=state) is True
    carried = RemediationLoopState.model_validate(
        state.model_dump(by_alias=True, mode="json")
    )
    assert carried.attempt_ordinal == 3
    assert carried.workspace_head_ref == "artifact://workspace/C1"
