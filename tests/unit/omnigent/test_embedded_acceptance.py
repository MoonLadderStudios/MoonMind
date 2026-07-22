from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.embedded_acceptance import (
    CASE_EVIDENCE_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_PREREQUISITES,
    REQUIRED_SECTIONS,
    build_embedded_acceptance_report,
)


def _source() -> dict:
    passed = lambda name: {
        "status": "passed",
        "evidenceRefs": [f"artifact://{name}"],
    }
    digest = "a" * 64
    source = {
        "producer": "github-actions:omnigent-embedded-acceptance",
        "identities": {
            "moonmindCommit": "abc123",
            "moonmindBuild": "build-7",
            "profileVersion": "omnigent-stock/v1",
            "protocolVersion": "omnigent/v1",
            "images": {
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
        },
        "prerequisites": {issue: passed(f"issue-{issue}") for issue in REQUIRED_PREREQUISITES},
        "sections": {section: passed(section) for section in REQUIRED_SECTIONS},
        "cleanup": {
            **passed("cleanup"),
            "historicalEvidencePreserved": True,
            "leasesReleased": True,
        },
    }
    evidence_objects = {}
    claims = {
        **{
            f"issue-{issue}": f"prerequisite:{issue}"
            for issue in REQUIRED_PREREQUISITES
        },
        **{section: f"section:{section}" for section in REQUIRED_SECTIONS},
        "cleanup": "cleanup",
    }
    for name, claim in claims.items():
        case_ref = f"artifact://case/{name}"
        evidence_objects[f"artifact://{name}"] = {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "status": "passed",
            "identities": copy.deepcopy(source["identities"]),
            "evidenceRefs": [f"artifact://channel/{name}"],
            "cases": {
                "controlling-case": {
                    "status": "passed",
                    "evidenceRefs": [case_ref],
                }
            },
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
            "producer": "github-actions:matrix",
            "secretScan": "passed",
            "cleanup": "passed",
        }
        evidence_objects[case_ref] = {
            "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "case": "controlling-case",
            "status": "passed",
            "identities": copy.deepcopy(source["identities"]),
            "evidenceRefs": [f"artifact://channel/case/{name}"],
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
            "producer": "github-actions:case-runner",
            "secretScan": "passed",
            "cleanup": "passed",
        }
    source["evidenceObjects"] = evidence_objects
    return source


def _build(source: dict, **kwargs: Any) -> dict:
    kwargs.setdefault("now", datetime(2026, 7, 21, 12, tzinfo=timezone.utc))
    return build_embedded_acceptance_report(source, **kwargs)


def test_complete_matrix_builds_publishable_issue_3425_report() -> None:
    report = _build(_source())
    assert report["status"] == "passed"
    assert report["issue"] == "MoonLadderStudios/MoonMind#3425"
    assert set(report["sections"]) == set(REQUIRED_SECTIONS)


def test_expected_commit_must_match_evidence_identity() -> None:
    with pytest.raises(ConformanceContractError, match="different commit"):
        _build(_source(), expected_commit="def456")


@pytest.mark.parametrize("kind,key", [("prerequisites", "3422"), ("sections", "mode-transition-rollback")])
def test_missing_or_failed_controlling_lane_refuses_publication(kind: str, key: str) -> None:
    source = _source()
    source[kind][key] = {"status": "failed", "evidenceRefs": ["artifact://failure"]}
    with pytest.raises(ConformanceContractError, match="did not pass"):
        _build(source)


def test_mutable_stock_host_and_incomplete_cleanup_refuse_publication() -> None:
    mutable = _source()
    mutable["identities"]["images"]["host"] = "host:latest"
    with pytest.raises(ConformanceContractError, match="digest-pinned"):
        _build(mutable)
    incomplete = copy.deepcopy(_source())
    incomplete["cleanup"]["leasesReleased"] = False
    with pytest.raises(ConformanceContractError, match="release leases"):
        _build(incomplete)


def test_secret_like_material_refuses_publication() -> None:
    source = _source()
    source["sections"]["secret-scan"]["note"] = "authorization=unsafe"
    with pytest.raises(ConformanceContractError, match="secret-like"):
        _build(source)


def test_unresolved_or_identity_mismatched_evidence_refuses_publication() -> None:
    unresolved = _source()
    unresolved["sections"]["mixed-mode-history"]["evidenceRefs"] = [
        "artifact://missing"
    ]
    with pytest.raises(ConformanceContractError, match="unresolved"):
        _build(unresolved)

    mismatched = _source()
    evidence = mismatched["evidenceObjects"]["artifact://mixed-mode-history"]
    evidence["identities"]["moonmindCommit"] = "different"
    with pytest.raises(ConformanceContractError, match="different identities"):
        _build(mismatched)


def test_failed_or_incomplete_resolved_case_refuses_publication() -> None:
    source = _source()
    evidence = source["evidenceObjects"]["artifact://hostile-input-bounds"]
    evidence["cases"]["controlling-case"]["status"] = "skipped"
    with pytest.raises(ConformanceContractError, match="cases are incomplete"):
        _build(source)


def test_unresolved_or_mismatched_leaf_case_refuses_publication() -> None:
    unresolved = _source()
    del unresolved["evidenceObjects"]["artifact://case/mixed-mode-history"]
    with pytest.raises(
        ConformanceContractError,
        match="case controlling-case evidence ref is unresolved",
    ):
        _build(unresolved)

    mismatched = _source()
    leaf = mismatched["evidenceObjects"]["artifact://case/mixed-mode-history"]
    leaf["case"] = "another-case"
    with pytest.raises(ConformanceContractError, match="does not prove its case"):
        _build(mismatched)


@pytest.mark.parametrize(
    "override,match",
    [
        ({"expiresAt": "2026-07-21T11:59:59Z"}, "validity period"),
        ({"revokedAt": "2026-07-21T11:00:00Z"}, "revoked"),
        ({"supersededBy": "artifact://replacement"}, "superseded"),
        ({"generatedAt": "not-a-time"}, "invalid generation"),
    ],
)
def test_stale_revoked_superseded_or_malformed_evidence_refuses_publication(
    override: dict, match: str
) -> None:
    source = _source()
    source["evidenceObjects"]["artifact://mode-transition-rollback"].update(override)
    with pytest.raises(ConformanceContractError, match=match):
        _build(source)
