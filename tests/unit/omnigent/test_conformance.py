"""MoonLadderStudios/MoonMind#3368 conformance contract tests."""

from pathlib import Path

import pytest

from moonmind.omnigent.conformance import (
    PROFILE_VERSION,
    CaseResult,
    ConformanceContractError,
    assert_secret_free,
    build_report,
    load_profile,
    require_pinned_images,
    validate_fixture,
)

PROFILE = Path("tests/fixtures/omnigent/conformance-v1.json")


def test_versioned_profile_spans_all_required_layers() -> None:
    profile = load_profile(PROFILE)
    assert profile["profileVersion"] == PROFILE_VERSION
    assert {case["layer"] for case in profile["cases"]} >= {
        "unit", "fake", "api", "frontend", "provider"
    }
    assert len(profile["fixtureFamilies"]) == 10


def test_unknown_fixture_version_requires_explicit_behavior() -> None:
    base = {"schemaVersion": "future/v9", "provenance": {"source": "upstream"}}
    with pytest.raises(ConformanceContractError, match="fail/degrade"):
        validate_fixture(base)
    assert validate_fixture({**base, "unknownVersionExpectation": "degrade"}) == "degrade"


def test_stock_images_must_be_immutable() -> None:
    with pytest.raises(ConformanceContractError, match="server image"):
        require_pinned_images({"server": "omnigent:latest", "host": "host:latest"})


def test_report_requires_every_case_and_records_machine_readable_evidence() -> None:
    profile = load_profile(PROFILE)
    cases = [
        CaseResult(case["id"], "passed", (f"artifact://{case['id']}",))
        for case in profile["cases"]
    ]
    digest = "a" * 64
    report = build_report(
        profile=profile,
        images={"server": f"server@sha256:{digest}", "host": f"host@sha256:{digest}"},
        host_architecture="linux/amd64",
        auth_mode="oauth",
        capabilities=("codex-native", "events"),
        cases=cases,
    )
    assert report["summary"] == {"passed": len(cases), "failed": 0, "skipped": 0}
    assert all(case["evidenceRefs"] for case in report["cases"])


def test_report_rejects_incomplete_layer_results() -> None:
    profile = load_profile(PROFILE)
    digest = "b" * 64
    with pytest.raises(ConformanceContractError, match="coverage mismatch"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture="linux/amd64",
            auth_mode="oauth",
            capabilities=(),
            cases=[CaseResult("proxy.routes", "passed", ("artifact://routes",))],
        )


@pytest.mark.parametrize(
    "value",
    [
        {"log": "Authorization: Bearer secret"},
        {"history": "token=abc123"},
        {"archive": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_all_evidence_paths_reject_secret_material(value: object) -> None:
    with pytest.raises(ConformanceContractError, match="secret-like"):
        assert_secret_free(value)
