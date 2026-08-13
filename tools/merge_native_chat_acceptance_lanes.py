#!/usr/bin/env python3
"""Merge trusted native-chat lane outputs and build the #3642 gate report.

The two lane jobs own observation and evidence collection.  This command owns
only the deterministic join: it rejects partial/overlapping lane inventories,
requires exact shared deployment identities, and delegates the final semantic
validation to ``build_native_chat_acceptance_report``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import ConformanceContractError  # noqa: E402
from moonmind.omnigent.native_chat_acceptance import (  # noqa: E402
    LANE_DETERMINISTIC,
    LANE_PROTECTED_LIVE,
    REQUIRED_SCENARIOS,
    SCENARIO_LANES,
    TRUSTED_LANE_PRODUCERS,
    TRUSTED_REPORT_PRODUCER,
    build_native_chat_acceptance_report,
)

LANE_SCHEMA_VERSION = "moonmind.omnigent.native-chat-acceptance-lane/v1"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceContractError(f"invalid lane artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ConformanceContractError(f"lane artifact is not an object: {path}")
    return value


def _validate_lane(value: Mapping[str, Any], *, lane: str) -> None:
    expected = {
        name for name, owner in SCENARIO_LANES.items() if owner == lane
    }
    if (
        value.get("schemaVersion") != LANE_SCHEMA_VERSION
        or value.get("lane") != lane
        or value.get("producer") != TRUSTED_LANE_PRODUCERS[lane]
        or value.get("status") != "passed"
    ):
        raise ConformanceContractError(f"{lane} lane lacks trusted passing provenance")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, Mapping) or set(scenarios) != expected:
        raise ConformanceContractError(f"{lane} lane scenario inventory is incomplete")
    objects = value.get("evidenceObjects")
    if not isinstance(objects, Mapping) or not objects:
        raise ConformanceContractError(f"{lane} lane lacks resolved evidence objects")
    for ref, item in objects.items():
        if (
            not isinstance(ref, str)
            or not ref.startswith("artifact://")
            or not isinstance(item, Mapping)
            or item.get("lane") != lane
            or item.get("producer") != TRUSTED_LANE_PRODUCERS[lane]
        ):
            raise ConformanceContractError(
                f"{lane} lane contains evidence with mismatched provenance"
            )


def merge_lanes(
    deterministic: Mapping[str, Any],
    protected_live: Mapping[str, Any],
    *,
    expected_commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_lane(deterministic, lane=LANE_DETERMINISTIC)
    _validate_lane(protected_live, lane=LANE_PROTECTED_LIVE)
    if deterministic.get("identities") != protected_live.get("identities"):
        raise ConformanceContractError("lane artifacts disagree on shared identities")
    deterministic_objects = dict(deterministic["evidenceObjects"])
    protected_objects = dict(protected_live["evidenceObjects"])
    overlap = set(deterministic_objects).intersection(protected_objects)
    if overlap:
        raise ConformanceContractError("lane evidence refs must be globally unique")
    scenarios = {
        **dict(deterministic["scenarios"]),
        **dict(protected_live["scenarios"]),
    }
    if set(scenarios) != set(REQUIRED_SCENARIOS):
        raise ConformanceContractError("combined scenario inventory is incomplete")
    deterministic_scan = deterministic.get("secretScan")
    protected_scan = protected_live.get("secretScan")
    if not isinstance(deterministic_scan, Mapping) or not isinstance(
        protected_scan, Mapping
    ):
        raise ConformanceContractError("each lane must publish its retained-evidence scan")
    all_objects = {**deterministic_objects, **protected_objects}
    retained_refs = sorted(
        ref
        for ref, item in all_objects.items()
        if not isinstance(item, Mapping) or item.get("kind") != "secret_scan"
    )
    cleanup = dict(protected_live.get("cleanup") or {})
    cleanup["preservedEvidenceRefs"] = retained_refs
    scan_refs = [
        *list(deterministic_scan.get("evidenceRefs") or []),
        *list(protected_scan.get("evidenceRefs") or []),
    ]
    source = {
        "producer": TRUSTED_REPORT_PRODUCER,
        "expiresAt": protected_live.get("expiresAt"),
        "supersedes": protected_live.get("supersedes"),
        "identities": dict(protected_live["identities"]),
        "safeIdentities": dict(protected_live.get("safeIdentities") or {}),
        "profilePolicyRefs": dict(protected_live.get("profilePolicyRefs") or {}),
        "scenarios": scenarios,
        "cleanup": cleanup,
        "secretScan": {
            "status": "passed",
            "evidenceRefs": list(dict.fromkeys(scan_refs)),
            "scannedRefs": retained_refs,
        },
        "evidenceObjects": all_objects,
    }
    return build_native_chat_acceptance_report(
        source, expected_commit=expected_commit, now=now
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("deterministic", type=Path)
    parser.add_argument("protected_live", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    report = merge_lanes(
        _read(args.deterministic),
        _read(args.protected_live),
        expected_commit=args.expected_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
