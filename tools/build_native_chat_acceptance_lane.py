#!/usr/bin/env python3
"""Build one resolved native-chat acceptance lane from observed evidence.

The input is an observation ledger written by the production-shaped journey.
This command never invents a result: every exact case must be present, passed,
and linked to a retained file plus the lane's audit, cleanup, and secret-scan
files.  It resolves and digests those bytes into the durable object graph later
consumed by ``merge_native_chat_acceptance_lanes.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    ConformanceContractError,
    assert_secret_free,
)
from moonmind.omnigent.native_chat_acceptance import (  # noqa: E402
    CASE_EVIDENCE_SCHEMA_VERSION,
    DURABLE_REF_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    LANE_DETERMINISTIC,
    LANE_PROTECTED_LIVE,
    REQUIRED_CASES,
    REQUIRED_CLEANUP_CASES,
    REQUIRED_PROFILE_REFS,
    SCENARIO_LANES,
    TRUSTED_LANE_PRODUCERS,
    _validate_identities,
)
from tools.merge_native_chat_acceptance_lanes import LANE_SCHEMA_VERSION  # noqa: E402

OBSERVATION_SCHEMA_VERSION = (
    "moonmind.omnigent.native-chat-acceptance-observations/v1"
)
_PROFILE_KINDS = {
    "profileRef": "profile",
    "launchPolicyRef": "launch_policy",
    "effectiveLaunchSnapshotRef": "effective_launch",
    "providerProfileRef": "provider_profile",
}


def _within(root: Path, raw: object, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ConformanceContractError(f"{label} file is required")
    candidate = (root / raw).resolve()
    if root != candidate and root not in candidate.parents:
        raise ConformanceContractError(f"{label} file escapes evidence root")
    if not candidate.is_file():
        raise ConformanceContractError(f"{label} file does not resolve")
    return candidate


def build_lane(
    observations: Mapping[str, Any],
    *,
    lane: str,
    evidence_root: Path,
    expected_commit: str,
) -> dict[str, Any]:
    if lane not in {LANE_DETERMINISTIC, LANE_PROTECTED_LIVE}:
        raise ConformanceContractError("unsupported native-chat acceptance lane")
    if (
        observations.get("schemaVersion") != OBSERVATION_SCHEMA_VERSION
        or observations.get("lane") != lane
    ):
        raise ConformanceContractError("observation ledger schema or lane is invalid")
    identities = _validate_identities(
        observations, expected_commit=expected_commit
    )
    safe_identities = observations.get("safeIdentities")
    if (
        not isinstance(safe_identities, Mapping)
        or (lane == LANE_PROTECTED_LIVE and not safe_identities)
    ):
        raise ConformanceContractError("lane safe identities are required")
    producer = TRUSTED_LANE_PRODUCERS[lane]
    root = evidence_root.resolve()
    generated = datetime.now(timezone.utc).isoformat()
    expires = observations.get("expiresAt")
    try:
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConformanceContractError("lane expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ConformanceContractError("lane expiry must be in the future")

    objects: dict[str, dict[str, Any]] = {}

    def durable(path_value: object, *, label: str, kind: str) -> str:
        path = _within(root, path_value, label=label)
        raw = path.read_bytes()
        assert_secret_free(raw.decode("utf-8", errors="replace"))
        digest = hashlib.sha256(raw).hexdigest()
        ref = f"artifact://{lane}/{kind}/{digest}"
        item = {
            "schemaVersion": DURABLE_REF_SCHEMA_VERSION,
            "kind": kind,
            "status": "passed",
            "retainedAfterCleanup": True,
            "identities": identities,
            "lane": lane,
            "producer": producer,
            "sha256": f"sha256:{digest}",
            "contentType": (
                "application/xml"
                if path.suffix.lower() == ".xml"
                else "application/json"
                if path.suffix.lower() == ".json"
                else "application/octet-stream"
            ),
            "sizeBytes": len(raw),
            "generatedAt": generated,
            "expiresAt": expires,
            "revokedAt": None,
            "supersededBy": None,
        }
        prior = objects.get(ref)
        if prior is not None and prior != item:
            raise ConformanceContractError("durable evidence digest collision")
        objects[ref] = item
        return ref

    shared = observations.get("sharedEvidence")
    if not isinstance(shared, Mapping):
        raise ConformanceContractError("lane shared evidence is required")
    audit_ref = durable(
        shared.get("auditFile"), label="lane audit", kind="mutation_audit"
    )
    cleanup_ref = durable(
        shared.get("cleanupFile"), label="lane cleanup", kind="cleanup"
    )
    scan_ref = durable(
        shared.get("secretScanFile"), label="lane secret scan", kind="secret_scan"
    )

    expected_scenarios = {
        name for name, owner in SCENARIO_LANES.items() if owner == lane
    }
    observed_scenarios = observations.get("scenarios")
    if not isinstance(observed_scenarios, Mapping) or set(observed_scenarios) != expected_scenarios:
        raise ConformanceContractError("lane observation scenario inventory is incomplete")

    scenarios: dict[str, Any] = {}

    def build_claim(name: str, cases_value: object, *, claim: str) -> dict[str, Any]:
        required_cases = (
            REQUIRED_CLEANUP_CASES if name == "cleanup" else REQUIRED_CASES[name]
        )
        if not isinstance(cases_value, Mapping) or set(cases_value) != set(required_cases):
            raise ConformanceContractError(f"{name} observation case inventory is incomplete")
        cases: dict[str, Any] = {}
        channel_refs: list[str] = []
        for case_name in required_cases:
            observation = cases_value[case_name]
            if not isinstance(observation, Mapping) or observation.get("status") != "passed":
                raise ConformanceContractError(f"{name}/{case_name} did not pass")
            decision = observation.get("authorizationDecision")
            actual = observation.get("upstreamSideEffectCount")
            expected = observation.get("expectedUpstreamSideEffectCount")
            if (
                decision not in {"allowed", "denied", "not_applicable"}
                or not isinstance(actual, int)
                or not isinstance(expected, int)
                or actual != expected
                or observation.get("durableAfterCleanup") is not True
            ):
                raise ConformanceContractError(
                    f"{name}/{case_name} lacks an observed controlling outcome"
                )
            evidence_files = observation.get("evidenceFiles")
            boundary_tests = observation.get("boundaryTests")
            if (
                not isinstance(boundary_tests, list)
                or not boundary_tests
                or not all(
                    isinstance(test, str) and ":" in test and test.strip()
                    for test in boundary_tests
                )
            ):
                raise ConformanceContractError(
                    f"{name}/{case_name} lacks resolved production-boundary tests"
                )
            if not isinstance(evidence_files, list) or not evidence_files:
                raise ConformanceContractError(
                    f"{name}/{case_name} lacks exact channel evidence files"
                )
            case_channel_refs: list[str] = []
            for index, evidence_file in enumerate(evidence_files):
                if not isinstance(evidence_file, Mapping):
                    raise ConformanceContractError(
                        f"{name}/{case_name} channel evidence is malformed"
                    )
                channel_ref = durable(
                    evidence_file.get("file"),
                    label=f"{name}/{case_name} channel {index}",
                    kind=str(evidence_file.get("kind") or "artifact"),
                )
                case_channel_refs.append(channel_ref)
                channel_refs.append(channel_ref)
            case_ref = f"artifact://{lane}/case/{name}/{case_name}"
            objects[case_ref] = {
                "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION,
                "claim": claim,
                "case": case_name,
                "status": "passed",
                "identities": identities,
                "lane": lane,
                "producer": producer,
                "outcome": {
                    "result": "passed",
                    "authorizationDecision": decision,
                    "upstreamSideEffectCount": actual,
                    "expectedUpstreamSideEffectCount": expected,
                    "durableAfterCleanup": True,
                },
                "boundaryTests": sorted(set(boundary_tests)),
                "evidenceRefs": list(dict.fromkeys(case_channel_refs)),
                "auditRef": audit_ref,
                "cleanupRef": cleanup_ref,
                "secretScanRef": scan_ref,
                "generatedAt": generated,
                "expiresAt": expires,
                "revokedAt": None,
                "supersededBy": None,
            }
            cases[case_name] = {"status": "passed", "evidenceRefs": [case_ref]}
        scenario_ref = f"artifact://{lane}/scenario/{name}"
        objects[scenario_ref] = {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "status": "passed",
            "identities": identities,
            "lane": lane,
            "producer": producer,
            "evidenceRefs": list(dict.fromkeys(channel_refs)),
            "cases": cases,
            "cleanupRef": cleanup_ref,
            "secretScanRef": scan_ref,
            "generatedAt": generated,
            "expiresAt": expires,
            "revokedAt": None,
            "supersededBy": None,
        }
        return {"status": "passed", "lane": lane, "evidenceRefs": [scenario_ref]}

    for scenario in sorted(expected_scenarios):
        scenarios[scenario] = build_claim(
            scenario,
            observed_scenarios[scenario],
            claim=f"scenario:{scenario}",
        )

    profile_policy_refs: dict[str, str] = {}
    cleanup: dict[str, Any] = {}
    if lane == LANE_PROTECTED_LIVE:
        profile_files = observations.get("profilePolicyFiles")
        if not isinstance(profile_files, Mapping) or set(profile_files) != set(REQUIRED_PROFILE_REFS):
            raise ConformanceContractError("protected lane profile/policy files are incomplete")
        for key, kind in _PROFILE_KINDS.items():
            profile_policy_refs[key] = durable(
                profile_files[key], label=key, kind=kind
            )
        cleanup = build_claim(
            "cleanup", observations.get("cleanupCases"), claim="cleanup"
        )
        cleanup.update(
            {
                "historicalEvidencePreserved": True,
                "leasesReleased": True,
                "releasedLeaseRefs": [
                    durable(
                        observations.get("leaseReleaseFile"),
                        label="lease release",
                        kind="lease_release",
                    )
                ],
            }
        )

    retained_refs = sorted(
        ref for ref, item in objects.items() if item.get("kind") != "secret_scan"
    )
    # The trusted lane builder performs the post-cleanup scan over every source
    # file while resolving it and scans the synthesized object graph below.
    # Preserve the exact covered-ref inventory in the durable scan object so
    # the final gate can independently reject a partial or pre-cleanup scan.
    objects[scan_ref].update(
        {
            "scannedRefs": retained_refs,
            "secretFindings": 0,
            "scanCompletedAfterCleanup": True,
        }
    )
    assert_secret_free(objects)
    return {
        "schemaVersion": LANE_SCHEMA_VERSION,
        "lane": lane,
        "producer": producer,
        "status": "passed",
        "expiresAt": expires,
        "supersedes": observations.get("supersedes"),
        "identities": identities,
        "safeIdentities": dict(safe_identities),
        "profilePolicyRefs": profile_policy_refs,
        "scenarios": scenarios,
        "cleanup": cleanup,
        "secretScan": {
            "status": "passed",
            "evidenceRefs": [scan_ref],
            "scannedRefs": retained_refs,
        },
        "evidenceObjects": objects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("observations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--lane", choices=(LANE_DETERMINISTIC, LANE_PROTECTED_LIVE), required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    raw = json.loads(args.observations.read_text(encoding="utf-8"))
    result = build_lane(
        raw,
        lane=args.lane,
        evidence_root=args.evidence_root,
        expected_commit=args.expected_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
