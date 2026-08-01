"""MoonLadderStudios/MoonMind#3518 cutover gate tests."""

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

import json

from moonmind.omnigent.cutover import (
    ARTIFACT_SCHEMA_VERSION,
    CUTOVER_POLICY_VERSION,
    MATRIX_VERSION,
    REQUIRED_EVIDENCE_KINDS,
    REQUIRED_MATRIX_ROWS,
    REQUIRED_TELEMETRY_GROUPS,
    ROW_CATALOG,
    CutoverPhase,
    effective_phase,
    evaluate_promotion,
    select_runtime,
)
from moonmind.omnigent.conformance import (
    PROFILE_SHA256,
    PROFILE_VERSION,
    REQUIRED_EVIDENCE_CHANNELS,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)

IMAGES = {
    "server": "example/server@sha256:" + "1" * 64,
    "host": "example/host@sha256:" + "2" * 64,
}
POLICY_VERSION = "codex-static-launch-policy/v1"
AGENT_PROFILE_VERSION = "codex-agent-profile/v1"


def _clean_secret_scan() -> dict[str, object]:
    return {
        channel: {"status": "passed", "evidenceRef": f"evidence/{channel}.json"}
        for channel in REQUIRED_EVIDENCE_CHANNELS
    }


def _observed_row(row_id: str) -> dict[str, object]:
    row = ROW_CATALOG[row_id]
    return {
        "row": row_id,
        "hostMode": row.host_modes[0],
        "architecture": "linux/amd64",
        "images": dict(IMAGES),
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "runtimeProvenance": row.provenance[0],
        "observedResult": "passed",
        "secretScan": _clean_secret_scan(),
    }


def _artifact_bytes(kind: str, row_ids: list[str] | None = None) -> bytes:
    if row_ids is None:
        row_ids = [r for r in REQUIRED_MATRIX_ROWS if ROW_CATALOG[r].kind == kind]
    return json.dumps(
        {
            "schemaVersion": ARTIFACT_SCHEMA_VERSION,
            "kind": kind,
            "producerVersion": "moonmind.codex-omnigent-observer/v1",
            "rows": [_observed_row(row_id) for row_id in row_ids],
        },
        sort_keys=True,
    ).encode()


def _evidence(tmp_path=None) -> dict[str, object]:
    manifest = []
    for kind in REQUIRED_EVIDENCE_KINDS:
        slug = kind
        content = _artifact_bytes(kind)
        ref = f"artifact://protected-live/codex-omnigent/{slug}"
        if tmp_path is not None:
            path = tmp_path / f"{slug}.json"
            path.write_bytes(content)
            ref = path.name
        manifest.append(
            {
                "kind": kind,
                "ref": ref,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
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
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "matrixVersion": MATRIX_VERSION,
        "matrixRows": list(REQUIRED_MATRIX_ROWS),
        "images": dict(IMAGES),
        "architectures": ["linux/amd64"],
        "telemetry": {
            group: {"sampleCount": 10} for group in REQUIRED_TELEMETRY_GROUPS
        },
        "thresholds": {
            "withinLimits": True,
            "results": {"launchSuccessRate": True, "secretViolations": True},
        },
        "evidenceRefs": [item["ref"] for item in manifest],
        "evidenceManifest": manifest,
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


def test_promotion_rejects_unbound_or_incomplete_evidence_provenance() -> None:
    evidence = _evidence()
    evidence["evidenceManifest"] = [
        {
            "kind": "submissionMatrix",
            "ref": evidence["evidenceRefs"][0],
            "sha256": "not-a-digest",
        }
    ]

    decision = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.CREATE_DEFAULT,
        evidence=evidence,
        now=NOW,
    )

    assert decision.allowed is False
    assert "provenance_bound_evidence_manifest_invalid" in decision.blockers
    assert "complete_evidence_kind_coverage_required" in decision.blockers


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

    with pytest.raises(ValueError, match="codex_direct_launch_disabled"):
        select_runtime(
            authored_runtime="codex",
            configured_default="codex_cli",
            phase=CutoverPhase.DIRECT_LAUNCH_DISABLED,
        )


def test_broad_default_only_applies_to_non_create_and_non_schedule_surfaces() -> None:
    selected = select_runtime(
        authored_runtime=None,
        configured_default="codex_cli",
        phase=CutoverPhase.BROAD_DEFAULT,
        submission_kind="workflow_proposal",
    )
    assert selected.runtime_id == "omnigent"


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
    path.write_text(__import__("json").dumps(_evidence(tmp_path)), encoding="utf-8")

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


def test_effective_phase_rejects_missing_or_tampered_manifest_evidence(
    tmp_path,
) -> None:
    evidence = _evidence(tmp_path)
    missing = tmp_path / str(evidence["evidenceRefs"][0])
    missing.unlink()
    path = tmp_path / "release.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")

    missing_status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )
    assert missing_status.phase is CutoverPhase.OPT_IN
    assert "evidence_manifest_ref_unreadable" in missing_status.blockers

    missing.write_text("tampered", encoding="utf-8")
    tampered_status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )
    assert tampered_status.phase is CutoverPhase.OPT_IN
    assert "evidence_manifest_digest_mismatch" in tampered_status.blockers


def test_effective_phase_rejects_self_asserted_row_artifact_with_matching_digest(
    tmp_path,
) -> None:
    """A digest-consistent artifact whose observed rows do not pass fails closed.

    This is the MoonLadderStudios/MoonMind#3564 hardening: promotion must not
    rely on document-level booleans or manifest digest integrity alone. The
    consumer re-parses each artifact and re-validates the observed per-row
    result even when the recorded SHA-256 matches the tampered bytes.
    """

    evidence = _evidence(tmp_path)
    artifact = tmp_path / str(evidence["evidenceRefs"][0])
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["rows"][0]["observedResult"] = "failed"
    tampered = json.dumps(payload, sort_keys=True).encode()
    artifact.write_bytes(tampered)
    # Re-derive the manifest digest so digest integrity is not what fails.
    evidence["evidenceManifest"][0]["sha256"] = hashlib.sha256(tampered).hexdigest()
    path = tmp_path / "release.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.OPT_IN
    assert "evidence_manifest_digest_mismatch" not in status.blockers
    assert "evidence_row_binding_invalid" in status.blockers
    assert "matrix_row_coverage_incomplete" in status.blockers


def test_effective_phase_rejects_split_evidence_for_one_kind(tmp_path) -> None:
    """Two digest-valid artifacts sharing a kind cannot union into coverage.

    A hand-authored or mutated promotion document can splice partial results
    from separate runs or producers into two artifacts of the same evidence
    kind that own disjoint rows. Row overlap alone would not catch that, so the
    launch-authority consumer must reject the duplicate kind before unioning
    its rows.
    """

    evidence = _evidence(tmp_path)
    kind = "submissionMatrix"
    row_ids = [r for r in REQUIRED_MATRIX_ROWS if ROW_CATALOG[r].kind == kind]
    assert len(row_ids) >= 2
    first_rows, second_rows = row_ids[:1], row_ids[1:]

    manifest = [
        item for item in evidence["evidenceManifest"] if item["kind"] != kind
    ]
    for index, subset in enumerate((first_rows, second_rows)):
        content = _artifact_bytes(kind, subset)
        name = f"{kind}-{index}.json"
        (tmp_path / name).write_bytes(content)
        manifest.append(
            {
                "kind": kind,
                "ref": name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    evidence["evidenceManifest"] = manifest
    evidence["evidenceRefs"] = [item["ref"] for item in manifest]
    path = tmp_path / "release.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.OPT_IN
    assert "split_evidence_kind_rejected" in status.blockers


def test_effective_phase_requires_evidence_for_every_released_architecture(
    tmp_path,
) -> None:
    """A row observed on only one of several released architectures fails closed."""

    evidence = _evidence(tmp_path)
    evidence["architectures"] = ["linux/amd64", "linux/arm64"]
    path = tmp_path / "release.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": str(path),
        },
        now=NOW,
    )

    assert status.phase is CutoverPhase.OPT_IN
    assert "evidence_row_binding_invalid" in status.blockers
    assert "matrix_row_coverage_incomplete" in status.blockers


def test_effective_phase_exposes_matrix_version_and_rows(tmp_path) -> None:
    path = tmp_path / "release.json"
    path.write_text(json.dumps(_evidence(tmp_path)), encoding="utf-8")

    status = effective_phase(
        env={
            "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE": "create_default",
            "MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF": path.as_uri(),
        },
        now=NOW,
    )

    projection = status.as_dict()
    assert status.phase is CutoverPhase.CREATE_DEFAULT
    assert projection["matrixVersion"] == MATRIX_VERSION
    assert projection["matrixRows"] == list(REQUIRED_MATRIX_ROWS)
    assert projection["launchPolicyVersion"] == POLICY_VERSION
    assert projection["agentProfileVersion"] == AGENT_PROFILE_VERSION


def test_effective_phase_uses_durable_deployed_phase_for_sequential_promotion(
    tmp_path,
) -> None:
    evidence = _evidence(tmp_path)
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
    evidence = _evidence(tmp_path)
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
    evidence = _evidence(tmp_path)
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
    evidence = _evidence(tmp_path)
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
