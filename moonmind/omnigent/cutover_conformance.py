"""Build promotion evidence from independently resolvable cutover artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from moonmind.omnigent.conformance import PROFILE_SHA256, PROFILE_VERSION
from moonmind.omnigent.cutover import (
    CUTOVER_POLICY_VERSION,
    REQUIRED_EVIDENCE_KINDS,
    REQUIRED_TELEMETRY_GROUPS,
)

ARTIFACT_SCHEMA_VERSION = "moonmind.codex-omnigent-cutover-artifact/v1"
EVIDENCE_KIND_PROMOTION_FIELD = {
    "submissionMatrix": "allRequiredCasesPassed",
    "historicalReads": "historicalReadsPassed",
    "temporalReplay": "temporalReplayPassed",
    "capacityOwnership": "capacitySingleOwnerPassed",
    "secretScan": "secretScansPassed",
    "releaseMetadata": "profilePolicyReady",
}

# Stable row IDs are the machine-readable form of the canonical v1 matrix.
REQUIRED_MATRIX_ROWS = (
    "oauth-profile.static",
    "oauth-profile.on-demand",
    "bridge.stock-proxy",
    "host.static",
    "host.on-demand",
    "submission.create",
    "submission.edit",
    "submission.rerun",
    "submission.schedule",
    "submission.preset",
    "repository.read",
    "repository.mutate-publish",
    "workflow-detail.live-replay-resources-controls",
    "lifecycle.cancel-timeout-failure-cleanup-janitor",
    "checkpoint.capture-reattach-restore-branch",
    "remediation.operator-autonomous-gate",
    "rag.initial-follow-up",
    "policy-agent-profile.persistence-ui",
    "egress.enforced",
    "release.images-architecture-upstream-license",
    "direct-runtime.historical-read-fallback",
)


class CutoverEvidenceBuildError(ValueError):
    """Raised when protected evidence is incomplete or internally inconsistent."""


def _artifact(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        content = path.read_bytes()
        payload = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CutoverEvidenceBuildError(f"unreadable cutover artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise CutoverEvidenceBuildError(f"cutover artifact is not an object: {path}")
    if payload.get("schemaVersion") != ARTIFACT_SCHEMA_VERSION:
        raise CutoverEvidenceBuildError(f"unsupported cutover artifact: {path}")
    if payload.get("passed") is not True:
        raise CutoverEvidenceBuildError(f"cutover artifact did not pass: {path}")
    observations = payload.get("observations")
    if (
        not isinstance(observations, Mapping)
        or not observations
        or any(result is not True for result in observations.values())
    ):
        raise CutoverEvidenceBuildError(
            f"cutover artifact lacks passing observed results: {path}"
        )
    return payload, content


def build_cutover_evidence(
    *,
    release: Mapping[str, Any],
    artifact_paths: Iterable[Path],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a promotion document only from a complete protected matrix.

    The builder derives pass booleans, refs, and SHA-256 values from artifact
    bytes. Callers cannot declare coverage or success in the release config.
    """

    manifest: list[dict[str, str]] = []
    promotion_results: dict[str, bool] = {}
    kinds: set[str] = set()
    rows: set[str] = set()
    for supplied_path in artifact_paths:
        path = supplied_path.resolve()
        payload, content = _artifact(path)
        kind = payload.get("kind")
        artifact_rows = payload.get("matrixRows")
        if not isinstance(kind, str) or kind not in REQUIRED_EVIDENCE_KINDS:
            raise CutoverEvidenceBuildError(f"invalid evidence kind in {path}")
        if kind in kinds:
            raise CutoverEvidenceBuildError(f"duplicate evidence kind: {kind}")
        if not isinstance(artifact_rows, list) or not artifact_rows or any(
            not isinstance(row, str) or row not in REQUIRED_MATRIX_ROWS
            for row in artifact_rows
        ):
            raise CutoverEvidenceBuildError(f"invalid matrix row coverage in {path}")
        duplicate_rows = rows.intersection(artifact_rows)
        if duplicate_rows:
            raise CutoverEvidenceBuildError(
                f"matrix rows have multiple owners: {sorted(duplicate_rows)}"
            )
        kinds.add(kind)
        promotion_results[EVIDENCE_KIND_PROMOTION_FIELD[kind]] = True
        rows.update(artifact_rows)
        manifest.append(
            {
                "kind": kind,
                "ref": path.as_uri(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    missing_kinds = sorted(set(REQUIRED_EVIDENCE_KINDS) - kinds)
    missing_rows = sorted(set(REQUIRED_MATRIX_ROWS) - rows)
    if missing_kinds or missing_rows:
        raise CutoverEvidenceBuildError(
            f"incomplete cutover evidence; missingKinds={missing_kinds}, "
            f"missingRows={missing_rows}"
        )

    telemetry = release.get("telemetry")
    if not isinstance(telemetry, Mapping) or any(
        not isinstance(telemetry.get(group), Mapping) or not telemetry[group]
        for group in REQUIRED_TELEMETRY_GROUPS
    ):
        raise CutoverEvidenceBuildError("complete migration telemetry is required")
    thresholds = release.get("thresholds")
    results = thresholds.get("results") if isinstance(thresholds, Mapping) else None
    if (
        not isinstance(thresholds, Mapping)
        or thresholds.get("withinLimits") is not True
        or not isinstance(results, Mapping)
        or not results
        or any(result is not True for result in results.values())
    ):
        raise CutoverEvidenceBuildError("all release thresholds must pass")

    document = dict(release)
    document.update(
        {
            "schemaVersion": CUTOVER_POLICY_VERSION,
            "generatedAt": (generated_at or datetime.now(timezone.utc)).isoformat(),
            "profileVersion": PROFILE_VERSION,
            "profileSha256": PROFILE_SHA256,
            **promotion_results,
            "matrixVersion": "codex-omnigent-support-matrix/v1",
            "matrixRows": list(REQUIRED_MATRIX_ROWS),
            "evidenceRefs": [item["ref"] for item in manifest],
            "evidenceManifest": manifest,
        }
    )
    return document


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "REQUIRED_MATRIX_ROWS",
    "CutoverEvidenceBuildError",
    "build_cutover_evidence",
]
