from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import (
    ConformanceContractError,
    assert_secret_free,
    build_report,
    evaluate_version,
    load_profile,
)

FIXTURES = Path("tests/fixtures/omnigent")


def test_profile_is_versioned_complete_and_provenanced() -> None:
    profile = load_profile(FIXTURES / "conformance-profile-v1.json")
    assert profile["provenance"]["issue"] == "MoonLadderStudios/MoonMind#3368"
    assert {case["layer"] for case in profile["cases"]} == {
        "backend", "proxy", "persistence", "events", "ui", "resources",
        "failures", "lifecycle", "security",
    }


def test_payload_fixture_covers_every_required_versioned_family() -> None:
    fixture = json.loads(
        (FIXTURES / "contract-payloads-v1.json").read_text(encoding="utf-8")
    )
    assert fixture["schemaVersion"] == "1.0"
    assert fixture["provenance"]["issue"] == "MoonLadderStudios/MoonMind#3368"
    assert set(fixture["families"]) == {
        "bridgeConfiguration", "sessionCreate", "sessionSnapshot", "sessionEvent",
        "control", "normalizedEvent", "lifecycle", "eventPage", "sseFrame",
        "terminalFallback", "captureManifest", "resourceProjection",
        "workflowDetailBlock", "failure", "directCodexEvent",
    }


def test_unknown_versions_fail_critical_and_degrade_optional() -> None:
    assert evaluate_version("2.0", critical=True).behavior == "fail"
    assert evaluate_version("2.0", critical=False).behavior == "degrade"


def test_report_records_images_cases_and_evidence() -> None:
    profile = load_profile(FIXTURES / "conformance-profile-v1.json")
    images = json.loads(
        (FIXTURES / "stock-images-v1.json").read_text(encoding="utf-8")
    )["images"]
    report = build_report(
        profile=profile,
        mode="stock-proxy",
        images=images,
        results=[
            {"caseId": case["id"], "status": "passed", "evidence": f"test:{case['id']}"}
            for case in profile["cases"]
        ],
        evidence_refs=["artifact://omnigent/route-results.json"],
    )
    assert report["profileVersion"] == "1.0"
    assert report["images"][0]["digest"].startswith("sha256:")
    assert report["summary"] == {"passed": 11, "failed": 0, "skipped": 0}


def test_report_rejects_incomplete_or_evidenceless_case_results() -> None:
    profile = load_profile(FIXTURES / "conformance-profile-v1.json")
    with pytest.raises(ConformanceContractError, match="missing cases"):
        build_report(profile=profile, mode="deterministic", images=[], results=[
            {"caseId": "configuration", "status": "passed", "evidence": "test:configuration"}
        ])
    with pytest.raises(ConformanceContractError, match="case-specific evidence"):
        build_report(profile=profile, mode="deterministic", images=[], results=[
            {"caseId": case["id"], "status": "passed", "evidence": ""}
            for case in profile["cases"]
        ])


def test_stock_manifest_pins_unmodified_upstream_images_by_digest() -> None:
    manifest = json.loads(
        (FIXTURES / "stock-images-v1.json").read_text(encoding="utf-8")
    )
    assert manifest["provenance"]["issue"] == "MoonLadderStudios/MoonMind#3368"
    assert [image["ref"] for image in manifest["images"]] == [
        "ghcr.io/omnigent-ai/omnigent-server:v0.1.0",
        "ghcr.io/omnigent-ai/omnigent-host:v0.1.0",
    ]
    assert all(len(image["digest"]) == 71 for image in manifest["images"])


@pytest.mark.parametrize(
    "value",
    [
        {"Authorization": "Bearer example"},
        {"token": "example"},
        {"message": "github_pat_not-safe"},
    ],
)
def test_archived_evidence_rejects_secret_shaped_content(value: object) -> None:
    with pytest.raises(ConformanceContractError):
        assert_secret_free(value)
