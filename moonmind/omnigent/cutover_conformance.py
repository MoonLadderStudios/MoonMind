"""Build promotion evidence from independently resolvable cutover artifacts.

Source issue: MoonLadderStudios/MoonMind#3564.

The builder never trusts a caller-supplied row list or a bare ``passed`` flag.
Each artifact must carry the observed lifecycle result for the rows it owns, and
every attribute (host mode, runtime provenance, immutable images, architecture,
conformance profile, launch-policy, and agent-profile version) is re-validated
against the release before a row is counted as proven.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from moonmind.omnigent.conformance import PROFILE_SHA256, PROFILE_VERSION
from moonmind.omnigent.cutover import (
    CUTOVER_POLICY_VERSION,
    MATRIX_VERSION,
    REQUIRED_EVIDENCE_KINDS,
    REQUIRED_MATRIX_ROWS,
    REQUIRED_TELEMETRY_GROUPS,
    CutoverMatrixError,
    validate_matrix_artifact,
)

EVIDENCE_KIND_PROMOTION_FIELD = {
    "submissionMatrix": "allRequiredCasesPassed",
    "historicalReads": "historicalReadsPassed",
    "temporalReplay": "temporalReplayPassed",
    "capacityOwnership": "capacitySingleOwnerPassed",
    "secretScan": "secretScansPassed",
    "releaseMetadata": "profilePolicyReady",
}


class CutoverEvidenceBuildError(ValueError):
    """Raised when protected evidence is incomplete or internally inconsistent."""


def build_cutover_evidence(
    *,
    release: Mapping[str, Any],
    artifact_paths: Iterable[Path],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a promotion document only from a complete protected matrix.

    The builder derives pass booleans, refs, owned rows, and SHA-256 values from
    the observed evidence in each artifact. Callers cannot declare coverage,
    row ownership, or success in the release config.
    """

    policy_version = release.get("launchPolicyVersion")
    agent_profile_version = release.get("agentProfileVersion")
    images = release.get("images")
    architectures = release.get("architectures")

    manifest: list[dict[str, str]] = []
    promotion_results: dict[str, bool] = {}
    kinds: set[str] = set()
    rows: set[str] = set()
    for supplied_path in artifact_paths:
        path = supplied_path.resolve()
        try:
            content = path.read_bytes()
            payload = json.loads(content)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CutoverEvidenceBuildError(
                f"unreadable cutover artifact: {path}"
            ) from exc
        try:
            kind, artifact_rows = validate_matrix_artifact(
                payload,
                expected_kind=None,
                images=images,
                architectures=architectures,
                profile_version=PROFILE_VERSION,
                profile_sha256=PROFILE_SHA256,
                policy_version=policy_version,
                agent_profile_version=agent_profile_version,
            )
        except CutoverMatrixError as exc:
            raise CutoverEvidenceBuildError(f"{exc} in {path}") from exc
        if kind in kinds:
            raise CutoverEvidenceBuildError(f"duplicate evidence kind: {kind}")
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
            "launchPolicyVersion": policy_version,
            "agentProfileVersion": agent_profile_version,
            **promotion_results,
            "matrixVersion": MATRIX_VERSION,
            "matrixRows": list(REQUIRED_MATRIX_ROWS),
            "evidenceRefs": [item["ref"] for item in manifest],
            "evidenceManifest": manifest,
        }
    )
    return document


__all__ = [
    "EVIDENCE_KIND_PROMOTION_FIELD",
    "CutoverEvidenceBuildError",
    "build_cutover_evidence",
]
