from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    LANE_DETERMINISTIC,
    REQUIRED_CASES,
    SCENARIO_LANES,
)
from tools.record_native_chat_deterministic_observations import (
    DETERMINISTIC_CASE_BOUNDARIES,
    record,
)


def _junit(path: Path, names: list[str]) -> None:
    suite = ET.Element(
        "testsuite",
        tests=str(len(names)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for name in names:
        testcase = ET.SubElement(suite, "testcase", classname="acceptance", name=name)
        prefix = "test_native_chat_acceptance_case["
        if name.startswith(prefix) and name.endswith("]"):
            scenario, case = name[len(prefix) : -1].split("--", 1)
            properties = ET.SubElement(testcase, "properties")
            for key, value in (
                ("nativeChatScenario", scenario),
                ("nativeChatCase", case),
                ("authorizationDecision", "not_applicable"),
                ("upstreamSideEffectCount", "0"),
                ("expectedUpstreamSideEffectCount", "0"),
                ("durableAfterCleanup", "true"),
            ):
                ET.SubElement(properties, "property", name=key, value=value)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _inputs(tmp_path: Path) -> dict[str, object]:
    backend = tmp_path / "backend.xml"
    frontend = tmp_path / "frontend.xml"
    browser = tmp_path / "browser.xml"
    boundary_names: dict[str, set[str]] = {
        "backend": set(),
        "frontend": set(),
        "browser": set(),
    }
    for cases in DETERMINISTIC_CASE_BOUNDARIES.values():
        for boundaries in cases.values():
            for suite, fragment in boundaries:
                boundary_names[suite].add(fragment)
    _junit(
        backend,
        [
            f"test_native_chat_acceptance_case[{scenario}--{case}]"
            for scenario, cases in REQUIRED_CASES.items()
            if SCENARIO_LANES[scenario] == LANE_DETERMINISTIC
            for case in cases
        ]
        + [
            "test_browser_payload_compiles_replays_and_releases_only_after_cleanup",
            "test_bridge_proxy_create_get_and_journal",
            "test_bridge_proxy_complete_route_matrix",
            "test_rollout_blocks_direct_http_sse_resource_and_control_bypasses",
            "test_native_http_mutation_is_scanned_receipted_and_replay_safe",
            "test_websocket_closes_when_authority_is_revoked_midstream",
            "test_high_security_scanner_error_fails_closed",
            "test_terminal_binding_serves_durable_snapshot_without_provider_session",
            "test_global_updates_frames_are_identity_scoped_and_virtualized",
            "test_terminal_input_frame_is_scanned_and_durably_receipted",
            "test_continue_creates_linked_workflow_and_pins_source",
            "test_captured_evidence_projects_authorized_refs",
            "test_production_adapter_emits_through_shared_exporter_without_identity_tags",
        ] + sorted(boundary_names["backend"]),
    )
    _junit(
        frontend,
        [
            "selects Chat by default and preserves query state",
            "deep-links to Chat and keeps non-chat tabs one click away",
            "keeps terminal workflow actions available when the native iframe is unavailable",
            "opens captured evidence as authorized MoonMind download links",
            "continues into the linked workflow with authored intent and navigates to it",
        ] + sorted(boundary_names["frontend"]),
    )
    _junit(
        browser,
        [
            "mounts the same-origin scoped URL with the scoped security attributes",
            "ignores foreign-origin liveness messages",
            "reports ready on a genuine same-origin load",
            "drives the native composer and claimed feature surface through scoped requests",
            "keeps large mobile sessions keyboard and screen-reader operable with reduced-motion CSS",
        ] + sorted(boundary_names["browser"]),
    )
    digest = "a" * 64
    return {
        "backend_junit": backend,
        "frontend_junit": frontend,
        "browser_junit": browser,
        "output_dir": tmp_path / "evidence",
        "commit": "abc123",
        "build": "build-1",
        "server_image": f"server@sha256:{digest}",
        "ui_image": f"ui@sha256:{digest}",
        "host_image": f"host@sha256:{digest}",
    }


def test_recorder_requires_and_links_every_controlling_testcase(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    output = record(**inputs)

    assert output.is_file()
    assert (inputs["output_dir"] / "backend-junit.xml").is_file()
    assert (inputs["output_dir"] / "frontend-junit.xml").is_file()
    assert (inputs["output_dir"] / "browser-junit.xml").is_file()


def test_recorder_rejects_generic_passing_backend_suite(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    _junit(inputs["backend_junit"], ["generic passing test"])

    with pytest.raises(ConformanceContractError, match="lacks controlling cases"):
        record(**inputs)


def test_recorder_rejects_case_without_observed_outcome(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    backend = inputs["backend_junit"]
    assert isinstance(backend, Path)
    tree = ET.parse(backend)
    first = tree.find(".//testcase[properties]")
    assert first is not None
    properties = first.find("properties")
    assert properties is not None
    first.remove(properties)
    tree.write(backend, encoding="utf-8", xml_declaration=True)

    with pytest.raises(ConformanceContractError, match="outcome identity is invalid"):
        record(**inputs)


def test_recorder_rejects_missing_case_specific_production_boundary(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    backend = inputs["backend_junit"]
    assert isinstance(backend, Path)
    tree = ET.parse(backend)
    for testcase in list(tree.findall(".//testcase")):
        if "test_identity_substitution_in_query_is_rejected" in str(
            testcase.get("name")
        ):
            parent = tree.getroot()
            parent.remove(testcase)
    tree.getroot().set("tests", str(len(tree.findall(".//testcase"))))
    tree.write(backend, encoding="utf-8", xml_declaration=True)

    with pytest.raises(ConformanceContractError, match="production boundary"):
        record(**inputs)
