from __future__ import annotations

import copy

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.embedded_acceptance import (
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
        evidence_objects[f"artifact://{name}"] = {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "status": "passed",
            "identities": copy.deepcopy(source["identities"]),
            "cases": {
                "controlling-case": {
                    "status": "passed",
                    "evidenceRefs": [f"artifact://case/{name}"],
                }
            },
            "generatedAt": "2026-07-21T00:00:00Z",
            "producer": "github-actions:matrix",
            "secretScan": "passed",
            "cleanup": "passed",
        }
    source["evidenceObjects"] = evidence_objects
    return source


def test_complete_matrix_builds_publishable_issue_3425_report() -> None:
    report = build_embedded_acceptance_report(_source())
    assert report["status"] == "passed"
    assert report["issue"] == "MoonLadderStudios/MoonMind#3425"
    assert set(report["sections"]) == set(REQUIRED_SECTIONS)


@pytest.mark.parametrize("kind,key", [("prerequisites", "3422"), ("sections", "mode-transition-rollback")])
def test_missing_or_failed_controlling_lane_refuses_publication(kind: str, key: str) -> None:
    source = _source()
    source[kind][key] = {"status": "failed", "evidenceRefs": ["artifact://failure"]}
    with pytest.raises(ConformanceContractError, match="did not pass"):
        build_embedded_acceptance_report(source)


def test_mutable_stock_host_and_incomplete_cleanup_refuse_publication() -> None:
    mutable = _source()
    mutable["identities"]["images"]["host"] = "host:latest"
    with pytest.raises(ConformanceContractError, match="digest-pinned"):
        build_embedded_acceptance_report(mutable)
    incomplete = copy.deepcopy(_source())
    incomplete["cleanup"]["leasesReleased"] = False
    with pytest.raises(ConformanceContractError, match="release leases"):
        build_embedded_acceptance_report(incomplete)


def test_secret_like_material_refuses_publication() -> None:
    source = _source()
    source["sections"]["secret-scan"]["note"] = "authorization=unsafe"
    with pytest.raises(ConformanceContractError, match="secret-like"):
        build_embedded_acceptance_report(source)


def test_unresolved_or_identity_mismatched_evidence_refuses_publication() -> None:
    unresolved = _source()
    unresolved["sections"]["mixed-mode-history"]["evidenceRefs"] = [
        "artifact://missing"
    ]
    with pytest.raises(ConformanceContractError, match="unresolved"):
        build_embedded_acceptance_report(unresolved)

    mismatched = _source()
    evidence = mismatched["evidenceObjects"]["artifact://mixed-mode-history"]
    evidence["identities"]["moonmindCommit"] = "different"
    with pytest.raises(ConformanceContractError, match="different identities"):
        build_embedded_acceptance_report(mismatched)


def test_failed_or_incomplete_resolved_case_refuses_publication() -> None:
    source = _source()
    evidence = source["evidenceObjects"]["artifact://hostile-input-bounds"]
    evidence["cases"]["controlling-case"]["status"] = "skipped"
    with pytest.raises(ConformanceContractError, match="cases are incomplete"):
        build_embedded_acceptance_report(source)
