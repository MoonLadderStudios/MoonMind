"""MoonLadderStudios/MoonMind#3518 cutover gate tests."""

from datetime import datetime, timedelta, timezone

from moonmind.omnigent.cutover import (
    CUTOVER_POLICY_VERSION,
    CutoverPhase,
    evaluate_promotion,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _evidence() -> dict[str, object]:
    return {
        "schemaVersion": CUTOVER_POLICY_VERSION,
        "generatedAt": NOW.isoformat(),
        "profilePolicyReady": True,
        "allRequiredCasesPassed": True,
        "secretScansPassed": True,
        "temporalReplayPassed": True,
        "historicalReadsPassed": True,
        "capacitySingleOwnerPassed": True,
        "thresholds": {"withinLimits": True},
        "evidenceRefs": ["artifact://protected-live/codex-omnigent/v1"],
    }


def test_promotion_fails_closed_without_live_evidence() -> None:
    decision = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.CREATE_DEFAULT,
        evidence=None,
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.blockers == ("live_conformance_evidence_missing",)


def test_promotion_rejects_stale_evidence_and_failed_thresholds() -> None:
    evidence = _evidence()
    evidence["generatedAt"] = (NOW - timedelta(days=8)).isoformat()
    evidence["thresholds"] = {"withinLimits": False}
    decision = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.CREATE_DEFAULT,
        evidence=evidence,
        now=NOW,
    )
    assert decision.allowed is False
    assert "live_conformance_evidence_stale" in decision.blockers
    assert "rollback_threshold_exceeded_or_missing" in decision.blockers


def test_promotion_requires_single_phase_and_complete_authority_handoffs() -> None:
    evidence = _evidence()
    evidence["capacitySingleOwnerPassed"] = False
    decision = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.BROAD_DEFAULT,
        evidence=evidence,
        now=NOW,
    )
    assert decision.allowed is False
    assert "promotion_must_advance_one_phase" in decision.blockers
    assert "capacitySingleOwnerPassed_required" in decision.blockers


def test_fresh_complete_evidence_allows_next_phase_and_rollback_is_unconditional() -> None:
    promoted = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.CREATE_DEFAULT,
        evidence=_evidence(),
        now=NOW,
    )
    assert promoted.allowed is True
    rolled_back = evaluate_promotion(
        current_phase=CutoverPhase.BROAD_DEFAULT,
        requested_phase=CutoverPhase.OPT_IN,
        evidence=None,
        now=NOW,
    )
    assert rolled_back.allowed is True
