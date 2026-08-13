from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    LANE_DETERMINISTIC,
    LANE_PROTECTED_LIVE,
    SCENARIO_LANES,
    TRUSTED_LANE_PRODUCERS,
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _modules():
    root = Path(__file__).parents[3]
    merger = _load(
        root / "tools/merge_native_chat_acceptance_lanes.py", "native_chat_merger"
    )
    fixtures = _load(
        root / "tests/unit/omnigent/test_native_chat_acceptance.py",
        "native_chat_acceptance_fixtures",
    )
    return merger, fixtures


def _lanes():
    merger, fixtures = _modules()
    source = fixtures._source()

    def lane_payload(lane: str) -> dict:
        objects = {
            ref: copy.deepcopy(item)
            for ref, item in source["evidenceObjects"].items()
            if item.get("lane") == lane
        }
        retained = [
            ref for ref, item in objects.items() if item.get("kind") != "secret_scan"
        ]
        return {
            "schemaVersion": merger.LANE_SCHEMA_VERSION,
            "lane": lane,
            "producer": TRUSTED_LANE_PRODUCERS[lane],
            "status": "passed",
            "expiresAt": source["expiresAt"],
            "supersedes": None,
            "identities": copy.deepcopy(source["identities"]),
            "safeIdentities": copy.deepcopy(source["safeIdentities"]),
            "profilePolicyRefs": copy.deepcopy(source["profilePolicyRefs"]),
            "scenarios": {
                name: copy.deepcopy(row)
                for name, row in source["scenarios"].items()
                if SCENARIO_LANES[name] == lane
            },
            "cleanup": copy.deepcopy(source["cleanup"]),
            "secretScan": {
                "status": "passed",
                "evidenceRefs": [f"artifact://secret-scan/{lane}"],
                "scannedRefs": retained,
            },
            "evidenceObjects": objects,
        }

    return merger, lane_payload(LANE_DETERMINISTIC), lane_payload(LANE_PROTECTED_LIVE)


def test_merge_requires_and_validates_both_exact_lane_inventories() -> None:
    merger, deterministic, protected = _lanes()
    report = merger.merge_lanes(
        deterministic, protected, expected_commit="abc123", now=_modules()[1]._NOW
    )
    assert report["status"] == "passed"
    assert report["identities"]["moonmindCommit"] == "abc123"


def test_merge_rejects_wrong_producer_and_partial_lane() -> None:
    merger, deterministic, protected = _lanes()
    deterministic["producer"] = "untrusted"
    with pytest.raises(ConformanceContractError, match="trusted passing provenance"):
        merger.merge_lanes(deterministic, protected, expected_commit="abc123")

    merger, deterministic, protected = _lanes()
    deterministic["scenarios"].pop(next(iter(deterministic["scenarios"])))
    with pytest.raises(ConformanceContractError, match="inventory"):
        merger.merge_lanes(deterministic, protected, expected_commit="abc123")


def test_merge_rejects_identity_disagreement() -> None:
    merger, deterministic, protected = _lanes()
    protected["identities"]["moonmindCommit"] = "other"
    with pytest.raises(ConformanceContractError, match="shared identities"):
        merger.merge_lanes(deterministic, protected, expected_commit="abc123")
