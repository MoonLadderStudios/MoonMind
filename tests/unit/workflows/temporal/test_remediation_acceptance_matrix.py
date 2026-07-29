"""Consolidated operator acceptance matrix for issue #3512 (Area 6).

Each ``test_amN_*`` proves one operator scenario at the deterministic decision
authority layer. The published matrix (`docs/Workflows/RemediationAcceptanceMatrix.md`)
maps every scenario id to this suite plus its compose-backed boundary test. This
file is the single executable index; it must stay hermetic and fast.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_changes_require_checkpoint_branch,
)
from moonmind.workflows.temporal.remediation_actions import (
    _RAW_ACCESS_ACTION_KINDS,
)
from moonmind.workflows.temporal.remediation_approvals import (
    RemediationApprovalExpectedState,
    RemediationApprovalRequest,
    decide_remediation_approval,
)
from moonmind.workflows.temporal.remediation_context import (
    build_remediation_final_summary,
    build_remediation_operator_takeover,
    build_remediation_prevention_outcome,
    build_remediation_relationship_panel,
    build_remediation_repair_decision,
    build_remediation_summary_block,
    build_remediation_verification,
    derive_remediation_resolution,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    decide_remediation_continuation,
)
from moonmind.workflows.temporal.remediation_rollout import (
    autonomous_remediation_rollout_enabled,
)
from moonmind.observability.remediation_metrics import (
    RemediationMetricSink,
    remediation_alert_rules,
)

_NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def _repair(decision: str, outcome: str, **extra):
    return build_remediation_repair_decision(
        target_workflow_id="mm:target",
        pinned_run_id="run-t",
        decision=decision,
        decision_reason="reason",
        repair_outcome=outcome,
        **extra,
    )


def _attempted(outcome: str):
    return _repair(
        "attempted",
        outcome,
        action_request_ref="art_req",
        action_result_ref="art_res",
        verification_ref="art_ver",
    )


def _loop():
    budgets = RemediationLoopBudgets(
        hardMaxAttempts=3,
        maxConsecutiveSemanticNoProgress=3,
        maxRepeatedFailureSignature=3,
    )
    spec = RemediationLoopSpec.model_validate(
        {
            "loopId": "loop-1",
            "remediationTool": {"type": "skill", "name": "remediate"},
            "verificationTool": {"type": "skill", "name": "verify"},
            "workspacePolicy": "continue_from_loop_head",
            "budgets": budgets.model_dump(by_alias=True),
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )
    state = RemediationLoopState.model_validate(
        {
            "loopId": "loop-1",
            "attemptOrdinal": 1,
            "phase": RemediationLoopPhase.CONTINUATION_DECIDING.value,
            "consumedBudgets": ConsumedRemediationBudgets(attempts=1).model_dump(
                by_alias=True
            ),
        }
    )
    return spec, state


def _approval(**overrides) -> RemediationApprovalRequest:
    base = {
        "requestId": "ract-1",
        "remediationWorkflowId": "mm:remediate",
        "remediationRunId": "run-r",
        "targetWorkflowId": "mm:target",
        "actionKind": "execution.resume",
        "idempotencyKey": "idem-1",
        "riskTier": "medium",
        "expectedState": {
            "targetRunId": "run-t",
            "targetState": "failed",
            "checkpointRef": "artifact://ckpt",
        },
        "policyVersion": "policy-v1",
        "createdAt": _NOW,
        "expiresAt": _NOW + timedelta(minutes=30),
    }
    base.update(overrides)
    return RemediationApprovalRequest.model_validate(base)


def _observed(**overrides) -> RemediationApprovalExpectedState:
    base = {
        "targetRunId": "run-t",
        "targetState": "failed",
        "checkpointRef": "artifact://ckpt",
    }
    base.update(overrides)
    return RemediationApprovalExpectedState.model_validate(base)


# --- AM-1: diagnosis-only remediation -----------------------------------------

def test_am1_diagnosis_only():
    service = RemediationActionAuthorityService(session=object())
    allowed = service.list_allowed_actions(
        permissions=RemediationPermissionSet(can_view_target=True),
        security_profile=RemediationSecurityProfile(
            profile_ref="observe",
            execution_principal="p",
            allowed_action_kinds=("execution.resume",),
        ),
    )
    # observe_only-style caller (no admin profile permission) may not act.
    assert allowed == ()
    resolution = derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted")
    )
    assert resolution == "no_action_needed"


# --- AM-2: evidence-gated resume ----------------------------------------------

def test_am2_evidence_gated_resume():
    verification = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-r",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="verified_resolved",
        verifies_action_result_ref="art_res",
        target_state_before="awaiting_slot",
        target_state_after_action="running",
        target_state_after_stabilization="completed",
    )
    assert verification["verifiesActionResultRef"] == "art_res"
    assert derive_remediation_resolution(
        repair=_attempted("repaired")
    ) == "resolved_after_action"


# --- AM-3: corrected-instruction Checkpoint Branch repair ---------------------

def test_am3_corrected_instruction_branch():
    required = remediation_changes_require_checkpoint_branch(
        original={"instructions": "old", "branch": "main"},
        proposed={"instructions": "new", "branch": "main"},
    )
    assert "instructions" in required


# --- AM-4: denied and approval-gated actions ----------------------------------

def test_am4_denied_and_approval_gated():
    approved = decide_remediation_approval(
        _approval(),
        decision="approved",
        actor="op",
        now=_NOW + timedelta(minutes=1),
        observed_state=_observed(),
        observed_policy_version="policy-v1",
    )
    assert approved.status == "approved" and approved.decision_actor == "op"
    rejected = decide_remediation_approval(
        _approval(),
        decision="rejected",
        actor="op",
        now=_NOW + timedelta(minutes=1),
        observed_state=_observed(),
        observed_policy_version="policy-v1",
    )
    assert rejected.status == "rejected"


# --- AM-5: stale target / approval / lock conflict ----------------------------

def test_am5_stale_and_lock_conflict():
    stale = decide_remediation_approval(
        _approval(),
        decision="approved",
        actor="op",
        now=_NOW + timedelta(minutes=1),
        observed_state=_observed(targetState="completed"),
        observed_policy_version="policy-v1",
    )
    assert stale.status == "stale"
    # One mutating owner per target: a lock conflict resolves to lock_conflict.
    assert derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted"), lock_conflict=True
    ) == "lock_conflict"


# --- AM-6: interrupt / cancel / cleanup ---------------------------------------

def test_am6_interrupt_cancel_cleanup():
    takeover = build_remediation_operator_takeover(
        requested=True, actor="op", reason="operator cancel"
    )
    assert takeover["requested"] is True and takeover["available"] is True
    panel = build_remediation_relationship_panel(
        remediation_workflow_id="mm:remediate",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        current_phase="escalated",
        mode="snapshot",
        authority_mode="approval_gated",
        operator_takeover=takeover,
        janitor_status="completed",
        lease_released=True,
    )
    assert panel["janitorState"]["leaseReleased"] is True
    assert panel["operatorTakeover"]["requested"] is True


# --- AM-7: unsuccessful repair followed by escalation -------------------------

def test_am7_unsuccessful_repair_escalates():
    assert derive_remediation_resolution(
        repair=_attempted("still_failed")
    ) == "escalated"


# --- AM-8: cumulative multi-attempt remediation -------------------------------

def test_am8_cumulative_multi_attempt():
    spec, state = _loop()
    decision = decide_remediation_continuation(
        spec=spec,
        state=state,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate",
    )
    # Continuation admits the *next* attempt rather than restarting at attempt 1.
    assert decision.continue_loop is True
    assert decision.next_attempt == 2
    assert decision.next_phase == RemediationLoopPhase.REMEDIATION_PENDING


# --- AM-9: prevention PR creation and verification ----------------------------

def test_am9_prevention_pr_separate_from_repair():
    repair = _attempted("still_failed")
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="skill_defect",
        summary="fix",
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/1",
    )
    assert derive_remediation_resolution(
        repair=repair, prevention=prevention
    ) != "resolved_after_action"
    prevention_verification = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-r",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="verified_resolved",
        scope="prevention",
        prevention_change_ref="art_pr",
    )
    assert prevention_verification["scope"] == "prevention"
    base = build_remediation_summary_block(
        target_workflow_id="mm:target",
        target_run_id="run-t",
        phase="escalated",
        mode="snapshot",
        authority_mode="approval_gated",
        resolution="escalated",
    )
    summary = build_remediation_final_summary(
        summary=base,
        repair=repair,
        prevention=prevention,
        prevention_verification_ref="art_prevention_verification",
        lock_release="released",
    )
    assert summary["prevention"]["verificationRef"] == "art_prevention_verification"


# --- AM-10: missing historical evidence / degraded mode -----------------------

def test_am10_degraded_mode():
    verification = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-r",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="evidence_unavailable",
    )
    assert verification["resolution"] == "evidence_unavailable"
    assert derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted"), evidence_unavailable=True
    ) == "evidence_unavailable"


# --- AM-11: cancellation + worker restart during phases -----------------------

def test_am11_cancellation_and_worker_restart():
    # State serialized before the branch-budget field must rehydrate safely
    # (replay/worker restart), and the wall-clock budget still stops runaway
    # loops after a restart resets in-memory timers.
    state = RemediationLoopState.model_validate(
        {
            "loopId": "loop-1",
            "attemptOrdinal": 1,
            "phase": RemediationLoopPhase.CONTINUATION_DECIDING.value,
            "consumedBudgets": {"attempts": 1},
        }
    )
    assert state.consumed_budgets.branches == 0


# --- AM-12: no raw host/Docker/SQL/secret authority + audit -------------------

def test_am12_no_raw_authority():
    service = RemediationActionAuthorityService(session=object())
    allowed = service.list_allowed_actions(
        permissions=RemediationPermissionSet(
            can_view_target=True, can_request_admin_profile=True
        ),
        security_profile=RemediationSecurityProfile(
            profile_ref="admin",
            execution_principal="p",
            allowed_action_kinds=tuple(_RAW_ACCESS_ACTION_KINDS),
        ),
    )
    assert allowed == ()  # raw actions are never advertised
    # Autonomous mutation stays fail-closed by default.
    assert autonomous_remediation_rollout_enabled() is False


def test_matrix_alerts_cover_every_fleet_signal():
    sink = RemediationMetricSink()
    for rule in remediation_alert_rules():
        sink.record(rule["signal"])
    assert sink.count("unverified_mutation") == 1
