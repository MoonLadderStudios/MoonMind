"""Protected cutover conformance artifact generation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from moonmind.omnigent.conformance import PROFILE_SHA256, PROFILE_VERSION
from moonmind.omnigent.cutover import (
    ARTIFACT_SCHEMA_VERSION,
    REQUIRED_EVIDENCE_KINDS,
    REQUIRED_MATRIX_ROWS,
    REQUIRED_TELEMETRY_GROUPS,
    ROW_CATALOG,
    CutoverPhase,
    evaluate_promotion,
)
from moonmind.omnigent.cutover_conformance import (
    CutoverEvidenceBuildError,
    build_cutover_evidence,
)

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)

IMAGES = {
    "server": "example/server@sha256:" + "1" * 64,
    "host": "example/host@sha256:" + "2" * 64,
}
POLICY_VERSION = "codex-static-launch-policy/v1"
AGENT_PROFILE_VERSION = "codex-agent-profile/v1"


def _release() -> dict[str, object]:
    return {
        "authorizedPhase": "CREATE_DEFAULT",
        "currentPhase": "OPT_IN",
        "images": dict(IMAGES),
        "architectures": ["linux/amd64"],
        "launchPolicyVersion": POLICY_VERSION,
        "agentProfileVersion": AGENT_PROFILE_VERSION,
        "telemetry": {
            group: {"sampleCount": 10} for group in REQUIRED_TELEMETRY_GROUPS
        },
        "thresholds": {
            "withinLimits": True,
            "results": {"launchSuccessRate": True, "secretViolations": True},
        },
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
        "secretScan": "clean",
    }


def _artifacts(tmp_path):
    owned: dict[str, list[str]] = {kind: [] for kind in REQUIRED_EVIDENCE_KINDS}
    for row_id in REQUIRED_MATRIX_ROWS:
        owned[ROW_CATALOG[row_id].kind].append(row_id)
    paths = []
    for kind, row_ids in owned.items():
        path = tmp_path / f"{kind}.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": ARTIFACT_SCHEMA_VERSION,
                    "kind": kind,
                    "producerVersion": "moonmind.codex-omnigent-observer/v1",
                    "rows": [_observed_row(row_id) for row_id in row_ids],
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
    payload["rows"].pop()
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CutoverEvidenceBuildError, match="missingRows"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )


def test_builder_rejects_failed_or_duplicate_evidence(tmp_path) -> None:
    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["rows"][0]["observedResult"] = "failed"
    paths[0].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CutoverEvidenceBuildError, match="did not observe a passing"):
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


def test_builder_rejects_caller_supplied_row_ownership(tmp_path) -> None:
    """A row observed by a kind the catalog does not assign fails closed."""

    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    foreign = _observed_row("direct-runtime.historical-read-fallback")
    payload["rows"].append(foreign)
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CutoverEvidenceBuildError, match="not owned by evidence kind"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )


def test_builder_rejects_self_asserted_pass_without_observed_results(tmp_path) -> None:
    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload.pop("rows")
    payload["passed"] = True
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CutoverEvidenceBuildError, match="declares no observed rows"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )


def test_builder_rejects_row_evidence_for_wrong_release_digests(tmp_path) -> None:
    """Observed images that are not the released digests fail closed."""

    paths = _artifacts(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["rows"][0]["images"] = {
        "server": "example/server@sha256:" + "9" * 64,
        "host": "example/host@sha256:" + "8" * 64,
    }
    paths[0].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CutoverEvidenceBuildError, match="released immutable digests"):
        build_cutover_evidence(
            release=_release(), artifact_paths=paths, generated_at=NOW
        )
