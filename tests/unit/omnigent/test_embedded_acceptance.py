from __future__ import annotations

import copy

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.embedded_acceptance import (
    REQUIRED_PREREQUISITES,
    REQUIRED_SECTIONS,
    build_embedded_acceptance_report,
)


def _source() -> dict:
    passed = lambda name: {"status": "passed", "evidenceRefs": [f"artifact://{name}"]}
    digest = "a" * 64
    return {
        "producer": "github-actions:omnigent-embedded-acceptance",
        "identities": {
            "moonmindCommit": "abc123",
            "moonmindBuild": "build-7",
            "profileVersion": "omnigent-stock/v1",
            "protocolVersion": "omnigent/v1",
            "images": {"server": f"server@sha256:{digest}", "host": f"host@sha256:{digest}"},
        },
        "prerequisites": {issue: passed(f"issue-{issue}") for issue in REQUIRED_PREREQUISITES},
        "sections": {section: passed(section) for section in REQUIRED_SECTIONS},
        "cleanup": {**passed("cleanup"), "historicalEvidencePreserved": True, "leasesReleased": True},
    }


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
