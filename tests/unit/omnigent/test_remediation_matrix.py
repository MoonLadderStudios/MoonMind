"""MoonLadderStudios/MoonMind#3626 operator-remediation matrix gate tests.

These cover the controlling support-matrix contract: the versioned required-row
catalog, the observed-evidence artifact binding (no caller-supplied ownership,
no self-asserted pass, delivery/repair separation, normal-product-path UI
journey authority, per-channel secret scans), and the fail-closed operator
release status (missing/stale/malformed/over-threshold blocks promotion; the
autonomous rollout gate stays closed).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.remediation_matrix import (
    AUTHORITY_MODES,
    GATE_AUTONOMOUS_ROLLOUT,
    GATE_MANUAL_DIAGNOSIS,
    GATE_MANUAL_MUTATION,
    PROHIBITED_UI_JOURNEY_MARKERS,
    REMEDIATION_ARTIFACT_SCHEMA_VERSION,
    REMEDIATION_MATRIX_VERSION,
    REMEDIATION_RELEASE_POLICY_VERSION,
    REMEDIATION_REPAIR_OUTCOMES,
    REMEDIATION_ROW_CATALOG,
    REMEDIATION_ROW_CATALOG_BY_ID,
    REQUIRED_REMEDIATION_EVIDENCE_KINDS,
    REQUIRED_REMEDIATION_LINEAGE_FIELDS,
    REQUIRED_REMEDIATION_MATRIX_ROWS,
    REQUIRED_REMEDIATION_RETAINED_CHANNELS,
    REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES,
    REQUIRED_REMEDIATION_TELEMETRY_GROUPS,
    REQUIRED_UI_JOURNEY_ASSERTIONS,
    RemediationMatrixError,
    evaluate_remediation_release,
    remediation_catalog_document,
    validate_remediation_evidence_artifact,
)
from moonmind.omnigent.remediation_matrix_conformance import (
    RemediationEvidenceBuildError,
    build_remediation_release_evidence,
)

NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)

IMAGES = {
    "server": "example/server@sha256:" + "1" * 64,
    "host": "example/host@sha256:" + "2" * 64,
}
ARCHITECTURES = ["linux/amd64"]
PROFILE_VERSION = "moonmind.omnigent.conformance/v4"
PROFILE_SHA256 = "a" * 64
POLICY_VERSION = "codex-static-launch-policy/v1"
AGENT_PROFILE_VERSION = "codex-agent-profile/v1"
REMEDIATION_POLICY_VERSION = "moonmind.omnigent.remediation-policy/v1"


def _clean_secret_scan() -> dict[str, object]:
    return {
        channel: {
            "status": "passed",
            "evidenceRef": f"secret-scans/{channel}.json",
            "sha256": "a" * 64,
            "schemaVersion": "moonmind.retained-evidence-secret-scan/v1",
            "contentType": "application/json",
            "sizeBytes": 2,
            "generatedAt": NOW.isoformat(),
        }
        for channel in REQUIRED_REMEDIATION_RETAINED_CHANNELS
    }


def _ui_journey(row) -> dict[str, object]:
    journey: dict[str, object] = {"journey": row.ui_journey}
    for assertion in REQUIRED_UI_JOURNEY_ASSERTIONS:
        journey[assertion] = True
    for marker in PROHIBITED_UI_JOURNEY_MARKERS:
        journey[marker] = False
    return journey


def _observed_row(row_id: str) -> dict[str, object]:
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    entry: dict[str, object] = {
        "row": row_id,
        "gate": row.gate,
        "observedDisposition": row.expected_outcome,
        "hostMode": row.host_modes[0],
        "targetProvenance": row.target_provenance[0],
        "remediationProvenance": row.remediation_provenance[0],
        "authorityMode": row.authority_mode,
        "egress": row.egress,
        "actionCapability": row.action_capability,
        "verificationCapability": row.verification_capability,
        "uiJourney": _ui_journey(row),
        "architecture": ARCHITECTURES[0],
        "images": dict(IMAGES),
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "remediationPolicyVersion": REMEDIATION_POLICY_VERSION,
        "thresholds": {
            key: {"within": True, "passed": 1, "total": 1}
            for key in row.thresholds
        },
        "timings": {
            "startedAt": (NOW - timedelta(seconds=1)).isoformat(),
            "completedAt": NOW.isoformat(),
            "durationMs": 1000,
            "phaseLatenciesMs": {"scenario": 1000},
        },
        "observations": {
            observation: True for observation in row.required_observations
        },
        "secretScan": _clean_secret_scan(),
        "lineage": {
            field: f"artifact://{row_id}/{field}"
            for field in REQUIRED_REMEDIATION_LINEAGE_FIELDS
        },
        "evidenceManifest": [
            {
                "type": record_type,
                "ref": f"source/{row_id}/{record_type}.json",
                "sha256": "a" * 64,
                "schemaVersion": f"moonmind.{record_type}/v1",
                "contentType": "application/json",
                "sizeBytes": 2,
                "generatedAt": NOW.isoformat(),
            }
            for record_type in sorted(REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES)
        ],
    }
    if row.is_mutation:
        entry["actionDelivery"] = {
            "status": "denied" if row.expected_outcome == "denied" else "delivered"
        }
        entry["repairVerification"] = {
            "outcome": (
                "approval_required"
                if row.expected_outcome == "denied"
                else "verified_resolved"
            )
        }
    if row.row_id == "remediation.autonomous.rollout-gate-closed":
        entry["uiJourney"]["normalCreateRequest"] = False
        entry["uiJourney"]["autonomousAdmissionDenied"] = True
    return entry


def _artifact(kind: str, row_ids: list[str] | None = None) -> dict[str, object]:
    if row_ids is None:
        row_ids = [
            r for r in REQUIRED_REMEDIATION_MATRIX_ROWS
            if REMEDIATION_ROW_CATALOG_BY_ID[r].evidence_kind == kind
        ]
    return {
        "schemaVersion": REMEDIATION_ARTIFACT_SCHEMA_VERSION,
        "matrixVersion": REMEDIATION_MATRIX_VERSION,
        "kind": kind,
        "producerVersion": "moonmind.operator-remediation-observer/v1",
        "rows": [_observed_row(row_id) for row_id in row_ids],
    }


def _stage_row_dependencies(tmp_path, artifact) -> None:
    for entry in artifact["rows"]:
        for record in entry["evidenceManifest"]:
            path = tmp_path / record["ref"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(
                {
                    "schemaVersion": record["schemaVersion"],
                    "generatedAt": record["generatedAt"],
                },
                sort_keys=True,
            ).encode()
            path.write_bytes(content)
            record["sha256"] = hashlib.sha256(content).hexdigest()
            record["sizeBytes"] = len(content)
        for channel, scan in entry["secretScan"].items():
            path = tmp_path / scan["evidenceRef"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                json.dumps(
                    {
                        "schemaVersion": scan["schemaVersion"],
                        "generatedAt": scan["generatedAt"],
                        "channel": channel,
                        "status": "passed",
                        "secretFindings": 0,
                        "prohibitedAuthorityFindings": 0,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            path.write_bytes(content)
            scan["sha256"] = hashlib.sha256(content).hexdigest()
            scan["sizeBytes"] = len(content)


def _stage_release(tmp_path, *, artifacts=None):
    """Write per-kind artifacts + the release document to ``tmp_path``."""

    if artifacts is None:
        artifacts = {kind: _artifact(kind) for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS}
    manifest = []
    for kind, artifact in artifacts.items():
        _stage_row_dependencies(tmp_path, artifact)
        content = json.dumps(artifact, sort_keys=True).encode()
        path = tmp_path / f"{kind}.json"
        path.write_bytes(content)
        manifest.append(
            {
                "kind": kind,
                "ref": f"{kind}.json",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    release = {
        "schemaVersion": REMEDIATION_RELEASE_POLICY_VERSION,
        "matrixVersion": REMEDIATION_MATRIX_VERSION,
        "generatedAt": NOW.isoformat(),
        "images": dict(IMAGES),
        "architectures": list(ARCHITECTURES),
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "remediationPolicyVersion": REMEDIATION_POLICY_VERSION,
        "telemetry": {group: {"observed": 1} for group in REQUIRED_REMEDIATION_TELEMETRY_GROUPS},
        "thresholds": {"withinLimits": True, "results": {"secretViolations": True}},
        "evidenceManifest": manifest,
    }
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    return release, release_path


def _artifact_bytes(artifact) -> bytes:
    return json.dumps(artifact, sort_keys=True).encode()


# ---------------------------------------------------------------------------
# Catalog invariants (section 1)
# ---------------------------------------------------------------------------


def test_catalog_covers_every_kind_with_unique_owned_rows() -> None:
    kinds = {row.evidence_kind for row in REMEDIATION_ROW_CATALOG}
    assert kinds == set(REQUIRED_REMEDIATION_EVIDENCE_KINDS)
    assert len(REQUIRED_REMEDIATION_MATRIX_ROWS) == len(set(REQUIRED_REMEDIATION_MATRIX_ROWS))
    # Every row names an owner and a gate class.
    for row in REMEDIATION_ROW_CATALOG:
        assert row.owner.strip()
        assert row.host_modes
        assert row.architectures
        assert row.gate in (
            GATE_MANUAL_DIAGNOSIS,
            GATE_MANUAL_MUTATION,
            GATE_AUTONOMOUS_ROLLOUT,
        )


def test_catalog_document_exposes_complete_owned_threshold_contract() -> None:
    document = remediation_catalog_document()
    assert document["issue"] == "MoonLadderStudios/MoonMind#3626"
    assert document["matrixVersion"] == REMEDIATION_MATRIX_VERSION
    assert {row["rowId"] for row in document["rows"]} == set(
        REQUIRED_REMEDIATION_MATRIX_ROWS
    )
    assert set(document["lineageFields"]) == set(REQUIRED_REMEDIATION_LINEAGE_FIELDS)
    assert set(document["retainedEvidenceChannels"]) == set(
        REQUIRED_REMEDIATION_RETAINED_CHANNELS
    )
    assert set(document["sourceRecordContract"]["requiredTypes"]) == set(
        REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES
    )
    assert set(document["telemetryGroups"]) == set(
        REQUIRED_REMEDIATION_TELEMETRY_GROUPS
    )
    for row in document["rows"]:
        assert row["owner"]
        assert row["hostModes"]
        assert row["architectures"]
        assert row["evidenceSchema"] == REMEDIATION_ARTIFACT_SCHEMA_VERSION
        assert all(
            threshold["rule"] == "all_observations_pass"
            for threshold in row["thresholds"].values()
        )


def test_repair_and_authority_vocabularies_match_canonical_owners() -> None:
    # Drift guard: matrix vocabularies must equal the authoritative sources so a
    # renamed outcome or authority mode fails here rather than silently diverging.
    from moonmind.workflows.temporal.remediation_actions import (
        _SUPPORTED_AUTHORITY_MODES,
    )
    from moonmind.workflows.temporal.remediation_verification import (
        REMEDIATION_VERIFICATION_OUTCOMES,
    )

    assert REMEDIATION_REPAIR_OUTCOMES == REMEDIATION_VERIFICATION_OUTCOMES
    assert set(AUTHORITY_MODES) == set(_SUPPORTED_AUTHORITY_MODES)


# ---------------------------------------------------------------------------
# Artifact binding (sections 2-4, AC5, AC7)
# ---------------------------------------------------------------------------


def test_valid_artifact_binds_its_owned_rows() -> None:
    kind = "diagnosisEvidence"
    observed_kind, rows = validate_remediation_evidence_artifact(
        _artifact(kind),
        expected_kind=kind,
        images=IMAGES,
        architectures=ARCHITECTURES,
        profile_version=PROFILE_VERSION,
        profile_sha256=PROFILE_SHA256,
        policy_version=POLICY_VERSION,
        agent_profile_version=AGENT_PROFILE_VERSION,
        remediation_policy_version=REMEDIATION_POLICY_VERSION,
    )
    assert observed_kind == kind
    assert rows == {
        r for r in REQUIRED_REMEDIATION_MATRIX_ROWS
        if REMEDIATION_ROW_CATALOG_BY_ID[r].evidence_kind == kind
    }


def _validate(artifact, kind="diagnosisEvidence"):
    return validate_remediation_evidence_artifact(
        artifact,
        expected_kind=kind,
        images=IMAGES,
        architectures=ARCHITECTURES,
        profile_version=PROFILE_VERSION,
        profile_sha256=PROFILE_SHA256,
        policy_version=POLICY_VERSION,
        agent_profile_version=AGENT_PROFILE_VERSION,
        remediation_policy_version=REMEDIATION_POLICY_VERSION,
    )


def test_caller_supplied_row_for_wrong_kind_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    # Splice in a row owned by a different kind.
    foreign = _observed_row("remediation.action.low-medium-risk-allowed")
    artifact["rows"].append(foreign)
    with pytest.raises(RemediationMatrixError, match="not owned by evidence kind"):
        _validate(artifact)


def test_unknown_protected_row_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    artifact["rows"][0]["row"] = "remediation.made.up.row"
    with pytest.raises(RemediationMatrixError, match="unknown protected row"):
        _validate(artifact)


def test_self_asserted_pass_without_observed_fields_is_rejected() -> None:
    kind = "diagnosisEvidence"
    artifact = {
        "schemaVersion": REMEDIATION_ARTIFACT_SCHEMA_VERSION,
        "matrixVersion": REMEDIATION_MATRIX_VERSION,
        "kind": kind,
        "producerVersion": "x",
        "rows": [{"row": "remediation.diagnosis.observe-only", "passed": True}],
    }
    with pytest.raises(RemediationMatrixError):
        _validate(artifact)


def test_wrong_expected_disposition_is_rejected() -> None:
    # A denial-only row cannot qualify by claiming it passed.
    artifact = _artifact("diagnosisEvidence")
    for entry in artifact["rows"]:
        if entry["row"] == "remediation.evidence.missing-unauthorized-denied":
            entry["observedDisposition"] = "passed"
    with pytest.raises(RemediationMatrixError, match="required disposition"):
        _validate(artifact)


def test_repair_outcome_cannot_masquerade_as_delivery_status(tmp_path) -> None:
    # A repair-verification outcome placed in the action-delivery field is
    # rejected because the two vocabularies are disjoint (AC5).
    kind = "actionApprovalEvidence"
    artifact = _artifact(kind)
    artifact["rows"][0]["actionDelivery"] = {"status": "verified_resolved"}
    with pytest.raises(RemediationMatrixError, match="delivery status is unrecognized"):
        _validate(artifact, kind=kind)


def test_reusing_one_object_for_delivery_and_repair_is_rejected(tmp_path) -> None:
    kind = "actionApprovalEvidence"
    artifact = _artifact(kind)
    shared = {"status": "delivered", "outcome": "verified_resolved"}
    artifact["rows"][0]["actionDelivery"] = shared
    artifact["rows"][0]["repairVerification"] = shared
    with pytest.raises(RemediationMatrixError, match="collapses action delivery"):
        _validate(artifact, kind=kind)


def test_mutation_row_missing_repair_field_is_rejected() -> None:
    kind = "actionApprovalEvidence"
    artifact = _artifact(kind)
    del artifact["rows"][0]["repairVerification"]
    with pytest.raises(RemediationMatrixError, match="separate evidence"):
        _validate(artifact, kind=kind)


@pytest.mark.parametrize("marker", PROHIBITED_UI_JOURNEY_MARKERS)
def test_prohibited_ui_authority_fails_the_row(marker) -> None:
    artifact = _artifact("diagnosisEvidence")
    artifact["rows"][0]["uiJourney"][marker] = True
    with pytest.raises(RemediationMatrixError, match="prohibited authority"):
        _validate(artifact)


@pytest.mark.parametrize("marker", PROHIBITED_UI_JOURNEY_MARKERS)
def test_missing_prohibited_ui_authority_observation_fails_the_row(marker) -> None:
    artifact = _artifact("diagnosisEvidence")
    del artifact["rows"][0]["uiJourney"][marker]
    with pytest.raises(RemediationMatrixError, match="prohibited authority"):
        _validate(artifact)


@pytest.mark.parametrize("assertion", REQUIRED_UI_JOURNEY_ASSERTIONS)
def test_missing_ui_assertion_fails_the_row(assertion) -> None:
    artifact = _artifact("diagnosisEvidence")
    artifact["rows"][0]["uiJourney"][assertion] = False
    with pytest.raises(RemediationMatrixError, match="missing assertion"):
        _validate(artifact)


def test_self_asserted_secret_scan_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    artifact["rows"][0]["secretScan"] = "clean"
    with pytest.raises(RemediationMatrixError, match="per-channel evidence"):
        _validate(artifact)


def test_threshold_not_within_limits_fails_the_row() -> None:
    artifact = _artifact("diagnosisEvidence")
    row = artifact["rows"][0]
    key = next(iter(REMEDIATION_ROW_CATALOG_BY_ID[row["row"]].thresholds))
    row["thresholds"][key] = {"within": False, "passed": 0, "total": 1}
    with pytest.raises(RemediationMatrixError, match="within"):
        _validate(artifact)


def test_missing_required_subscenario_fails_the_row() -> None:
    kind = "reliabilitySecurityEvidence"
    artifact = _artifact(kind)
    row = next(
        entry for entry in artifact["rows"]
        if entry["row"] == "remediation.reliability.cancellation-each-phase"
    )
    row["observations"]["cleanup"] = False
    with pytest.raises(RemediationMatrixError, match="lacks observed scenarios"):
        _validate(artifact, kind=kind)


def test_missing_required_lineage_field_fails_the_row() -> None:
    artifact = _artifact("diagnosisEvidence")
    del artifact["rows"][0]["lineage"]["firstMessageRef"]
    with pytest.raises(RemediationMatrixError, match="durable lineage fields"):
        _validate(artifact)


def test_missing_source_record_type_fails_the_row() -> None:
    artifact = _artifact("diagnosisEvidence")
    artifact["rows"][0]["evidenceManifest"] = artifact["rows"][0][
        "evidenceManifest"
    ][1:]
    with pytest.raises(RemediationMatrixError, match="lacks source evidence types"):
        _validate(artifact)


def test_missing_architecture_coverage_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    with pytest.raises(RemediationMatrixError, match="every released architecture"):
        validate_remediation_evidence_artifact(
            artifact,
            expected_kind="diagnosisEvidence",
            images=IMAGES,
            architectures=["linux/amd64", "linux/arm64"],
            profile_version=PROFILE_VERSION,
            profile_sha256=PROFILE_SHA256,
            policy_version=POLICY_VERSION,
            agent_profile_version=AGENT_PROFILE_VERSION,
            remediation_policy_version=REMEDIATION_POLICY_VERSION,
        )


def test_mutable_image_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    with pytest.raises(RemediationMatrixError, match="immutable"):
        validate_remediation_evidence_artifact(
            artifact,
            expected_kind="diagnosisEvidence",
            images={"server": "example/server:latest", "host": IMAGES["host"]},
            architectures=ARCHITECTURES,
            profile_version=PROFILE_VERSION,
            profile_sha256=PROFILE_SHA256,
            policy_version=POLICY_VERSION,
            agent_profile_version=AGENT_PROFILE_VERSION,
            remediation_policy_version=REMEDIATION_POLICY_VERSION,
        )


# ---------------------------------------------------------------------------
# Release status (section 5, AC7, AC9)
# ---------------------------------------------------------------------------


def test_missing_evidence_fails_closed_with_autonomous_gate_closed() -> None:
    status = evaluate_remediation_release(evidence=None, now=NOW)
    assert not status.manual_diagnosis_supported
    assert not status.manual_mutation_supported
    assert status.autonomous_rollout_authorized is False
    assert "remediation_release_evidence_missing" in status.blockers
    assert "autonomous_rollout_gate_closed" in status.blockers


def test_complete_release_supports_manual_but_never_autonomous(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    # Coverage is complete and threshold-compliant: manual diagnosis and manual
    # mutation are supported...
    assert status.covered_rows == set(REQUIRED_REMEDIATION_MATRIX_ROWS)
    assert status.manual_diagnosis_supported is True
    assert status.manual_mutation_supported is True
    # ...but the autonomous rollout gate is still a hard, fail-closed blocker.
    assert status.autonomous_rollout_authorized is False
    assert status.blockers == ("autonomous_rollout_gate_closed",)
    projection = status.as_dict()
    assert projection["autonomousRolloutAuthorized"] is False
    assert projection["promotionAllowed"] is False


def test_builder_derives_complete_release_coverage_telemetry_and_thresholds(
    tmp_path,
) -> None:
    release, _ = _stage_release(tmp_path)
    document = build_remediation_release_evidence(
        release=release,
        artifact_paths=[
            tmp_path / f"{kind}.json"
            for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
        ],
        generated_at=NOW,
    )

    assert document["issue"] == "MoonLadderStudios/MoonMind#3626"
    assert document["matrixRows"] == list(REQUIRED_REMEDIATION_MATRIX_ROWS)
    assert set(document["telemetry"]) == set(
        REQUIRED_REMEDIATION_TELEMETRY_GROUPS
    )
    assert document["thresholds"]["withinLimits"] is True
    assert all(document["thresholds"]["results"].values())
    assert len(document["evidenceManifest"]) == len(
        REQUIRED_REMEDIATION_EVIDENCE_KINDS
    )


def test_builder_rejects_incomplete_observed_matrix(tmp_path) -> None:
    release, _ = _stage_release(tmp_path)
    paths = [
        tmp_path / f"{kind}.json"
        for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
        if kind != "reliabilitySecurityEvidence"
    ]
    with pytest.raises(RemediationEvidenceBuildError, match="missingKinds"):
        build_remediation_release_evidence(
            release=release,
            artifact_paths=paths,
            generated_at=NOW,
        )


def test_release_builder_cli_stages_nested_evidence_for_post_cleanup_validation(
    tmp_path, monkeypatch
) -> None:
    release, _ = _stage_release(tmp_path)
    release_config = tmp_path / "release-config.json"
    release_config.write_text(json.dumps(release), encoding="utf-8")
    output = tmp_path / "bundle" / "release.json"
    script = (
        Path(__file__).parents[3]
        / "tools"
        / "build_operator_remediation_release_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("remediation_builder_cli", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = [
        str(script),
        "--release",
        str(release_config),
        "--output",
        str(output),
    ]
    for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS:
        argv.extend(["--artifact", str(tmp_path / f"{kind}.json")])
    monkeypatch.setattr(sys, "argv", argv)

    assert module.main() == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    status = evaluate_remediation_release(
        evidence=document,
        evidence_document_path=output,
        evidence_ref=str(output),
        now=datetime.fromisoformat(document["generatedAt"]),
    )
    assert status.manual_mutation_supported is True
    assert status.blockers == ("autonomous_rollout_gate_closed",)


def test_stale_release_evidence_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["generatedAt"] = (NOW - timedelta(days=30)).isoformat()
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_release_evidence_stale" in status.blockers
    assert status.manual_mutation_supported is False


def test_missing_telemetry_group_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["telemetry"].pop(REQUIRED_REMEDIATION_TELEMETRY_GROUPS[0])
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_telemetry_required" in status.blockers


def test_over_threshold_release_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["thresholds"] = {"withinLimits": False, "results": {"secretViolations": False}}
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "rollback_threshold_exceeded_or_missing" in status.blockers


def test_digest_mismatch_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["evidenceManifest"][0]["sha256"] = "0" * 64
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "evidence_manifest_digest_mismatch" in status.blockers
    assert status.manual_diagnosis_supported is False


def test_nested_source_record_digest_mismatch_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    first_artifact_path = tmp_path / release["evidenceManifest"][0]["ref"]
    artifact = json.loads(first_artifact_path.read_text(encoding="utf-8"))
    source_path = tmp_path / artifact["rows"][0]["evidenceManifest"][0]["ref"]
    source_path.write_text('{"schemaVersion":"tampered/v1"}', encoding="utf-8")

    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )

    assert "evidence_row_binding_invalid" in status.blockers
    assert status.manual_mutation_supported is False


def test_split_evidence_kind_is_rejected(tmp_path) -> None:
    # Two artifacts share a kind but own disjoint rows -> split coverage.
    kind = "diagnosisEvidence"
    kind_rows = [
        r for r in REQUIRED_REMEDIATION_MATRIX_ROWS
        if REMEDIATION_ROW_CATALOG_BY_ID[r].evidence_kind == kind
    ]
    first = _artifact(kind, kind_rows[:2])
    second = _artifact(kind, kind_rows[2:])
    artifacts = {k: _artifact(k) for k in REQUIRED_REMEDIATION_EVIDENCE_KINDS}
    artifacts[kind] = first
    release, release_path = _stage_release(tmp_path, artifacts=artifacts)
    # Stage the second same-kind artifact and append it to the manifest.
    _stage_row_dependencies(tmp_path, second)
    content = _artifact_bytes(second)
    (tmp_path / "diagnosisEvidence-2.json").write_bytes(content)
    release["evidenceManifest"].append(
        {
            "kind": kind,
            "ref": "diagnosisEvidence-2.json",
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "split_evidence_kind_rejected" in status.blockers
    assert "matrix_row_coverage_incomplete" in status.blockers


def test_incomplete_kind_coverage_blocks_promotion(tmp_path) -> None:
    artifacts = {
        kind: _artifact(kind)
        for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
        if kind != "reliabilitySecurityEvidence"
    }
    release, release_path = _stage_release(tmp_path, artifacts=artifacts)
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "complete_evidence_kind_coverage_required" in status.blockers
    assert "matrix_row_coverage_incomplete" in status.blockers
    assert status.manual_mutation_supported is False


def test_release_requires_evidence_document_path_for_manifest(tmp_path) -> None:
    release, _ = _stage_release(tmp_path)
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=None,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_evidence_document_path_required" in status.blockers
