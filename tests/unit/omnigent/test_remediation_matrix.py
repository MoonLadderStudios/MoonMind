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
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.remediation_matrix import (
    ACTION_RISK_CASES,
    AUTHORITY_MODES,
    BRANCH_CHANGED_CHOICES,
    CONTEXT_DENIAL_CASES,
    CONTEXT_FOLLOW_PHASES,
    CONTEXT_NONDISCLOSURE_PROTECTIONS,
    DUPLICATE_EFFECT_CASES,
    GATE_AUTONOMOUS_ROLLOUT,
    GATE_MANUAL_DIAGNOSIS,
    GATE_MANUAL_MUTATION,
    PROHIBITED_UI_JOURNEY_MARKERS,
    PROHIBITED_AUTHORITY_CASES,
    REMEDIATION_ARTIFACT_SCHEMA_VERSION,
    REMEDIATION_MATRIX_VERSION,
    REMEDIATION_EVIDENCE_IDENTITY_FIELDS,
    REMEDIATION_LINEAGE_REF_RECORD_TYPES,
    REMEDIATION_REPAIR_OUTCOMES,
    REMEDIATION_DURABLE_PHASES,
    REMEDIATION_ROW_CATALOG,
    REMEDIATION_ROW_CATALOG_BY_ID,
    REMEDIATION_SOURCE_RECORD_SCHEMAS,
    REMEDIATION_TELEMETRY_SCHEMA_VERSION,
    REQUIRED_REMEDIATION_EVIDENCE_KINDS,
    REQUIRED_REMEDIATION_LINEAGE_FIELDS,
    REQUIRED_REMEDIATION_MATRIX_ROWS,
    REQUIRED_REMEDIATION_PHASE_LATENCIES,
    REQUIRED_REMEDIATION_RETAINED_CHANNELS,
    REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES,
    REQUIRED_REMEDIATION_TELEMETRY_GROUPS,
    REQUIRED_UI_JOURNEY_ASSERTIONS,
    RESUME_AUTHORITY_CASES,
    SESSION_CONTROL_CASES,
    STALE_AUTHORITY_CASES,
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


def _row_delivery_and_repair(row) -> tuple[str, str]:
    row_id = row.row_id
    if not row.is_mutation:
        return "not_applicable", "canceled"
    if row_id in {
        "remediation.idempotency.duplicate-suppression",
        "remediation.security.duplicate-prevention-idempotency",
    }:
        return "suppressed_idempotent", "verified_no_change"
    if row_id == "remediation.lock.mutation-conflict-diagnosis-parallelism":
        return "denied", "approval_required"
    if row_id == "remediation.verify.action-delivered-no-change":
        return "delivered", "verified_no_change"
    if row_id in {
        "remediation.verify.still-failed-regressed-unavailable",
        "remediation.prevention.repair-fail-then-prevention-pr",
    }:
        return "delivered", "still_failed"
    if row_id == "remediation.prevention.pr-verification-failure-not-relabeled":
        return "delivered", "verification_failed"
    if row_id == "remediation.reliability.cancellation-each-phase":
        return "not_delivered", "canceled"
    if row_id == "remediation.repair.no-progress-exhaustion":
        return "not_delivered", "still_failed"
    if row.expected_outcome == "denied":
        return "denied", "approval_required"
    return "delivered", "verified_resolved"


def _row_approval_outcome(row) -> str:
    if not row.is_mutation or row.authority_mode == "admin_auto":
        return "not_required"
    if row.row_id in {
        "remediation.resume.unavailable-stale-mismatch",
        "remediation.approval.denied-expired-consumed-unauthorized-stale",
        "remediation.staleness.generation-rejected",
        "remediation.egress.restricted-denied",
        "remediation.security.prohibited-authority-denied",
    }:
        return "denied"
    return "approved"


def _observed_row(row_id: str) -> dict[str, object]:
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    delivery_status, repair_outcome = _row_delivery_and_repair(row)
    timings = {
        "startedAt": (NOW - timedelta(seconds=1)).isoformat(),
        "completedAt": NOW.isoformat(),
        "durationMs": 1000,
        "phaseLatenciesMs": {
            phase: index + 1
            for index, phase in enumerate(REQUIRED_REMEDIATION_PHASE_LATENCIES)
        },
    }
    manifest = [
        {
            "type": record_type,
            "ref": f"source/{row_id}/{record_type}.json",
            "sha256": "a" * 64,
            "schemaVersion": REMEDIATION_SOURCE_RECORD_SCHEMAS[record_type],
            "contentType": "application/json",
            "sizeBytes": 2,
            "generatedAt": NOW.isoformat(),
        }
        for record_type in sorted(REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES)
    ]
    refs = {record["type"]: record["ref"] for record in manifest}
    identity = _identity(row_id)
    lineage = {
        **{field: identity[field] for field in REMEDIATION_EVIDENCE_IDENTITY_FIELDS[:10]},
        **{
            field: refs[record_type]
            for field, record_type in REMEDIATION_LINEAGE_REF_RECORD_TYPES.items()
        },
    }
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
        "timings": timings,
        "observations": {
            observation: True for observation in row.required_observations
        },
        "secretScan": _clean_secret_scan(),
        "lineage": lineage,
        "evidenceManifest": manifest,
    }
    if row.is_mutation:
        entry["actionDelivery"] = {"status": delivery_status}
        entry["repairVerification"] = {"outcome": repair_outcome}
    if row.row_id == "remediation.autonomous.rollout-gate-closed":
        entry["uiJourney"]["normalCreateRequest"] = False
        entry["uiJourney"]["autonomousAdmissionDenied"] = True
    return entry


def _identity(row_id: str) -> dict[str, str]:
    return {
        field: f"{field}-{row_id}"
        for field in REMEDIATION_EVIDENCE_IDENTITY_FIELDS
    }


def _source_payload(entry: dict[str, object], record_type: str) -> dict[str, object]:
    row_id = str(entry["row"])
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    identity = _identity(row_id)
    delivery_status, repair_outcome = _row_delivery_and_repair(row)
    approval_outcome = _row_approval_outcome(row)
    threshold_samples = {
        key: {"passed": 1, "total": 1} for key in row.thresholds
    }
    observations = {
        observation: True for observation in row.required_observations
    }
    common: dict[str, object] = {
        "schemaVersion": REMEDIATION_SOURCE_RECORD_SCHEMAS[record_type],
        "generatedAt": NOW.isoformat(),
        "row": row_id,
        "identity": identity,
    }
    values: dict[str, dict[str, object]] = {
        "scenarioObservation": {
            "observed": True,
            "observedDisposition": row.expected_outcome,
            "hostMode": row.host_modes[0],
            "architecture": ARCHITECTURES[0],
            "targetProvenance": row.target_provenance[0],
            "remediationProvenance": row.remediation_provenance[0],
            "remainingLiveResources": 0,
            "timings": entry["timings"],
            "thresholdSamples": threshold_samples,
            "observations": observations,
            "lineage": entry["lineage"],
            **(
                {
                    "actionDelivery": {"status": delivery_status},
                    "repairVerification": {"outcome": repair_outcome},
                }
                if row.is_mutation
                else {}
            ),
        },
        "browserTrace": {
            "uiJourney": entry["uiJourney"],
            "hostMode": row.host_modes[0],
            "architecture": ARCHITECTURES[0],
            "remediationCreated": row.authority_mode != "admin_auto",
        },
        "authoredRequest": {
            "authorityMode": row.authority_mode,
            "actionCapability": row.action_capability,
            "verificationCapability": row.verification_capability,
            "origin": "autonomous" if row.authority_mode == "admin_auto" else "manual",
            "actionRisk": row.action_risk,
        },
        "immutableInputSnapshot": {
            "targetProvenance": row.target_provenance[0],
            "immutable": True,
            "inputDigest": "d" * 64,
        },
        "workflowLineage": {
            "remediationProvenance": row.remediation_provenance[0],
            "lineage": entry["lineage"],
            "timings": entry["timings"],
            "resumeOutcome": (
                "resumed" if row_id == "remediation.resume.evidence-gated-success"
                else "unavailable"
                if row_id == "remediation.resume.unavailable-stale-mismatch"
                else "not_applicable"
            ),
            "resumeAuthorityCases": (
                list(RESUME_AUTHORITY_CASES)
                if row_id == "remediation.resume.unavailable-stale-mismatch"
                else []
            ),
            "branchCreated": row_id.startswith("remediation.branch.")
            or row_id == "remediation.repair.cumulative-multi-attempt",
            "newSemanticStepExecution": row_id
            == "remediation.branch.corrected-instruction-repair",
            "freshStockSession": row_id
            == "remediation.branch.corrected-instruction-repair",
            "immutableInputPreserved": row_id
            in {
                "remediation.resume.evidence-gated-success",
                "remediation.branch.changed-choices-require-branch",
            },
            "branchChangedChoices": (
                list(BRANCH_CHANGED_CHOICES)
                if row_id == "remediation.branch.changed-choices-require-branch"
                else []
            ),
            "acceptedWorkspaceProgressPreserved": row_id
            == "remediation.repair.cumulative-multi-attempt",
            "attemptCount": (
                2
                if row_id
                in {
                    "remediation.repair.cumulative-multi-attempt",
                    "remediation.repair.no-progress-exhaustion",
                }
                else 1
            ),
            "hostLifecycleOutcome": (
                "reconciled"
                if row_id
                in {
                    "remediation.host.static-lifecycle",
                    "remediation.host.on-demand-lifecycle",
                }
                else "not_applicable"
            ),
        },
        "contextEvidence": {
            "contextBuildOutcome": (
                "degraded" if "partial-historical" in row_id
                else "denied" if "missing-unauthorized" in row_id
                else "success"
            ),
            "evidenceOutcome": (
                "degraded" if "partial-historical" in row_id
                else "denied" if "missing-unauthorized" in row_id
                else "available"
            ),
            "followPhases": (
                list(CONTEXT_FOLLOW_PHASES)
                if row_id == "remediation.evidence.active-snapshot-follow-reconnect"
                else []
            ),
            "denialCases": (
                list(CONTEXT_DENIAL_CASES)
                if row_id == "remediation.evidence.missing-unauthorized-denied"
                else []
            ),
            "nondisclosureProtections": (
                list(CONTEXT_NONDISCLOSURE_PROTECTIONS)
                if row_id == "remediation.evidence.missing-unauthorized-denied"
                else []
            ),
        },
        "profilePolicyAuthority": {
            "authorityMode": row.authority_mode,
            "actionCapability": row.action_capability,
            "verificationCapability": row.verification_capability,
            "actionRisk": row.action_risk,
            "agentProfileId": identity["agentProfileId"],
            "providerProfileId": identity["providerProfileId"],
            "leaseId": identity["leaseId"],
            "profileValidated": True,
            "policyValidated": True,
            "credentialGenerationFresh": True,
            "strongReviewerAuthority": row_id
            == "remediation.action.high-risk-stronger-authority",
            "leaseHostReconciled": row_id
            == "remediation.lease.provider-profile-host-reconciliation",
        },
        "egressAttestation": {
            "authority": row.egress,
            "decision": {
                "not_applicable": "not_applicable",
                "restricted_allowed": "allowed",
                "restricted_denied": "denied",
            }[row.egress],
            "attestationOutcome": "passed",
        },
        "approvalDecision": {
            "requested": row.is_mutation and row.authority_mode == "approval_gated",
            "outcome": approval_outcome,
            "outcomesObserved": (
                ["denied", "expired", "consumed", "unauthorized", "stale"]
                if row_id
                == "remediation.approval.denied-expired-consumed-unauthorized-stale"
                else [approval_outcome]
            ),
        },
        "actionResult": {
            "requested": row.is_mutation and row.authority_mode != "admin_auto",
            "deliveryStatus": delivery_status,
            "outcome": {
                "not_applicable": "no_op",
                "delivered": "delivered",
                "denied": "denied",
                "suppressed_idempotent": "no_op",
                "not_delivered": "failure",
            }[delivery_status],
            "actionKind": row.action_capability,
            "risk": row.action_risk,
            "lockConflict": row_id
            == "remediation.lock.mutation-conflict-diagnosis-parallelism",
            "cooldown": False,
            "duplicateSuppressed": "idempotency" in row_id,
            "nestedRemediationDenied": False,
            "noProgressEscalated": "no-progress" in row_id,
            "riskCasesDelivered": (
                list(ACTION_RISK_CASES)
                if row_id == "remediation.action.low-medium-risk-allowed"
                else []
            ),
            "staleAuthorityRejections": (
                list(STALE_AUTHORITY_CASES)
                if row_id == "remediation.staleness.generation-rejected"
                else []
            ),
            "diagnosisParallelAllowed": row_id
            == "remediation.lock.mutation-conflict-diagnosis-parallelism",
            "sessionControlsDelivered": (
                list(SESSION_CONTROL_CASES)
                if row_id
                == "remediation.session.interrupt-clear-cancel-terminate-restart"
                else []
            ),
            "prohibitedAuthoritiesDenied": (
                list(PROHIBITED_AUTHORITY_CASES)
                if row_id == "remediation.security.prohibited-authority-denied"
                else []
            ),
        },
        "verificationResult": {
            "outcome": repair_outcome,
            "verificationCapability": row.verification_capability,
            "unverifiedMutation": repair_outcome
            in {"evidence_unavailable", "verification_failed"},
            "repeatedFailure": "no-progress" in row_id,
            "attemptsExhausted": "no-progress" in row_id,
            "preventionOutcome": (
                "published_pr"
                if row_id == "remediation.prevention.repair-fail-then-prevention-pr"
                else "analyzed_separately"
                if row_id
                == "remediation.prevention.repair-success-separate-analysis"
                else "not_applicable"
            ),
            "outcomesObserved": (
                list(REMEDIATION_REPAIR_OUTCOMES & {
                    "still_failed",
                    "regressed",
                    "evidence_unavailable",
                    "verification_failed",
                })
                if row_id
                == "remediation.verify.still-failed-regressed-unavailable"
                else [repair_outcome]
            ),
            "immediateRepairOutcome": (
                "still_failed"
                if row_id
                in {
                    "remediation.prevention.repair-fail-then-prevention-pr",
                    "remediation.prevention.pr-verification-failure-not-relabeled",
                }
                else repair_outcome
            ),
            "preventionAnalysisSeparate": row_id
            == "remediation.prevention.repair-success-separate-analysis",
            "targetRelabeledRepaired": False,
        },
        "publicationOutcome": {
            "outcome": (
                "published"
                if row_id == "remediation.prevention.repair-fail-then-prevention-pr"
                else "verification_failed"
                if row_id
                == "remediation.prevention.pr-verification-failure-not-relabeled"
                else "not_applicable"
            ),
            "preventionPrOutcome": (
                "published_reviewable"
                if row_id == "remediation.prevention.repair-fail-then-prevention-pr"
                else "verification_failed"
                if row_id
                == "remediation.prevention.pr-verification-failure-not-relabeled"
                else "not_applicable"
            ),
        },
        "cleanupOutcome": {
            "outcome": "completed",
            "remainingLiveResources": 0,
            "terminalHarvested": True,
            "janitorVerified": True,
            "lockReleased": True,
            "capacityReleased": True,
            "providerProfileReleasedLast": True,
            "operatorCancelled": "cancellation-each-phase" in row_id,
            "operatorTakeover": False,
            "targetedCleanup": row_id
            in {
                "remediation.cleanup.targeted-janitor-verification",
                "remediation.cleanup.complete-provider-profile-release-last",
            },
            "helperRestarted": row_id
            == "remediation.helper.container-restart-reap-linkage",
            "helperReaped": row_id
            == "remediation.helper.container-restart-reap-linkage",
            "helperTargetLinked": row_id
            == "remediation.helper.container-restart-reap-linkage",
        },
        "temporalHistory": {
            "replayCount": (
                len(REMEDIATION_DURABLE_PHASES)
                if "worker-restart-temporal-replay" in row_id
                else 0
            ),
            "replayOutcome": "passed",
            "cancellationPhases": (
                list(REMEDIATION_DURABLE_PHASES)
                if "cancellation-each-phase" in row_id
                else []
            ),
            "replayPhases": (
                list(REMEDIATION_DURABLE_PHASES)
                if "worker-restart-temporal-replay" in row_id
                else []
            ),
            "duplicateEffectsSuppressed": (
                list(DUPLICATE_EFFECT_CASES)
                if row_id == "remediation.security.duplicate-prevention-idempotency"
                else []
            ),
            "firstMessageCount": 0 if row.authority_mode == "admin_auto" else 1,
            "duplicateSuppressionCount": 1 if "duplicate" in row_id else 0,
        },
        "sideEffectAudit": {
            "observedDisposition": row.expected_outcome,
            "observations": observations,
            "thresholdSamples": threshold_samples,
            "firstMessageCount": 0 if row.authority_mode == "admin_auto" else 1,
            "duplicateSuppressionCount": 1 if "duplicate" in row_id else 0,
        },
        "retainedEvidenceScan": {
            "channels": list(REQUIRED_REMEDIATION_RETAINED_CHANNELS),
            "secretFindings": 0,
            "prohibitedAuthorityFindings": 0,
        },
    }
    return {**common, **values[record_type]}


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
                _source_payload(entry, record["type"]), sort_keys=True
            ).encode()
            path.write_bytes(content)
            record["sha256"] = hashlib.sha256(content).hexdigest()
            record["sizeBytes"] = len(content)
        secret_scan = entry.get("secretScan")
        if not isinstance(secret_scan, dict):
            continue
        for channel in REQUIRED_REMEDIATION_RETAINED_CHANNELS:
            scan = secret_scan[channel]
            path = tmp_path / scan["evidenceRef"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                json.dumps(
                    {
                        "schemaVersion": "moonmind.retained-evidence-secret-scan/v1",
                        "generatedAt": NOW.isoformat(),
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
    release_inputs = {
        "images": dict(IMAGES),
        "architectures": list(ARCHITECTURES),
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "remediationPolicyVersion": REMEDIATION_POLICY_VERSION,
    }
    release = build_remediation_release_evidence(
        release=release_inputs,
        artifact_paths=[tmp_path / f"{kind}.json" for kind in artifacts],
        generated_at=NOW,
    )
    for item in release["evidenceManifest"]:
        item["ref"] = Path(item["ref"]).name
    release["evidenceRefs"] = [item["ref"] for item in release["evidenceManifest"]]
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    return release, release_path


def _artifact_bytes(artifact) -> bytes:
    return json.dumps(artifact, sort_keys=True).encode()


def _rewrite_source_record(
    root: Path,
    artifact: dict[str, object],
    record_type: str,
    update,
    *,
    row_index: int = 0,
) -> None:
    entry = artifact["rows"][row_index]
    record = next(
        item for item in entry["evidenceManifest"] if item["type"] == record_type
    )
    path = root / record["ref"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    update(payload)
    content = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(content)
    record["sha256"] = hashlib.sha256(content).hexdigest()
    record["sizeBytes"] = len(content)


def _validate_staged(artifact, root: Path, *, kind="diagnosisEvidence"):
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
        evidence_document_path=root / "artifact.json",
        evidence_time=NOW,
    )


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
    assert document["telemetryContract"]["schemaVersion"] == REMEDIATION_TELEMETRY_SCHEMA_VERSION
    assert set(document["telemetryContract"]["groups"]) == set(
        REQUIRED_REMEDIATION_TELEMETRY_GROUPS
    )
    for row in document["rows"]:
        assert row["owner"]
        assert row["hostModes"]
        assert row["architectures"]
        assert row["evidenceSchema"] == REMEDIATION_ARTIFACT_SCHEMA_VERSION
        assert all(
            threshold["rule"] == "catalog_owned_typed_fact_predicate"
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
    observed_kind, rows = _validate(_artifact(kind), kind=kind)
    assert observed_kind == kind
    assert rows == {
        r for r in REQUIRED_REMEDIATION_MATRIX_ROWS
        if REMEDIATION_ROW_CATALOG_BY_ID[r].evidence_kind == kind
    }


def _validate(artifact, kind="diagnosisEvidence", *, images=None, architectures=None):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        rows = artifact.get("rows") if isinstance(artifact, dict) else None
        if isinstance(rows, list) and all(
            isinstance(entry, dict)
            and entry.get("row") in REMEDIATION_ROW_CATALOG_BY_ID
            and isinstance(entry.get("evidenceManifest"), list)
            for entry in rows
        ):
            _stage_row_dependencies(root, artifact)
        return validate_remediation_evidence_artifact(
            artifact,
            expected_kind=kind,
            images=images or IMAGES,
            architectures=architectures or ARCHITECTURES,
            profile_version=PROFILE_VERSION,
            profile_sha256=PROFILE_SHA256,
            policy_version=POLICY_VERSION,
            agent_profile_version=AGENT_PROFILE_VERSION,
            remediation_policy_version=REMEDIATION_POLICY_VERSION,
            evidence_document_path=root / "artifact.json",
            evidence_time=NOW,
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


def test_schema_only_source_record_cannot_qualify_support(tmp_path) -> None:
    artifact = _artifact("diagnosisEvidence")
    _stage_row_dependencies(tmp_path, artifact)
    _rewrite_source_record(
        tmp_path,
        artifact,
        "approvalDecision",
        lambda payload: payload.clear() or payload.update({
            "schemaVersion": REMEDIATION_SOURCE_RECORD_SCHEMAS["approvalDecision"],
            "generatedAt": NOW.isoformat(),
        }),
    )
    with pytest.raises(RemediationMatrixError, match="mismatched row identity"):
        _validate_staged(artifact, tmp_path)


def test_nonresolving_lineage_ref_is_rejected_even_when_claims_agree(tmp_path) -> None:
    artifact = _artifact("diagnosisEvidence")
    _stage_row_dependencies(tmp_path, artifact)
    entry = artifact["rows"][0]
    entry["lineage"]["approvalRef"] = "source/missing-approval.json"
    for record_type in ("workflowLineage", "scenarioObservation"):
        _rewrite_source_record(
            tmp_path,
            artifact,
            record_type,
            lambda payload: payload["lineage"].update(
                {"approvalRef": "source/missing-approval.json"}
            ),
        )
    with pytest.raises(RemediationMatrixError, match="not bound to its typed source"):
        _validate_staged(artifact, tmp_path)


def test_mismatched_cross_record_session_identity_is_rejected(tmp_path) -> None:
    artifact = _artifact("diagnosisEvidence")
    _stage_row_dependencies(tmp_path, artifact)
    _rewrite_source_record(
        tmp_path,
        artifact,
        "approvalDecision",
        lambda payload: payload["identity"].update({"sessionId": "other-session"}),
    )
    with pytest.raises(RemediationMatrixError, match="mismatched workflow/run/session"):
        _validate_staged(artifact, tmp_path)


def test_fabricated_scenario_outcome_cannot_override_action_audit(tmp_path) -> None:
    kind = "actionApprovalEvidence"
    artifact = _artifact(kind)
    _stage_row_dependencies(tmp_path, artifact)
    _rewrite_source_record(
        tmp_path,
        artifact,
        "scenarioObservation",
        lambda payload: payload.update({"observedDisposition": "denied"}),
    )
    with pytest.raises(RemediationMatrixError, match="scenario observation conflicts"):
        _validate_staged(artifact, tmp_path, kind=kind)


@pytest.mark.parametrize(
    ("row_id", "record_type", "update"),
    (
        (
            "remediation.action.approval-gated-approved",
            "approvalDecision",
            lambda payload: payload.update({"outcome": "denied"}),
        ),
        (
            "remediation.verify.action-delivered-target-resolved",
            "actionResult",
            lambda payload: payload.update(
                {"deliveryStatus": "denied", "outcome": "denied"}
            ),
        ),
        (
            "remediation.verify.action-delivered-target-resolved",
            "verificationResult",
            lambda payload: payload.update({"outcome": "verified_no_change"}),
        ),
        (
            "remediation.verify.action-delivered-no-change",
            "verificationResult",
            lambda payload: payload.update({"outcome": "verified_resolved"}),
        ),
        (
            "remediation.verify.still-failed-regressed-unavailable",
            "verificationResult",
            lambda payload: payload.update({"outcome": "verified_resolved"}),
        ),
        (
            "remediation.reliability.cancellation-each-phase",
            "temporalHistory",
            lambda payload: payload.update({"cancellationPhases": []}),
        ),
        (
            "remediation.reliability.worker-restart-temporal-replay",
            "temporalHistory",
            lambda payload: payload.update({"replayPhases": []}),
        ),
        (
            "remediation.security.duplicate-prevention-idempotency",
            "temporalHistory",
            lambda payload: payload.update({"firstMessageCount": 2}),
        ),
        (
            "remediation.cleanup.complete-provider-profile-release-last",
            "cleanupOutcome",
            lambda payload: payload.update({"providerProfileReleasedLast": False}),
        ),
    ),
    ids=(
        "approved-summary-denied-by-authority",
        "delivered-summary-denied-by-action-owner",
        "resolved-summary-no-change-verification",
        "no-change-summary-resolved-verification",
        "non-resolved-summary-resolved-verification",
        "missing-cancellation-phases",
        "missing-replay-phases",
        "duplicate-first-message",
        "provider-profile-not-released-last",
    ),
)
def test_passing_summaries_cannot_override_typed_semantic_contradictions(
    tmp_path, row_id, record_type, update
) -> None:
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    artifact = _artifact(row.evidence_kind, [row_id])
    _stage_row_dependencies(tmp_path, artifact)
    _rewrite_source_record(tmp_path, artifact, record_type, update)

    with pytest.raises(RemediationMatrixError, match="authoritative typed facts"):
        _validate_staged(artifact, tmp_path, kind=row.evidence_kind)


@pytest.mark.parametrize(
    ("row_id", "requested"),
    (
        ("remediation.resume.evidence-gated-success", False),
        ("remediation.action.low-medium-risk-allowed", False),
        ("remediation.diagnosis.observe-only", True),
        ("remediation.autonomous.rollout-gate-closed", True),
    ),
    ids=(
        "approval-gated-recovery-without-request",
        "approval-gated-action-without-request",
        "observe-only-with-request",
        "admin-auto-with-request",
    ),
)
def test_passing_artifact_cannot_override_approval_request_authority(
    tmp_path, row_id, requested
) -> None:
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    artifact = _artifact(row.evidence_kind, [row_id])
    _stage_row_dependencies(tmp_path, artifact)
    _rewrite_source_record(
        tmp_path,
        artifact,
        "approvalDecision",
        lambda payload: payload.update({"requested": requested}),
    )

    with pytest.raises(RemediationMatrixError, match="authoritative typed facts"):
        _validate_staged(artifact, tmp_path, kind=row.evidence_kind)


def test_fabricated_audit_and_scenario_threshold_counts_are_rejected(tmp_path) -> None:
    row_id = "remediation.action.approval-gated-approved"
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    threshold = row.thresholds[0]
    artifact = _artifact(row.evidence_kind, [row_id])
    _stage_row_dependencies(tmp_path, artifact)
    for record_type in ("sideEffectAudit", "scenarioObservation"):
        _rewrite_source_record(
            tmp_path,
            artifact,
            record_type,
            lambda payload: payload["thresholdSamples"].update(
                {threshold: {"passed": 2, "total": 2}}
            ),
        )

    with pytest.raises(RemediationMatrixError, match="authoritative typed facts"):
        _validate_staged(artifact, tmp_path, kind=row.evidence_kind)


def test_combined_release_fails_closed_on_typed_approval_contradiction(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    kind = "actionApprovalEvidence"
    artifact_path = tmp_path / f"{kind}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    row_index = next(
        index
        for index, entry in enumerate(artifact["rows"])
        if entry["row"] == "remediation.action.approval-gated-approved"
    )
    _rewrite_source_record(
        tmp_path,
        artifact,
        "approvalDecision",
        lambda payload: payload.update({"outcome": "denied"}),
        row_index=row_index,
    )
    content = _artifact_bytes(artifact)
    artifact_path.write_bytes(content)
    manifest_entry = next(
        item for item in release["evidenceManifest"] if item["kind"] == kind
    )
    manifest_entry["sha256"] = hashlib.sha256(content).hexdigest()
    release_path.write_text(json.dumps(release), encoding="utf-8")

    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "evidence_row_binding_invalid" in status.blockers
    assert status.manual_mutation_supported is False


@pytest.mark.parametrize(
    ("row_id", "requested"),
    (
        ("remediation.resume.evidence-gated-success", False),
        ("remediation.action.low-medium-risk-allowed", False),
        ("remediation.diagnosis.observe-only", True),
        ("remediation.autonomous.rollout-gate-closed", True),
    ),
    ids=(
        "approval-gated-recovery-without-request",
        "approval-gated-action-without-request",
        "observe-only-with-request",
        "admin-auto-with-request",
    ),
)
def test_combined_release_fails_closed_on_approval_request_authority(
    tmp_path, row_id, requested
) -> None:
    release, release_path = _stage_release(tmp_path)
    row = REMEDIATION_ROW_CATALOG_BY_ID[row_id]
    artifact_path = tmp_path / f"{row.evidence_kind}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    row_index = next(
        index
        for index, entry in enumerate(artifact["rows"])
        if entry["row"] == row_id
    )
    _rewrite_source_record(
        tmp_path,
        artifact,
        "approvalDecision",
        lambda payload: payload.update({"requested": requested}),
        row_index=row_index,
    )
    content = _artifact_bytes(artifact)
    artifact_path.write_bytes(content)
    manifest_entry = next(
        item
        for item in release["evidenceManifest"]
        if item["kind"] == row.evidence_kind
    )
    manifest_entry["sha256"] = hashlib.sha256(content).hexdigest()
    release_path.write_text(json.dumps(release), encoding="utf-8")

    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "evidence_row_binding_invalid" in status.blockers
    assert status.manual_mutation_supported is False


def test_missing_architecture_coverage_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    with pytest.raises(RemediationMatrixError, match="every released architecture"):
        _validate(
            artifact,
            architectures=["linux/amd64", "linux/arm64"],
        )


def test_mutable_image_is_rejected() -> None:
    artifact = _artifact("diagnosisEvidence")
    with pytest.raises(RemediationMatrixError, match="immutable"):
        _validate(
            artifact,
            images={"server": "example/server:latest", "host": IMAGES["host"]},
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
    assert document["telemetry"]["schemaVersion"] == REMEDIATION_TELEMETRY_SCHEMA_VERSION
    assert set(document["telemetry"]["groups"]) == set(
        REQUIRED_REMEDIATION_TELEMETRY_GROUPS
    )
    duplicate_group = document["telemetry"]["groups"][
        "lockCooldownDuplicateAndEscalation"
    ]
    expected_duplicate_suppressions = sum(
        1 for row_id in REQUIRED_REMEDIATION_MATRIX_ROWS if "duplicate" in row_id
    )
    assert duplicate_group["duplicateSuppressionCount"] == (
        expected_duplicate_suppressions
    )
    assert duplicate_group["duplicateSuppressionRate"] == round(
        expected_duplicate_suppressions / len(REQUIRED_REMEDIATION_MATRIX_ROWS), 6
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
    release["telemetry"]["groups"].pop(REQUIRED_REMEDIATION_TELEMETRY_GROUPS[0])
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_telemetry_required_or_invalid" in status.blockers


def test_missing_telemetry_dimension_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    del release["telemetry"]["groups"]["approvalOutcomes"]["expirationRate"]
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_telemetry_required_or_invalid" in status.blockers
    assert "remediation_telemetry_diverges_from_evidence" in status.blockers


def test_wrong_action_kind_risk_bucket_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    buckets = release["telemetry"]["groups"]["actionOutcomesByKindAndRisk"]["buckets"]
    value = buckets.pop(next(iter(buckets)))
    buckets["caller-supplied|low"] = value
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_telemetry_required_or_invalid" in status.blockers


def test_missing_phase_latency_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    del release["telemetry"]["groups"]["branchLifecycleLatency"][
        "phaseLatenciesMs"
    ]["publication"]
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "remediation_telemetry_required_or_invalid" in status.blockers


def test_over_threshold_release_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["thresholds"]["withinLimits"] = False
    release_path.write_text(json.dumps(release), encoding="utf-8")
    status = evaluate_remediation_release(
        evidence=release,
        evidence_document_path=release_path,
        evidence_ref="release.json",
        now=NOW,
    )
    assert "release_thresholds_diverge_from_telemetry" in status.blockers


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
    second = _artifact(kind, kind_rows[2:])
    release, release_path = _stage_release(tmp_path)
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


def test_incomplete_kind_coverage_blocks_promotion(tmp_path) -> None:
    release, release_path = _stage_release(tmp_path)
    release["evidenceManifest"] = [
        item
        for item in release["evidenceManifest"]
        if item["kind"] != "reliabilitySecurityEvidence"
    ]
    release["evidenceRefs"] = [item["ref"] for item in release["evidenceManifest"]]
    release_path.write_text(json.dumps(release), encoding="utf-8")
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
