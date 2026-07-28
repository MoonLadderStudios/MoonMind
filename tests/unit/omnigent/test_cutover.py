"""MoonLadderStudios/MoonMind#3518 cutover gate tests."""

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.cutover import (
    CUTOVER_POLICY_VERSION,
    REQUIRED_TELEMETRY_GROUPS,
    CutoverPhase,
    effective_phase,
    evaluate_promotion,
    select_runtime,
)
from moonmind.omnigent.conformance import PROFILE_SHA256, PROFILE_VERSION


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
        "authorizedPhase": "CREATE_DEFAULT",
        "currentPhase": "OPT_IN",
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "images": {
            "server": "example/server@sha256:" + "1" * 64,
            "host": "example/host@sha256:" + "2" * 64,
        },
        "architectures": ["linux/amd64"],
        "telemetry": {
            group: {"sampleCount": 10} for group in REQUIRED_TELEMETRY_GROUPS
        },
        "thresholds": {
            "withinLimits": True,
            "results": {"launchSuccessRate": True, "secretViolations": True},
        },
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
    evidence["thresholds"] = {
        "withinLimits": False,
        "results": {"launchSuccessRate": False},
    }
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


def test_effective_phase_cannot_be_promoted_by_environment_alone() -> None:
    status = effective_phase(
        env={"MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default"},
        now=NOW,
    )
    assert status.configured_phase is CutoverPhase.CREATE_DEFAULT
    assert status.deployed_phase is CutoverPhase.OPT_IN
    assert status.phase is CutoverPhase.OPT_IN
    assert status.blockers == ("live_conformance_evidence_missing",)


def test_effective_phase_loads_exact_authorized_local_evidence(tmp_path) -> None:
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(_evidence()), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": path.as_uri(),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.CREATE_DEFAULT
    assert status.blockers == ()
    assert status.as_dict()["images"]["host"].endswith("2" * 64)


def test_effective_phase_uses_durable_deployed_phase_for_sequential_promotion(
    tmp_path,
) -> None:
    evidence = _evidence()
    evidence["authorizedPhase"] = "BROAD_DEFAULT"
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "broad_default",
            "MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE": "opt_in",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.OPT_IN
    assert "promotion_must_advance_one_phase" in status.blockers


def test_effective_phase_requires_evidence_to_match_deployed_phase(tmp_path) -> None:
    evidence = _evidence()
    evidence["authorizedPhase"] = "SCHEDULE_DEFAULT"
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "schedule_default",
            "MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.CREATE_DEFAULT
    assert "evidence_current_phase_mismatch" in status.blockers


def test_denied_promotion_preserves_deployed_phase_instead_of_resetting_defaults(
    tmp_path,
) -> None:
    evidence = _evidence()
    evidence["authorizedPhase"] = "SCHEDULE_DEFAULT"
    evidence["currentPhase"] = "CREATE_DEFAULT"
    evidence["allRequiredCasesPassed"] = False
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "schedule_default",
            "MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.CREATE_DEFAULT
    assert "allRequiredCasesPassed_required" in status.blockers


def test_phase_six_requires_separate_code_removal_evidence() -> None:
    evidence = _evidence()
    evidence["authorizedPhase"] = "DIRECT_LAUNCH_REMOVED"
    evidence["currentPhase"] = "DIRECT_LAUNCH_DISABLED"

    decision = evaluate_promotion(
        current_phase=CutoverPhase.DIRECT_LAUNCH_DISABLED,
        requested_phase=CutoverPhase.DIRECT_LAUNCH_REMOVED,
        evidence=evidence,
        now=NOW,
    )

    assert decision.allowed is False
    assert "direct_launch_retirement_not_built" in decision.blockers
    assert "directLaunchCodeRemoved_required" in decision.blockers
    assert "directLaunchUiRemoved_required" in decision.blockers
    assert "directLaunchConfigRemoved_required" in decision.blockers
    assert "duplicateCapacityOwnershipRemoved_required" in decision.blockers
    assert "retirement_evidence_refs_required" in decision.blockers


def test_effective_phase_rejects_stale_or_wrong_phase_evidence(tmp_path) -> None:
    evidence = _evidence()
    evidence["authorizedPhase"] = "BROAD_DEFAULT"
    evidence["generatedAt"] = (NOW - timedelta(days=8)).isoformat()
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.OPT_IN
    assert "evidence_authorized_phase_mismatch" in status.blockers
    assert "live_conformance_evidence_stale" in status.blockers
