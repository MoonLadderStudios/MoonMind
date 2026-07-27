from datetime import datetime, timezone
import json
from types import SimpleNamespace

from moonmind.workflows.executions.omnigent_codex_rollout import (
    decide_codex_path,
    project_codex_release_status,
    sign_codex_cutover_evidence,
)


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
SIGNING_KEY = "test-only-cutover-evidence-key"


def _flags(**updates):
    evidence = {
        "generation": "v1", "matrixVersion": "moonmind.omnigent.codex-support/v1",
        "reportRef": "artifact://conformance/codex-v1.json",
        "reportSha256": "a" * 64, "recordedAt": NOW.isoformat(),
        "liveConformancePassed": True, "replayPassed": True,
        "historicalReadsPassed": True, "secretViolations": 0,
        "readinessSuccessRatio": .999, "terminalHarvestSuccessRatio": .999,
        "cleanupFailureRatio": .001, "replayGapRatio": 0,
        "controlDeliverySuccessRatio": .999,
        "artifactCompletenessRatio": .999, "checkpointSuccessRatio": .999,
        "remediationSuccessRatio": .999, "ragSuccessRatio": .999,
        "janitorFailureRatio": .001, "policyDenials": 0, "readinessDenials": 0,
        "qualifiedCaseIds": ["create.static.amd64", "create.ondemand.amd64"],
        "hostModes": ["static", "ondemand"], "architectures": ["linux/amd64"],
        "imageDigests": ["example.invalid/codex@sha256:" + "b" * 64],
    }
    evidence = sign_codex_cutover_evidence(evidence, signing_key=SIGNING_KEY)
    values = dict(omnigent_codex_rollout_phase="create_default", omnigent_codex_rollout_generation="v1", omnigent_codex_conformance_evidence_json=json.dumps(evidence), omnigent_codex_conformance_signing_key=SIGNING_KEY, omnigent_codex_conformance_max_age_hours=168)
    values.update(updates)
    return SimpleNamespace(**values)


def test_create_default_is_generation_bound_and_preserves_evidence() -> None:
    decision = decide_codex_path(feature_flags=_flags(), selection="automatic", submission="create", now=NOW)
    assert decision.admitted and decision.selected_path == "omnigent"
    assert decision.matrix_version == "moonmind.omnigent.codex-support/v1"


def test_explicit_omnigent_never_falls_back_when_evidence_is_missing_or_stale() -> None:
    for flags in (_flags(omnigent_codex_conformance_evidence_json=""), _flags(omnigent_codex_conformance_max_age_hours=1)):
        decision = decide_codex_path(feature_flags=flags, selection="omnigent", submission="create", now=NOW.replace(day=28))
        assert not decision.admitted and decision.selected_path == "none"
        assert decision.reason_code == "conformance_gate_failed"


def test_schedules_move_only_in_scheduled_phase_and_direct_can_be_disabled() -> None:
    assert decide_codex_path(feature_flags=_flags(), selection="automatic", submission="schedule", now=NOW).selected_path == "direct"
    assert decide_codex_path(feature_flags=_flags(omnigent_codex_rollout_phase="scheduled_default"), selection="automatic", submission="schedule", now=NOW).selected_path == "omnigent"
    blocked = decide_codex_path(feature_flags=_flags(omnigent_codex_rollout_phase="direct_disabled"), selection="direct", submission="create", now=NOW)
    assert not blocked.admitted and blocked.reason_code == "direct_launch_disabled"


def test_threshold_failures_block_promotion() -> None:
    raw = json.loads(_flags().omnigent_codex_conformance_evidence_json)
    for field, value in (("secretViolations", 1), ("replayGapRatio", .001), ("readinessSuccessRatio", .98), ("cleanupFailureRatio", .02)):
        failed = dict(raw); failed[field] = value
        failed = sign_codex_cutover_evidence(failed, signing_key=SIGNING_KEY)
        decision = decide_codex_path(feature_flags=_flags(omnigent_codex_conformance_evidence_json=json.dumps(failed)), selection="automatic", submission="create", now=NOW)
        assert not decision.admitted and decision.selected_path == "none"


def test_release_status_uses_the_same_fresh_evidence_gate() -> None:
    ready = project_codex_release_status(feature_flags=_flags(), now=NOW)
    assert ready.promotion_ready
    assert ready.evidence_ref == "artifact://conformance/codex-v1.json"

    stale = project_codex_release_status(
        feature_flags=_flags(omnigent_codex_conformance_max_age_hours=1),
        now=NOW.replace(day=28),
    )
    assert not stale.promotion_ready
    assert stale.reason_code == "conformance_gate_failed"


def test_unsigned_or_tampered_operator_json_cannot_promote() -> None:
    raw = json.loads(_flags().omnigent_codex_conformance_evidence_json)
    raw["readinessSuccessRatio"] = 1
    decision = decide_codex_path(
        feature_flags=_flags(omnigent_codex_conformance_evidence_json=json.dumps(raw)),
        selection="omnigent",
        submission="create",
        now=NOW,
    )
    assert not decision.admitted
    assert decision.evidence_ref is None


def test_internal_cohort_precedes_general_default() -> None:
    flags = _flags(omnigent_codex_rollout_phase="internal")
    assert decide_codex_path(
        feature_flags=flags, selection="automatic", submission="create",
        cohort="internal", now=NOW,
    ).selected_path == "omnigent"
    assert decide_codex_path(
        feature_flags=flags, selection="automatic", submission="create",
        cohort="general", now=NOW,
    ).selected_path == "direct"
