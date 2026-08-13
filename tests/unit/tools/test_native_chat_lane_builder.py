from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    LANE_DETERMINISTIC,
    REQUIRED_CASES,
    SCENARIO_LANES,
)


def _module():
    path = Path(__file__).parents[3] / "tools/build_native_chat_acceptance_lane.py"
    spec = importlib.util.spec_from_file_location("native_chat_lane_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observations(tmp_path: Path) -> dict:
    module = _module()
    for name in ("case", "audit", "cleanup", "scan"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"kind": name, "status": "passed"}) + "\n",
            encoding="utf-8",
        )
    digest = "a" * 64
    scenarios = {}
    for scenario, cases in REQUIRED_CASES.items():
        if SCENARIO_LANES[scenario] != LANE_DETERMINISTIC:
            continue
        scenarios[scenario] = {
            case: {
                "status": "passed",
                "authorizationDecision": "not_applicable",
                "upstreamSideEffectCount": 0,
                "expectedUpstreamSideEffectCount": 0,
                "durableAfterCleanup": True,
                "boundaryTests": [f"backend:test_{scenario}_{case}"],
                "evidenceFiles": [
                    {"file": "case.json", "kind": "test_result"}
                ],
            }
            for case in cases
        }
    return {
        "schemaVersion": module.OBSERVATION_SCHEMA_VERSION,
        "lane": LANE_DETERMINISTIC,
        "expiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "identities": {
            "moonmindCommit": "abc123",
            "moonmindBuild": "build-1",
            "hostArchitecture": "linux/amd64",
            "contractVersions": {
                "nativeUiBootstrap": "bootstrap/v1",
                "nativeUiRouteFeature": "route/v1",
                "outboundScan": "scan/v1",
                "telemetry": "telemetry/v1",
            },
            "images": {
                "server": f"server@sha256:{digest}",
                "ui": f"ui@sha256:{digest}",
                "host": f"host@sha256:{digest}",
            },
            "compatibilityManifestDigest": f"sha256:{digest}",
        },
        "safeIdentities": {},
        "sharedEvidence": {
            "auditFile": "audit.json",
            "cleanupFile": "cleanup.json",
            "secretScanFile": "scan.json",
        },
        "scenarios": scenarios,
    }


def test_builder_resolves_exact_case_files_into_durable_objects(tmp_path: Path) -> None:
    module = _module()
    lane = module.build_lane(
        _observations(tmp_path),
        lane=LANE_DETERMINISTIC,
        evidence_root=tmp_path,
        expected_commit="abc123",
    )
    assert lane["status"] == "passed"
    assert set(lane["scenarios"]) == {
        name for name, owner in SCENARIO_LANES.items() if owner == LANE_DETERMINISTIC
    }
    assert lane["secretScan"]["scannedRefs"]
    scan_ref = lane["secretScan"]["evidenceRefs"][0]
    assert lane["evidenceObjects"][scan_ref]["scannedRefs"] == lane["secretScan"][
        "scannedRefs"
    ]
    assert lane["evidenceObjects"][scan_ref]["scanCompletedAfterCleanup"] is True


def test_builder_rejects_missing_case_and_unobserved_side_effect(tmp_path: Path) -> None:
    module = _module()
    observations = _observations(tmp_path)
    scenario = next(iter(observations["scenarios"]))
    observations["scenarios"][scenario].pop(
        next(iter(observations["scenarios"][scenario]))
    )
    with pytest.raises(ConformanceContractError, match="case inventory"):
        module.build_lane(
            observations,
            lane=LANE_DETERMINISTIC,
            evidence_root=tmp_path,
            expected_commit="abc123",
        )

    observations = _observations(tmp_path)
    scenario = next(iter(observations["scenarios"]))
    case = next(iter(observations["scenarios"][scenario].values()))
    case["upstreamSideEffectCount"] = 1
    with pytest.raises(ConformanceContractError, match="controlling outcome"):
        module.build_lane(
            observations,
            lane=LANE_DETERMINISTIC,
            evidence_root=tmp_path,
            expected_commit="abc123",
        )


def test_builder_rejects_evidence_outside_run_root(tmp_path: Path) -> None:
    module = _module()
    observations = _observations(tmp_path)
    scenario = next(iter(observations["scenarios"]))
    case = next(iter(observations["scenarios"][scenario].values()))
    case["evidenceFiles"][0]["file"] = "../outside.json"
    with pytest.raises(ConformanceContractError, match="escapes evidence root"):
        module.build_lane(
            observations,
            lane=LANE_DETERMINISTIC,
            evidence_root=tmp_path,
            expected_commit="abc123",
        )


def test_builder_rejects_case_without_production_boundary_test(tmp_path: Path) -> None:
    module = _module()
    observations = _observations(tmp_path)
    scenario = next(iter(observations["scenarios"]))
    case = next(iter(observations["scenarios"][scenario].values()))
    case["boundaryTests"] = []
    with pytest.raises(ConformanceContractError, match="production-boundary"):
        module.build_lane(
            observations,
            lane=LANE_DETERMINISTIC,
            evidence_root=tmp_path,
            expected_commit="abc123",
        )
