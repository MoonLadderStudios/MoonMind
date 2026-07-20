"""MoonLadderStudios/MoonMind#3419 conformance contract tests."""

import json

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

PROFILE = Path("tests/fixtures/omnigent/conformance-v2.json")


def _scans() -> dict[str, dict[str, str]]:
    return {
        channel: {"status": "passed", "evidenceRef": f"artifact://scan/{channel}"}
        for channel in ("logs", "temporalHistory", "screenshots", "archives")
    }


def test_versioned_profile_spans_all_required_layers() -> None:
    profile = load_profile(PROFILE)
    assert profile["profileVersion"] == PROFILE_VERSION
    assert {case["layer"] for case in profile["cases"]} >= {
        "unit", "fake", "api", "frontend", "provider"
    }
    assert len(profile["fixtureFamilies"]) == 10


def test_load_profile_rejects_missing_empty_and_duplicate_case_ids(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profile.json"
    for invalid_case in ({"not_id": "foo"}, {"id": ""}, {"id": 7}):
        profile_path.write_text(
            '{"profileVersion": "moonmind.omnigent.conformance/v2", "cases": ['
            + json.dumps(invalid_case)
            + "]}",
            encoding="utf-8",
        )
        with pytest.raises(ConformanceContractError, match="present, non-empty"):
            load_profile(profile_path)

    profile_path.write_text(
        '{"profileVersion": "moonmind.omnigent.conformance/v2", '
        '"cases": [{"id": "foo"}, {"id": "foo"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ConformanceContractError, match="ids must be unique"):
        load_profile(profile_path)


def test_load_profile_rejects_modified_inventory(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        '{"profileVersion": "moonmind.omnigent.conformance/v2", '
        '"cases": [{"id": "easy"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ConformanceContractError, match="canonical inventory"):
        load_profile(profile_path)


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
        protocol_version="omnigent/v1",
        evidence_scans=_scans(),
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
            protocol_version="omnigent/v1",
            evidence_scans=_scans(),
            cases=[CaseResult("proxy.routes", "passed", ("artifact://routes",))],
        )


def test_report_rejects_empty_case_evidence_refs() -> None:
    profile = load_profile(PROFILE)
    digest = "c" * 64
    cases = [
        CaseResult(case["id"], "passed", (f"artifact://{case['id']}",))
        for case in profile["cases"]
    ]
    cases[0] = CaseResult(cases[0].case_id, "passed", ())
    with pytest.raises(ConformanceContractError, match="evidence ref"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture="linux/amd64",
            auth_mode="oauth",
            protocol_version="omnigent/v1",
            evidence_scans=_scans(),
            capabilities=(),
            cases=cases,
        )


def test_report_requires_each_raw_evidence_channel_scan() -> None:
    profile = load_profile(PROFILE)
    digest = "d" * 64
    cases = [
        CaseResult(case["id"], "passed", (f"artifact://{case['id']}",))
        for case in profile["cases"]
    ]
    scans = _scans()
    del scans["screenshots"]
    with pytest.raises(ConformanceContractError, match="screenshots"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture="linux/amd64",
            auth_mode="oauth",
            protocol_version="omnigent/v1",
            evidence_scans=scans,
            capabilities=(),
            cases=cases,
        )


@pytest.mark.parametrize("value", [None, 3, False])
def test_report_rejects_non_string_host_metadata(value: object) -> None:
    profile = load_profile(PROFILE)
    digest = "e" * 64
    cases = [
        CaseResult(case["id"], "passed", (f"artifact://{case['id']}",))
        for case in profile["cases"]
    ]
    with pytest.raises(ConformanceContractError, match="host architecture"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture=value,  # type: ignore[arg-type]
            auth_mode="oauth",
            protocol_version="omnigent/v1",
            evidence_scans=_scans(),
            capabilities=(),
            cases=cases,
        )


def test_report_rejects_duplicate_results_and_non_string_scan_refs() -> None:
    profile = load_profile(PROFILE)
    digest = "f" * 64
    cases = [
        CaseResult(case["id"], "passed", (f"artifact://{case['id']}",))
        for case in profile["cases"]
    ]
    with pytest.raises(ConformanceContractError, match="duplicate case"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture="linux/amd64",
            auth_mode="oauth",
            protocol_version="omnigent/v1",
            evidence_scans=_scans(),
            capabilities=(),
            cases=[*cases, cases[0]],
        )
    scans: dict[str, dict[str, object]] = _scans()
    scans["logs"]["evidenceRef"] = False
    with pytest.raises(ConformanceContractError, match="logs"):
        build_report(
            profile=profile,
            images={
                "server": f"server@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            host_architecture="linux/amd64",
            auth_mode="oauth",
            protocol_version="omnigent/v1",
            evidence_scans=scans,  # type: ignore[arg-type]
            capabilities=(),
            cases=cases,
        )


@pytest.mark.parametrize(
    "value",
    [
        {"log": "Authorization: Bearer secret"},
        {"token": "abc123"},
        {"nested": {"password": "hunter2"}},
        {"history": "token=abc123"},
        {"archive": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_all_evidence_paths_reject_secret_material(value: object) -> None:
    with pytest.raises(ConformanceContractError, match="secret-like"):
        assert_secret_free(value)
