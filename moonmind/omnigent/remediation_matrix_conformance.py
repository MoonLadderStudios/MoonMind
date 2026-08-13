"""Assemble the #3626 operator-remediation release document from observations.

Row ownership, coverage, telemetry samples, and threshold results are derived
from validated evidence artifacts.  Release callers supply only immutable
deployment inputs; they cannot assert a row result or combined pass.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from moonmind.omnigent.remediation_matrix import (
    REMEDIATION_MATRIX_VERSION,
    REMEDIATION_RELEASE_POLICY_VERSION,
    REMEDIATION_ROW_CATALOG_BY_ID,
    REQUIRED_REMEDIATION_EVIDENCE_KINDS,
    REQUIRED_REMEDIATION_MATRIX_ROWS,
    REQUIRED_REMEDIATION_TELEMETRY_GROUPS,
    RemediationMatrixError,
    validate_remediation_evidence_artifact,
)


class RemediationEvidenceBuildError(ValueError):
    """Raised when observed artifacts cannot form a complete release document."""


_REQUIRED_RELEASE_INPUTS = (
    "images",
    "architectures",
    "profileVersion",
    "profileSha256",
    "launchPolicyVersion",
    "agentProfileVersion",
    "remediationPolicyVersion",
)


def _telemetry(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {
        kind: sum(
            1
            for row_id in rows
            if REMEDIATION_ROW_CATALOG_BY_ID[row_id].evidence_kind == kind
        )
        for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
    }
    durations = {
        row_id: int(entry["timings"]["durationMs"])
        for row_id, entry in rows.items()
    }
    denied_rows = sum(
        1 for entry in rows.values() if entry.get("observedDisposition") == "denied"
    )
    delivered_rows = sum(
        1
        for entry in rows.values()
        if isinstance(entry.get("actionDelivery"), Mapping)
        and entry["actionDelivery"].get("status") == "delivered"
    )
    return {
        "remediationCreation": {
            "sampleCount": len(rows),
            "qualifiedCount": len(rows),
        },
        "contextBuild": {
            "sampleCount": by_kind["diagnosisEvidence"],
            "successCount": by_kind["diagnosisEvidence"],
        },
        "evidenceAvailability": {
            "sampleCount": by_kind["diagnosisEvidence"],
            "degradedOrDeniedCount": sum(
                1
                for row_id in rows
                if "degraded" in row_id or "unauthorized" in row_id
            ),
        },
        "approvalOutcomes": {
            "sampleCount": by_kind["actionApprovalEvidence"],
            "deniedCount": denied_rows,
        },
        "actionOutcomesByKindAndRisk": {
            "sampleCount": by_kind["actionApprovalEvidence"],
            "deliveredCount": delivered_rows,
            "deniedOrSuppressedCount": denied_rows,
        },
        "lockCooldownDuplicateAndEscalation": {
            "sampleCount": by_kind["actionApprovalEvidence"]
        },
        "branchLifecycleLatency": {
            "sampleCount": by_kind["recoveryBranchEvidence"],
            "maxDurationMs": max(
                durations[row_id]
                for row_id in rows
                if REMEDIATION_ROW_CATALOG_BY_ID[row_id].evidence_kind
                == "recoveryBranchEvidence"
            ),
        },
        "verificationOutcomes": {
            "sampleCount": by_kind["verificationPreventionEvidence"],
            "unverifiedMutationCount": sum(
                1
                for entry in rows.values()
                if isinstance(entry.get("repairVerification"), Mapping)
                and entry["repairVerification"].get("outcome")
                in {"evidence_unavailable", "verification_failed"}
            ),
        },
        "repeatedFailureAndAttemptExhaustion": {
            "sampleCount": by_kind["recoveryBranchEvidence"]
        },
        "egressOutcomes": {
            "sampleCount": by_kind["reliabilitySecurityEvidence"],
            "denialCount": sum(
                1
                for row_id in rows
                if REMEDIATION_ROW_CATALOG_BY_ID[row_id].egress
                == "restricted_denied"
            ),
        },
        "operatorCancellationAndTakeover": {
            "sampleCount": by_kind["reliabilitySecurityEvidence"]
        },
        "autonomousAndManualOrigin": {
            "sampleCount": len(rows),
            "manualCount": sum(
                1
                for row_id in rows
                if REMEDIATION_ROW_CATALOG_BY_ID[row_id].authority_mode
                != "admin_auto"
            ),
            "autonomousDeniedCount": sum(
                1
                for row_id in rows
                if REMEDIATION_ROW_CATALOG_BY_ID[row_id].authority_mode
                == "admin_auto"
            ),
        },
    }


def build_remediation_release_evidence(
    *,
    release: Mapping[str, Any],
    artifact_paths: Iterable[Path],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one digest-bound release document from complete observed rows."""

    observation_time = generated_at or datetime.now(timezone.utc)
    missing_inputs = [key for key in _REQUIRED_RELEASE_INPUTS if not release.get(key)]
    if missing_inputs:
        raise RemediationEvidenceBuildError(
            f"immutable release inputs are incomplete: {missing_inputs}"
        )

    seen_kinds: set[str] = set()
    observed_rows: dict[str, Mapping[str, Any]] = {}
    manifest: list[dict[str, str]] = []
    threshold_results: dict[str, bool] = {}
    try:
        for supplied_path in artifact_paths:
            path = supplied_path.resolve()
            content = path.read_bytes()
            payload = json.loads(content)
            if not isinstance(payload, Mapping):
                raise RemediationEvidenceBuildError(
                    f"remediation evidence is not an object: {path}"
                )
            kind, row_ids = validate_remediation_evidence_artifact(
                payload,
                expected_kind=None,
                images=release["images"],
                architectures=release["architectures"],
                profile_version=release["profileVersion"],
                profile_sha256=release["profileSha256"],
                policy_version=release["launchPolicyVersion"],
                agent_profile_version=release["agentProfileVersion"],
                remediation_policy_version=release["remediationPolicyVersion"],
                evidence_document_path=path,
                evidence_time=observation_time,
            )
            if kind in seen_kinds:
                raise RemediationEvidenceBuildError(
                    f"duplicate remediation evidence kind: {kind}"
                )
            seen_kinds.add(kind)
            entries = {
                str(entry["row"]): entry
                for entry in payload["rows"]
                if isinstance(entry, Mapping) and isinstance(entry.get("row"), str)
            }
            for row_id in row_ids:
                if row_id in observed_rows:
                    raise RemediationEvidenceBuildError(
                        f"duplicate observed remediation row: {row_id}"
                    )
                observed_rows[row_id] = entries[row_id]
                for threshold, result in entries[row_id].get("thresholds", {}).items():
                    threshold_results[f"{row_id}:{threshold}"] = bool(
                        isinstance(result, Mapping) and result.get("within") is True
                    )
            manifest.append(
                {
                    "kind": kind,
                    "ref": path.as_uri(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    except (OSError, UnicodeError, json.JSONDecodeError, RemediationMatrixError) as exc:
        raise RemediationEvidenceBuildError(str(exc)) from exc

    missing_kinds = sorted(set(REQUIRED_REMEDIATION_EVIDENCE_KINDS) - seen_kinds)
    missing_rows = sorted(set(REQUIRED_REMEDIATION_MATRIX_ROWS) - set(observed_rows))
    if missing_kinds or missing_rows:
        raise RemediationEvidenceBuildError(
            "incomplete operator-remediation evidence; "
            f"missingKinds={missing_kinds}, missingRows={missing_rows}"
        )
    telemetry = _telemetry(observed_rows)
    if set(telemetry) != set(REQUIRED_REMEDIATION_TELEMETRY_GROUPS):
        raise RemediationEvidenceBuildError("remediation telemetry derivation is incomplete")
    within_limits = bool(threshold_results) and all(threshold_results.values())
    if not within_limits:
        raise RemediationEvidenceBuildError("one or more remediation thresholds failed")

    return {
        "schemaVersion": REMEDIATION_RELEASE_POLICY_VERSION,
        "issue": "MoonLadderStudios/MoonMind#3626",
        "matrixVersion": REMEDIATION_MATRIX_VERSION,
        "generatedAt": observation_time.isoformat(),
        **{key: release[key] for key in _REQUIRED_RELEASE_INPUTS},
        "matrixRows": list(REQUIRED_REMEDIATION_MATRIX_ROWS),
        "telemetry": telemetry,
        "thresholds": {
            "withinLimits": within_limits,
            "results": threshold_results,
        },
        "evidenceRefs": [item["ref"] for item in manifest],
        "evidenceManifest": manifest,
    }


__all__ = [
    "RemediationEvidenceBuildError",
    "build_remediation_release_evidence",
]
