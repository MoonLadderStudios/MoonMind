"""Protected cutover conformance artifact generation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from moonmind.omnigent.cutover import (
    REQUIRED_EVIDENCE_KINDS,
    REQUIRED_TELEMETRY_GROUPS,
    CutoverPhase,
    evaluate_promotion,
)
from moonmind.omnigent.cutover_conformance import (
    ARTIFACT_SCHEMA_VERSION,
    REQUIRED_MATRIX_ROWS,
    CutoverEvidenceBuildError,
    build_cutover_evidence,
)

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _release() -> dict[str, object]:
    return {
        "authorizedPhase": "CREATE_DEFAULT",
        "currentPhase": "OPT_IN",
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
    }


def _artifacts(tmp_path):
    rows = list(REQUIRED_MATRIX_ROWS)
    paths = []
    for index, kind in enumerate(REQUIRED_EVIDENCE_KINDS):
        owned_rows = rows[index::len(REQUIRED_EVIDENCE_KINDS)]
        path = tmp_path / f"{kind}.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": ARTIFACT_SCHEMA_VERSION,
                    "kind": kind,
                    "passed": True,
                    "matrixRows": owned_rows,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def test_builder_derives_complete_digest_bound_promotion_evidence(tmp_path) -> None:
    paths = _artifacts(tmp_path)
    evidence = build_cutover_evidence(
        release=_release(), artifact_paths=paths, generated_at=NOW
    )

    assert evidence["matrixRows"] == list(REQUIRED_MATRIX_ROWS)
    assert evidence["evidenceRefs"] == [path.resolve().as_uri() for path in paths]
    for item, path in zip(evidence["evidenceManifest"], paths, strict=True):
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    decision = evaluate_promotion(
        current_phase=CutoverPhase.OPT_IN,
        requested_phase=CutoverPhase.CREATE_DEFAULT,
        evidence=evidence,
        now=NOW,
    )
    assert decision.allowed is True


def test_builder_rejects_missing_matrix_row(tmp_path) -> None:
    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["matrixRows"].pop()
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CutoverEvidenceBuildError, match="missingRows"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )


def test_builder_rejects_failed_or_duplicate_evidence(tmp_path) -> None:
    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["passed"] = False
    paths[0].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CutoverEvidenceBuildError, match="did not pass"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )

    paths = _artifacts(tmp_path)
    with pytest.raises(CutoverEvidenceBuildError, match="duplicate evidence kind"):
        build_cutover_evidence(
            release=_release(),
            artifact_paths=[*paths, paths[0]],
            generated_at=NOW,
        )
