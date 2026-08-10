from datetime import datetime, timezone
import hashlib
import json

import pytest

from moonmind.omnigent.operator_remediation_gate import (
    EVIDENCE_SCHEMA_VERSION, REQUIRED_EVIDENCE_FIELDS, REQUIRED_ROW_CATALOG,
    RemediationGateError, allows_autonomous_mutation, build_combined_matrix, catalog_document, release_status,
    validate_row_artifact,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _ref(tmp_path, name):
    path = tmp_path / name
    path.write_text("observed evidence")
    return {"ref": path.resolve().as_uri(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "contentType": "application/json"}


def _artifact(tmp_path, row):
    payload = {"schemaVersion": EVIDENCE_SCHEMA_VERSION, "rowId": row.row_id,
        "owner": row.owner, "observedResult": "passed", "generatedAt": NOW.isoformat(),
        "evidenceKind": row.evidence_kind,
        "targetRuntimeProvenance": row.target_provenance,
        "remediationRuntimeProvenance": row.remediation_provenance,
        "hostMode": row.host_modes[0], "architecture": row.architectures[0],
        "actionCapability": row.action_capability, "verificationCapability": row.verification_capability,
        "authority": row.authority, "uiJourney": row.ui_journey, "liveResourcesGone": True,
        "timings": {"durationSeconds": 1}, "thresholds": {"withinLimits": True},
        "secretScan": {"status": "passed", "prohibitedAuthorityFound": False}}
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in payload:
            payload[field] = _ref(tmp_path, f"{row.row_id}-{field}.json")
    return payload


def test_catalog_is_complete_machine_readable_and_has_authoritative_owners():
    document = catalog_document()
    assert document["matrixVersion"].endswith("/v1")
    assert len(document["rows"]) == len(REQUIRED_ROW_CATALOG) >= 35
    assert len({row["rowId"] for row in document["rows"]}) == len(document["rows"])
    assert all(row["owner"] and row["thresholds"] and row["gates"] for row in document["rows"])


def test_row_rejects_self_asserted_pass_and_caller_owned_row(tmp_path):
    row = REQUIRED_ROW_CATALOG[0]
    payload = _artifact(tmp_path, row)
    payload["owner"] = "caller"
    with pytest.raises(RemediationGateError, match="owner"):
        validate_row_artifact(payload, now=NOW)
    payload["owner"] = row.owner
    payload["verificationEvidence"] = {"passed": True}
    with pytest.raises(RemediationGateError, match="refs require"):
        validate_row_artifact(payload, now=NOW)


def test_release_status_blocks_incomplete_matrix():
    status = release_status(artifact_paths=[], release_inputs={"immutable": True, "version": "v1"}, now=NOW)
    assert status["status"] == "blocked"
    assert status["autonomousMutationAllowed"] is False
    assert "missingRows" in status["blockers"][0]
    assert allows_autonomous_mutation(status) is False
    assert allows_autonomous_mutation({"status": "supported", "autonomousMutationAllowed": True}) is False


def test_combined_matrix_is_derived_from_all_digest_validated_rows(tmp_path):
    paths = []
    for index, row in enumerate(REQUIRED_ROW_CATALOG):
        path = tmp_path / f"row-{index}.json"
        path.write_text(json.dumps(_artifact(tmp_path, row), sort_keys=True))
        paths.append(path)
    result = build_combined_matrix(artifact_paths=paths, release_inputs={"immutable": True, "version": "release-1"}, now=NOW)
    assert result["status"] == "supported"
    assert result["autonomousMutationAllowed"] is True
    assert set(result["rows"]) == {row.row_id for row in REQUIRED_ROW_CATALOG}
    assert allows_autonomous_mutation(result) is True


def test_stale_or_over_threshold_artifact_fails_closed(tmp_path):
    row = REQUIRED_ROW_CATALOG[0]
    payload = _artifact(tmp_path, row)
    payload["timings"]["durationSeconds"] = row.max_duration_seconds + 1
    with pytest.raises(RemediationGateError, match="duration"):
        validate_row_artifact(payload, now=NOW)
