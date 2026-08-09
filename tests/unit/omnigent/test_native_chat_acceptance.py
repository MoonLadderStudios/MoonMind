from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    COMPATIBILITY_VERSION, OBSERVATION_VERSION, REQUIRED_ACCESSIBILITY,
    REQUIRED_CHANNELS, REQUIRED_FEATURES, REQUIRED_LANES, REQUIRED_SCENARIOS,
    REQUIRED_SECURITY_CONTROLS, REQUIRED_TELEMETRY, REQUIRED_TRANSPORTS,
    PRODUCER_VERSION, SCENARIO_EVIDENCE_VERSION,
    assemble_native_chat_acceptance_input,
    build_native_chat_acceptance_report,
    expected_scenario_outcome,
)


def _fixture(root: Path) -> dict:
    def evidence_record(relative: str, content: str = "redacted observed evidence") -> dict:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"evidenceRef": f"artifact://{relative}",
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}

    def scenario_record(lane: str, scenario: str) -> dict:
        payload = {
            "schemaVersion": SCENARIO_EVIDENCE_VERSION,
            "lane": lane,
            "scenarioId": scenario,
            "producer": {
                "schemaVersion": PRODUCER_VERSION,
                "kind": (
                    "protected-stock-image"
                    if lane == "protected-stock-image-journey"
                    else "deterministic-browser-fake-server"
                ),
                "command": ["node", "tools/run_omnigent_native_chat_journey.mjs"],
                "exitCode": 0,
            },
            "observation": {
                "observationId": f"{lane}:{scenario}",
                "operation": scenario,
                "expectedOutcome": expected_scenario_outcome(scenario),
                "actualOutcome": expected_scenario_outcome(scenario),
                "requestCount": 1,
                "responseCount": 1,
                "stateBefore": "ready",
                "stateAfter": "observed",
            },
            "upstreamRequests": [],
            "moonmindRequests": [{"method": "POST", "url": f"/{lane}/{scenario}"}],
            "responses": [{"status": 200, "url": f"/{lane}/{scenario}"}],
        }
        return {
            "id": scenario,
            "outcome": "passed",
            "upstreamSideEffects": 0,
            **evidence_record(
                f"scenarios/{lane}/{scenario}.json",
                json.dumps(payload, sort_keys=True),
            ),
        }

    digest = "sha256:" + "a" * 64
    identity = {
        "moonmindCommit": "commit-3642", "moonmindBuild": "build-1",
        "serverImageDigest": digest, "uiImageDigest": digest,
        "hostImageDigest": digest, "architecture": "linux/amd64",
        "profileDigest": digest, "policyDigest": digest,
        "compatibilityManifestDigest": "",
    }
    lanes = {}
    for lane in REQUIRED_LANES:
        path = root / "lanes" / f"{lane}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        observation = {"schemaVersion": OBSERVATION_VERSION, "lane": lane,
                       "status": "passed", "identity": identity,
                       "scenarios": []}
        # Written again after compatibility identity is finalised below.
        path.write_text(json.dumps(observation), encoding="utf-8")
        lanes[lane] = {"status": "passed", "evidenceRef": f"artifact://lanes/{lane}.json"}
    compatibility = {
        "schemaVersion": COMPATIBILITY_VERSION,
        "transports": {name: "passed" for name in REQUIRED_TRANSPORTS},
        "features": {name: "passed" for name in REQUIRED_FEATURES},
        "accessibility": {name: "passed" for name in REQUIRED_ACCESSIBILITY},
        "securityControls": {name: "passed" for name in REQUIRED_SECURITY_CONTROLS},
    }
    compatibility_path = root / "compatibility.json"
    compatibility_path.write_text(json.dumps(compatibility), encoding="utf-8")
    identity["compatibilityManifestDigest"] = "sha256:" + hashlib.sha256(
        compatibility_path.read_bytes()).hexdigest()
    for lane in REQUIRED_LANES:
        path = root / "lanes" / f"{lane}.json"
        path.write_text(json.dumps({"schemaVersion": OBSERVATION_VERSION,
            "lane": lane, "status": "passed", "identity": identity,
            "scenarios": [scenario_record(lane, scenario)
                for scenario in REQUIRED_SCENARIOS[lane]]
            }), encoding="utf-8")
        lanes[lane]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    retained = {}
    for channel in REQUIRED_CHANNELS:
        retained[channel] = {"kind": channel,
            **evidence_record(f"retained/{channel}")}
    return {
        "producer": "protected-provider-workflow", "identity": identity,
        "generatedAt": "2026-08-09T00:00:00Z", "expiresAt": "2026-08-16T00:00:00Z",
        "supersededBy": None, "lanes": lanes,
        "compatibilityManifestRef": "artifact://compatibility.json",
        "retainedEvidence": retained,
        "telemetry": {name: {"sampleCount": 1, "identityLabels": [],
            **evidence_record(f"telemetry/{name}")} for name in REQUIRED_TELEMETRY},
        "rollout": {key: {"outcome": "passed",
            **evidence_record(f"rollout/{key}")} for key in
            ("canaryPolicy", "disableInteractiveChat", "historicalReads",
             "noRuntimeFallback", "temporaryFlagRetirement")},
    }


def _build(source: dict, root: Path) -> dict:
    return build_native_chat_acceptance_report(source, evidence_root=root,
        expected_commit="commit-3642", now=datetime(2026, 8, 9, 12, tzinfo=timezone.utc))


def test_complete_protected_native_chat_matrix_builds_report(tmp_path: Path) -> None:
    report = _build(_fixture(tmp_path), tmp_path)
    assert report["issue"] == "MoonLadderStudios/MoonMind#3642"
    assert report["status"] == "passed"
    assert set(report["lanes"]) == set(REQUIRED_LANES)


def test_repository_producer_evidence_assembles_without_fixture_lane_synthesis(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    manifest = dict(source)
    manifest.pop("lanes")
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    assembled = assemble_native_chat_acceptance_input(
        tmp_path, output_root=tmp_path
    )

    report = _build(assembled, tmp_path)
    assert report["status"] == "passed"
    assert set(report["lanes"]) == set(REQUIRED_LANES)


def test_repository_producer_assembler_rejects_missing_scenario(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    manifest = dict(source)
    manifest.pop("lanes")
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / "scenarios" / "authority-isolation" / "owner.json").unlink()

    with pytest.raises(ConformanceContractError, match="producer scenario is unreadable"):
        assemble_native_chat_acceptance_input(tmp_path, output_root=tmp_path)


@pytest.mark.parametrize("lane", REQUIRED_LANES)
def test_any_missing_or_failed_lane_closes_gate(tmp_path: Path, lane: str) -> None:
    source = _fixture(tmp_path)
    source["lanes"][lane]["status"] = "skipped"
    with pytest.raises(ConformanceContractError, match=f"lane {lane} did not pass"):
        _build(source, tmp_path)


def test_tampered_observation_or_mutable_identity_closes_gate(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    (tmp_path / "lanes" / "authority-isolation.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConformanceContractError, match="not bound|digest mismatch"):
        _build(source, tmp_path)
    source = _fixture(tmp_path)
    source["identity"]["hostImageDigest"] = "host:latest"
    with pytest.raises(ConformanceContractError, match="immutable SHA-256"):
        _build(source, tmp_path)


def test_status_only_observation_cannot_claim_a_lane(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    path = tmp_path / "lanes" / "authority-isolation.json"
    observation = json.loads(path.read_text())
    observation.pop("scenarios")
    path.write_text(json.dumps(observation), encoding="utf-8")
    source["lanes"]["authority-isolation"]["sha256"] = "sha256:" + hashlib.sha256(
        path.read_bytes()).hexdigest()
    with pytest.raises(ConformanceContractError, match="scenario inventory"):
        _build(source, tmp_path)


def test_fixture_authored_pass_without_producer_provenance_closes_gate(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    path = tmp_path / "scenarios" / "authority-isolation" / "owner.json"
    payload = json.loads(path.read_text())
    payload.pop("producer")
    path.write_text(json.dumps(payload), encoding="utf-8")
    scenario = json.loads(
        (tmp_path / "lanes" / "authority-isolation.json").read_text()
    )["scenarios"][0]
    scenario["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    lane_path = tmp_path / "lanes" / "authority-isolation.json"
    lane_payload = json.loads(lane_path.read_text())
    lane_payload["scenarios"][0] = scenario
    lane_path.write_text(json.dumps(lane_payload), encoding="utf-8")
    source["lanes"]["authority-isolation"]["sha256"] = (
        "sha256:" + hashlib.sha256(lane_path.read_bytes()).hexdigest()
    )
    with pytest.raises(ConformanceContractError, match="producer provenance"):
        _build(source, tmp_path)


def test_side_effect_counter_must_match_captured_requests(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    lane_path = tmp_path / "lanes" / "browser-network-isolation.json"
    lane_payload = json.loads(lane_path.read_text())
    lane_payload["scenarios"][0]["upstreamSideEffects"] = 1
    lane_path.write_text(json.dumps(lane_payload), encoding="utf-8")
    source["lanes"]["browser-network-isolation"]["sha256"] = (
        "sha256:" + hashlib.sha256(lane_path.read_bytes()).hexdigest()
    )
    with pytest.raises(ConformanceContractError, match="objective observation"):
        _build(source, tmp_path)


def test_generic_assertion_strings_do_not_prove_a_scenario(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    scenario_path = (
        tmp_path / "scenarios" / "authority-isolation" / "owner.json"
    )
    payload = json.loads(scenario_path.read_text())
    payload.pop("observation")
    payload["observedAssertions"] = ["route observed", "passed"]
    scenario_path.write_text(json.dumps(payload), encoding="utf-8")
    lane_path = tmp_path / "lanes" / "authority-isolation.json"
    lane = json.loads(lane_path.read_text())
    lane["scenarios"][0]["sha256"] = "sha256:" + hashlib.sha256(
        scenario_path.read_bytes()).hexdigest()
    lane_path.write_text(json.dumps(lane), encoding="utf-8")
    source["lanes"]["authority-isolation"]["sha256"] = "sha256:" + hashlib.sha256(
        lane_path.read_bytes()).hexdigest()

    with pytest.raises(ConformanceContractError, match="objective observation"):
        _build(source, tmp_path)


def test_one_observation_cannot_be_reused_for_unrelated_scenarios(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    lane_path = tmp_path / "lanes" / "authority-isolation.json"
    lane = json.loads(lane_path.read_text())
    first_path = tmp_path / lane["scenarios"][0]["evidenceRef"].removeprefix("artifact://")
    second_path = tmp_path / lane["scenarios"][1]["evidenceRef"].removeprefix("artifact://")
    first = json.loads(first_path.read_text())
    second = json.loads(second_path.read_text())
    second["observation"]["observationId"] = first["observation"]["observationId"]
    second_path.write_text(json.dumps(second), encoding="utf-8")
    lane["scenarios"][1]["sha256"] = "sha256:" + hashlib.sha256(
        second_path.read_bytes()).hexdigest()
    lane_path.write_text(json.dumps(lane), encoding="utf-8")
    source["lanes"]["authority-isolation"]["sha256"] = "sha256:" + hashlib.sha256(
        lane_path.read_bytes()).hexdigest()

    with pytest.raises(ConformanceContractError, match="reused"):
        _build(source, tmp_path)


def test_denial_scenario_cannot_report_an_allowed_outcome(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    scenario_path = (
        tmp_path / "scenarios" / "authority-isolation" / "unauthorized.json"
    )
    payload = json.loads(scenario_path.read_text())
    payload["observation"]["actualOutcome"] = "allowed"
    scenario_path.write_text(json.dumps(payload), encoding="utf-8")
    lane_path = tmp_path / "lanes" / "authority-isolation.json"
    lane = json.loads(lane_path.read_text())
    scenario = next(item for item in lane["scenarios"] if item["id"] == "unauthorized")
    scenario["sha256"] = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    lane_path.write_text(json.dumps(lane), encoding="utf-8")
    source["lanes"]["authority-isolation"]["sha256"] = "sha256:" + hashlib.sha256(
        lane_path.read_bytes()).hexdigest()

    with pytest.raises(ConformanceContractError, match="objective observation"):
        _build(source, tmp_path)


def test_retained_evidence_is_digest_bound_and_scanned_from_bytes(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    path = tmp_path / "retained" / "artifacts"
    path.write_text("token=exposed-value", encoding="utf-8")
    source["retainedEvidence"]["artifacts"]["sha256"] = "sha256:" + hashlib.sha256(
        path.read_bytes()).hexdigest()
    with pytest.raises(ConformanceContractError):
        _build(source, tmp_path)


def test_boolean_rollout_and_arbitrary_telemetry_are_not_evidence(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    source["rollout"]["canaryPolicy"] = True
    with pytest.raises(ConformanceContractError, match="rollout"):
        _build(source, tmp_path)
    source = _fixture(tmp_path)
    source["telemetry"]["bindingResolution"] = {"passed": 1}
    with pytest.raises(ConformanceContractError, match="observed"):
        _build(source, tmp_path)


def test_incomplete_transport_telemetry_or_retained_evidence_closes_gate(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    compatibility = tmp_path / "compatibility.json"
    payload = json.loads(compatibility.read_text())
    del payload["transports"]["websocket"]
    compatibility.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConformanceContractError, match="compatibility manifest digest mismatch|transports"):
        _build(source, tmp_path)
    source = _fixture(tmp_path)
    del source["telemetry"]["bindingResolution"]
    with pytest.raises(ConformanceContractError, match="telemetry"):
        _build(source, tmp_path)
    source = _fixture(tmp_path)
    (tmp_path / "retained" / "screenshots").unlink()
    with pytest.raises(ConformanceContractError, match="unresolved"):
        _build(source, tmp_path)
