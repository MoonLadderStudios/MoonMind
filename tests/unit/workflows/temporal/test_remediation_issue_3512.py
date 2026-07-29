"""Contract coverage for issue MoonLadderStudios/MoonMind#3512.

These tests exercise the new first-class verification taxonomy, the repair vs.
prevention resolution safeguard, and the bounded loop controls (wall-clock and
branch budgets). They are pure-contract and hermetic.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.remediation_context import (
    REMEDIATION_VERIFICATION_RESOLUTIONS,
    build_remediation_action_detail,
    build_remediation_autonomous_origin,
    build_remediation_final_summary,
    build_remediation_operator_takeover,
    build_remediation_prevention_outcome,
    build_remediation_relationship_panel,
    build_remediation_repair_decision,
    build_remediation_summary_block,
    build_remediation_verification,
    derive_remediation_resolution,
    normalize_remediation_verification_resolution,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    decide_remediation_continuation,
    record_branch_created,
)


# --- Area 3: verification is a first-class phase with a 7-value taxonomy -------

def test_verification_taxonomy_has_exactly_the_seven_required_resolutions():
    assert REMEDIATION_VERIFICATION_RESOLUTIONS == {
        "verified_resolved",
        "verified_no_change",
        "still_failed",
        "regressed",
        "evidence_unavailable",
        "approval_required",
        "verification_failed",
    }


def test_normalize_verification_resolution_degrades_unknown_to_failed():
    assert normalize_remediation_verification_resolution("regressed") == "regressed"
    assert (
        normalize_remediation_verification_resolution("totally-made-up")
        == "verification_failed"
    )
    assert normalize_remediation_verification_resolution("") == "verification_failed"


def test_verified_resolution_requires_link_to_exact_action_result():
    with pytest.raises(ValueError, match="action result"):
        build_remediation_verification(
            remediation_workflow_id="mm:remediate",
            remediation_run_id="run-1",
            target_workflow_id="mm:target",
            target_run_id="run-t",
            resolution="verified_resolved",
        )


def test_verification_records_action_link_and_before_after_states():
    payload = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-1",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="verified_resolved",
        verifies_action_result_ref="art_action_result",
        target_agent_run_id="tr_01",
        target_logical_step_id="run-tests",
        target_session_id="sess-1",
        target_state_before="failed",
        target_state_after_action="completed",
        target_state_after_stabilization="completed",
        verification_hint="re-check target terminal state",
    )
    assert payload["scope"] == "immediate_repair"
    assert payload["resolution"] == "verified_resolved"
    assert payload["verifiesActionResultRef"] == "art_action_result"
    assert payload["target"]["agentRunId"] == "tr_01"
    assert payload["target"]["logicalStepId"] == "run-tests"
    assert payload["target"]["sessionId"] == "sess-1"
    assert payload["targetState"] == {
        "before": "failed",
        "afterAction": "completed",
        "afterStabilization": "completed",
    }


def test_evidence_unavailable_verification_needs_no_action_link():
    payload = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-1",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="evidence_unavailable",
    )
    assert "verifiesActionResultRef" not in payload


def test_prevention_verification_is_separate_and_rejects_target_action_link():
    with pytest.raises(ValueError, match="prevention verification must not"):
        build_remediation_verification(
            remediation_workflow_id="mm:remediate",
            remediation_run_id="run-1",
            target_workflow_id="mm:target",
            target_run_id="run-t",
            resolution="verified_resolved",
            scope="prevention",
            verifies_action_result_ref="art_action_result",
        )
    payload = build_remediation_verification(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-1",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        resolution="verified_resolved",
        scope="prevention",
        prevention_change_ref="art_prevention_pr",
    )
    assert payload["scope"] == "prevention"
    assert payload["preventionChangeRef"] == "art_prevention_pr"


# --- Area 5: immediate repair vs. prevention separation safeguard -------------

def _repair(decision: str, outcome: str, **extra):
    return build_remediation_repair_decision(
        target_workflow_id="mm:target",
        pinned_run_id="run-t",
        decision=decision,
        decision_reason="reason",
        repair_outcome=outcome,
        **extra,
    )


def test_prevention_pr_cannot_relabel_target_as_repaired():
    # Repair failed, but a reviewable prevention PR was created. The target must
    # not be reported as resolved_after_action.
    repair = _repair("attempted", "still_failed",
                     action_request_ref="art_req",
                     action_result_ref="art_res",
                     verification_ref="art_ver")
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="skill_defect",
        summary="fix skill",
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/1",
    )
    resolution = derive_remediation_resolution(repair=repair, prevention=prevention)
    assert resolution != "resolved_after_action"
    assert resolution == "escalated"

    base = build_remediation_summary_block(
        target_workflow_id="mm:target",
        target_run_id="run-t",
        phase="escalated",
        mode="snapshot_then_follow",
        authority_mode="approval_gated",
        resolution="resolved_after_action",  # inconsistent claim
    )
    with pytest.raises(ValueError, match="prevention change must not relabel"):
        build_remediation_final_summary(
            summary=base,
            repair=repair,
            prevention=prevention,
            lock_release="released",
        )


def test_repaired_target_derives_resolved_after_action():
    repair = _repair("attempted", "repaired",
                     action_request_ref="art_req",
                     action_result_ref="art_res",
                     verification_ref="art_ver")
    assert derive_remediation_resolution(repair=repair) == "resolved_after_action"


def test_derive_resolution_maps_repair_states():
    assert derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted")
    ) == "no_action_needed"
    assert derive_remediation_resolution(
        repair=_repair("unsafe", "unsafe")
    ) == "unsafe_to_act"
    assert derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted"), lock_conflict=True
    ) == "lock_conflict"
    assert derive_remediation_resolution(
        repair=_repair("skipped", "not_attempted"), evidence_unavailable=True
    ) == "evidence_unavailable"


def test_final_summary_records_separate_prevention_verification_ref():
    repair = _repair("attempted", "repaired",
                     action_request_ref="art_req",
                     action_result_ref="art_res",
                     verification_ref="art_ver")
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="skill_defect",
        summary="fix skill",
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/1",
    )
    base = build_remediation_summary_block(
        target_workflow_id="mm:target",
        target_run_id="run-t",
        phase="resolved",
        mode="snapshot_then_follow",
        authority_mode="approval_gated",
        resolution="resolved_after_action",
    )
    summary = build_remediation_final_summary(
        summary=base,
        repair=repair,
        prevention=prevention,
        prevention_verification_ref="art_prevention_verification",
        lock_release="released",
    )
    assert summary["prevention"]["verificationRef"] == "art_prevention_verification"
    assert summary["repair"]["repairOutcome"] == "repaired"


# --- Area 4/AC7: bounded wall-clock and branch budgets ------------------------

def _loop_spec(**budget_overrides) -> RemediationLoopSpec:
    budgets = RemediationLoopBudgets(
        hardMaxAttempts=5,
        maxConsecutiveSemanticNoProgress=5,
        maxRepeatedFailureSignature=5,
        **budget_overrides,
    )
    return RemediationLoopSpec.model_validate(
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


def _loop_state(spec: RemediationLoopSpec, *, attempts: int = 1, branches: int = 0):
    return RemediationLoopState.model_validate(
        {
            "loopId": spec.loop_id,
            "attemptOrdinal": attempts,
            "phase": RemediationLoopPhase.CONTINUATION_DECIDING.value,
            "consumedBudgets": ConsumedRemediationBudgets(
                attempts=attempts, branches=branches
            ).model_dump(by_alias=True),
        }
    )


def test_wall_clock_budget_stops_further_attempts():
    spec = _loop_spec(maxWallClockSeconds=100)
    state = _loop_state(spec, attempts=1)
    decision = decide_remediation_continuation(
        spec=spec,
        state=state,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate",
        elapsed_seconds=150.0,
    )
    assert decision.continue_loop is False
    assert decision.reason == "wall_clock_budget_exhausted"
    assert decision.next_phase == RemediationLoopPhase.STOPPED_REMAINING_WORK


def test_wall_clock_budget_allows_when_under_limit():
    spec = _loop_spec(maxWallClockSeconds=100)
    state = _loop_state(spec, attempts=1)
    decision = decide_remediation_continuation(
        spec=spec,
        state=state,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate",
        elapsed_seconds=10.0,
    )
    assert decision.continue_loop is True
    assert decision.reason == "verification_requested_remediation"


def test_branch_budget_stops_and_counter_enforced():
    spec = _loop_spec(maxBranches=2)
    exhausted = _loop_state(spec, attempts=1, branches=2)
    decision = decide_remediation_continuation(
        spec=spec,
        state=exhausted,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate",
    )
    assert decision.continue_loop is False
    assert decision.reason == "branch_budget_exhausted"

    within = _loop_state(spec, attempts=1, branches=1)
    advanced = record_branch_created(spec=spec, state=within)
    assert advanced.consumed_budgets.branches == 2
    with pytest.raises(ValueError, match="branch budget exhausted"):
        record_branch_created(spec=spec, state=advanced)


# --- Area 1 / AC11: relationship panel, autonomous origin, operator takeover --

def test_autonomous_origin_is_operator_visible():
    origin = build_remediation_autonomous_origin(
        trigger_origin="policy",
        autonomous=True,
        policy_ref="admin_healer_default",
        created_by_workflow_id="mm:parent",
    )
    assert origin == {
        "triggerOrigin": "policy",
        "autonomous": True,
        "policyRef": "admin_healer_default",
        "createdByWorkflowId": "mm:parent",
    }
    with pytest.raises(ValueError):
        build_remediation_autonomous_origin(trigger_origin="made-up")


def test_operator_takeover_surface_defaults_available():
    takeover = build_remediation_operator_takeover()
    assert takeover == {"available": True, "requested": False}
    requested = build_remediation_operator_takeover(
        requested=True, actor="op@example.com", reason="pause autonomous healer"
    )
    assert requested["requested"] is True
    assert requested["actor"] == "op@example.com"
    assert requested["reason"] == "pause autonomous healer"


def test_action_detail_renders_inline_authority_fields():
    detail = build_remediation_action_detail(
        action_kind="execution.resume",
        risk_tier="medium",
        policy_decision="allowed",
        status="applied",
        expected_state="awaiting_slot",
        idempotency_key="idem-1",
        decision_actor="op@example.com",
        verification_required=True,
        verification_resolution="verified_resolved",
        before_evidence_refs=["execution:mm:target:run:r"],
        after_evidence_refs=["execution:mm:target:action:idem-1"],
    )
    assert detail["actionKind"] == "execution.resume"
    assert detail["expectedState"] == "awaiting_slot"
    assert detail["idempotencyKey"] == "idem-1"
    assert detail["decisionActor"] == "op@example.com"
    assert detail["verificationRequired"] is True
    assert detail["verificationResolution"] == "verified_resolved"
    assert detail["beforeEvidenceRefs"] == ["execution:mm:target:run:r"]


def test_relationship_panel_assembles_ac1_fields():
    panel = build_remediation_relationship_panel(
        remediation_workflow_id="mm:remediate",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        current_phase="acting",
        mode="snapshot_then_follow",
        authority_mode="approval_gated",
        instructions_summary="Investigate and repair the stuck target.",
        runtime={"mode": "codex", "model": "gpt-5", "profileId": "codex_team"},
        provider_profile={"id": "codex_team", "label": "Codex team"},
        launch_policy={"ref": "admin_healer_default", "mode": "on_demand_docker"},
        evidence_policy={"tailLines": 2000, "includeDiagnostics": True},
        autonomous_origin=build_remediation_autonomous_origin(
            trigger_origin="manual"
        ),
        operator_takeover=build_remediation_operator_takeover(),
        janitor_status="completed",
        janitor_refs={"cleanup": "art_cleanup"},
        lease_released=True,
        action_details=[
            build_remediation_action_detail(
                action_kind="execution.resume",
                risk_tier="medium",
                idempotency_key="idem-1",
                verification_required=True,
            )
        ],
    )
    assert panel["currentPhase"] == "acting"
    assert panel["instructionsSummary"].startswith("Investigate")
    assert panel["runtime"]["mode"] == "codex"
    assert panel["providerProfile"]["id"] == "codex_team"
    assert panel["launchPolicy"]["ref"] == "admin_healer_default"
    assert panel["evidencePolicy"]["tailLines"] == 2000
    assert panel["autonomousOrigin"]["triggerOrigin"] == "manual"
    assert panel["operatorTakeover"]["available"] is True
    assert panel["janitorState"] == {
        "status": "completed",
        "refs": {"cleanup": "art_cleanup"},
        "leaseReleased": True,
    }
    assert panel["actionDetails"][0]["actionKind"] == "execution.resume"


def test_relationship_panel_omits_secret_like_policy_values():
    panel = build_remediation_relationship_panel(
        remediation_workflow_id="mm:remediate",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        current_phase="diagnosing",
        mode="snapshot",
        authority_mode="observe_only",
        runtime={"mode": "codex", "token": "raw-secret"},
    )
    assert "token" not in panel["runtime"]
    assert "raw-secret" not in __import__("json").dumps(panel)


def test_consumed_budgets_branches_defaults_zero_for_in_flight_state():
    # In-flight state serialized before this change carries no branches field;
    # it must default to 0 without failing validation (replay safety).
    state = RemediationLoopState.model_validate(
        {
            "loopId": "loop-1",
            "attemptOrdinal": 0,
            "phase": RemediationLoopPhase.REMEDIATION_PENDING.value,
            "consumedBudgets": {"attempts": 0},
        }
    )
    assert state.consumed_budgets.branches == 0
