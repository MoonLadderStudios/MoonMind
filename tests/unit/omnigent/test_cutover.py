"""MoonLadderStudios/MoonMind#3518 cutover gate tests."""

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.cutover import (
    CUTOVER_POLICY_VERSION,
    CutoverPhase,
    evaluate_promotion,
    select_runtime,
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


def test_create_and_schedule_defaults_advance_in_separate_phases() -> None:
    create = select_runtime(
        authored_runtime=None,
        configured_default="codex_cli",
        phase=CutoverPhase.CREATE_DEFAULT,
    )
    schedule = select_runtime(
        authored_runtime=None,
        configured_default="codex_cli",
        phase=CutoverPhase.CREATE_DEFAULT,
        submission_kind="schedule",
    )
    assert create.runtime_id == "omnigent"
    assert schedule.runtime_id == "codex_cli"
    assert create.as_dict()["authored"] is False


def test_explicit_selection_is_preserved_and_direct_launch_eventually_rejected() -> None:
    explicit = select_runtime(
        authored_runtime="omnigent",
        configured_default="codex_cli",
        phase=CutoverPhase.OPT_IN,
    )
    assert explicit.runtime_id == "omnigent"
    assert explicit.authored is True

    with pytest.raises(ValueError, match="codex_direct_launch_disabled"):
        select_runtime(
            authored_runtime="codex_cli",
            configured_default="codex_cli",
            phase=CutoverPhase.DIRECT_LAUNCH_DISABLED,
        )
