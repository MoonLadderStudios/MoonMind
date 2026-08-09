from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    COMPATIBILITY_VERSION, OBSERVATION_VERSION, REQUIRED_CHANNELS,
    REQUIRED_LANES, REQUIRED_TELEMETRY, REQUIRED_TRANSPORTS,
    build_native_chat_acceptance_report,
)


def _fixture(root: Path) -> dict:
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
                       "status": "passed", "identity": identity}
        # Written again after compatibility identity is finalised below.
        path.write_text(json.dumps(observation), encoding="utf-8")
        lanes[lane] = {"status": "passed", "evidenceRef": f"artifact://lanes/{lane}.json"}
    compatibility = {"schemaVersion": COMPATIBILITY_VERSION,
                     "transports": {name: "passed" for name in REQUIRED_TRANSPORTS}}
    compatibility_path = root / "compatibility.json"
    compatibility_path.write_text(json.dumps(compatibility), encoding="utf-8")
    identity["compatibilityManifestDigest"] = "sha256:" + hashlib.sha256(
        compatibility_path.read_bytes()).hexdigest()
    for lane in REQUIRED_LANES:
        path = root / "lanes" / f"{lane}.json"
        path.write_text(json.dumps({"schemaVersion": OBSERVATION_VERSION,
            "lane": lane, "status": "passed", "identity": identity}), encoding="utf-8")
        lanes[lane]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    retained = {}
    for channel in REQUIRED_CHANNELS:
        path = root / "retained" / channel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("redacted evidence", encoding="utf-8")
        retained[channel] = f"artifact://retained/{channel}"
    return {
        "producer": "protected-provider-workflow", "identity": identity,
        "generatedAt": "2026-08-09T00:00:00Z", "expiresAt": "2026-08-16T00:00:00Z",
        "supersededBy": None, "lanes": lanes,
        "compatibilityManifestRef": "artifact://compatibility.json",
        "retainedEvidence": retained, "retainedEvidenceSecretScan": "passed",
        "telemetry": {name: {"passed": 1} for name in REQUIRED_TELEMETRY},
        "rollout": {key: True for key in ("canaryPolicy", "disableInteractiveChat",
            "historicalReads", "noRuntimeFallback", "temporaryFlagRetirement")},
    }


def _build(source: dict, root: Path) -> dict:
    return build_native_chat_acceptance_report(source, evidence_root=root,
        expected_commit="commit-3642", now=datetime(2026, 8, 9, 12, tzinfo=timezone.utc))


def test_complete_protected_native_chat_matrix_builds_report(tmp_path: Path) -> None:
    report = _build(_fixture(tmp_path), tmp_path)
    assert report["issue"] == "MoonLadderStudios/MoonMind#3642"
    assert report["status"] == "passed"
    assert set(report["lanes"]) == set(REQUIRED_LANES)


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
