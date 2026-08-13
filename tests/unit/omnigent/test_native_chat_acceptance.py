"""Fail-closed gate tests for the #3642 native Chat acceptance report."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_chat_acceptance import (
    CASE_EVIDENCE_SCHEMA_VERSION,
    DURABLE_REF_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    LANE_DETERMINISTIC,
    LANE_PROTECTED_LIVE,
    REQUIRED_CASES,
    REQUIRED_CLEANUP_CASES,
    REQUIRED_SCENARIOS,
    SCENARIO_LANES,
    TRUSTED_LANE_PRODUCERS,
    TRUSTED_REPORT_PRODUCER,
    build_native_chat_acceptance_report,
)

_NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _source() -> dict:
    digest = "a" * 64

    def passed(name: str, lane: str) -> dict:
        return {"status": "passed", "lane": lane, "evidenceRefs": [f"artifact://{name}"]}

    identities = {
        "moonmindCommit": "abc123",
        "moonmindBuild": "build-7",
        "hostArchitecture": "linux/amd64",
        "contractVersions": {
            "nativeUiBootstrap": "moonmind.omnigent_native_ui.bootstrap.v1",
            "nativeUiRouteFeature": "1",
            "outboundScan": "moonmind.omnigent.native_outbound_scan.v1",
            "telemetry": "moonmind.omnigent.native_chat_telemetry/v1",
        },
        "images": {
            "server": f"server@sha256:{digest}",
            "ui": f"ui@sha256:{digest}",
            "host": f"host@sha256:{digest}",
        },
        "compatibilityManifestDigest": f"sha256:{digest}",
    }
    source = {
        "producer": TRUSTED_REPORT_PRODUCER,
        "expiresAt": "2026-07-22T00:00:00Z",
        "supersedes": None,
        "identities": identities,
        "safeIdentities": {
            "workflowRef": "wf-ref-1",
            "runRef": "run-ref-1",
            "stepRef": "step-ref-1",
            "agentRunRef": "agentrun-ref-1",
            "bindingRef": "chatb_opaque_1",
        },
        "profilePolicyRefs": {
            "profileRef": "artifact://profile",
            "launchPolicyRef": "artifact://launch-policy",
            "effectiveLaunchSnapshotRef": "artifact://effective-launch/1",
            "providerProfileRef": "artifact://provider-profile",
        },
        "scenarios": {
            name: passed(name, SCENARIO_LANES[name]) for name in REQUIRED_SCENARIOS
        },
        "cleanup": {
            "status": "passed",
            "lane": LANE_PROTECTED_LIVE,
            "evidenceRefs": ["artifact://cleanup"],
            "historicalEvidencePreserved": True,
            "leasesReleased": True,
        },
        "secretScan": {"status": "passed", "evidenceRefs": [], "scannedRefs": []},
    }

    claims = {name: f"scenario:{name}" for name in REQUIRED_SCENARIOS}
    claims["cleanup"] = "cleanup"
    evidence_objects: dict[str, Any] = {}
    def durable(ref: str, *, kind: str, lane: str) -> None:
        evidence_objects[ref] = {
            "schemaVersion": DURABLE_REF_SCHEMA_VERSION,
            "kind": kind,
            "status": "passed",
            "retainedAfterCleanup": True,
            "identities": copy.deepcopy(identities),
            "lane": lane,
            "producer": TRUSTED_LANE_PRODUCERS[lane],
            "sha256": "sha256:" + "d" * 64,
            "contentType": "application/json",
            "sizeBytes": 42,
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
        }

    for lane in (LANE_DETERMINISTIC, LANE_PROTECTED_LIVE):
        durable(f"artifact://channel/{lane}", kind="artifact", lane=lane)
        durable(f"artifact://audit/{lane}", kind="mutation_audit", lane=lane)
        durable(f"artifact://cleanup-ref/{lane}", kind="cleanup", lane=lane)
        durable(f"artifact://secret-scan/{lane}", kind="secret_scan", lane=lane)
    durable("artifact://lease-release", kind="lease_release", lane=LANE_PROTECTED_LIVE)
    for ref, kind in (
        ("artifact://profile", "profile"),
        ("artifact://launch-policy", "launch_policy"),
        ("artifact://effective-launch/1", "effective_launch"),
        ("artifact://provider-profile", "provider_profile"),
    ):
        durable(ref, kind=kind, lane=LANE_PROTECTED_LIVE)

    for name, claim in claims.items():
        lane = LANE_PROTECTED_LIVE if name == "cleanup" else SCENARIO_LANES[name]
        required_cases = (
            REQUIRED_CLEANUP_CASES if name == "cleanup" else REQUIRED_CASES[name]
        )
        cases: dict[str, Any] = {}
        for case_name in required_cases:
            case_ref = f"artifact://case/{name}/{case_name}"
            cases[case_name] = {"status": "passed", "evidenceRefs": [case_ref]}
            evidence_objects[case_ref] = {
                "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION,
                "claim": claim,
                "case": case_name,
                "status": "passed",
                "identities": copy.deepcopy(identities),
                "lane": lane,
                "producer": TRUSTED_LANE_PRODUCERS[lane],
                "outcome": {
                    "result": "passed",
                    "authorizationDecision": "not_applicable",
                    "upstreamSideEffectCount": 0,
                    "expectedUpstreamSideEffectCount": 0,
                    "durableAfterCleanup": True,
                },
                "boundaryTests": [
                    (
                        f"protected-live-action:{name}.{case_name}"
                        if lane == LANE_PROTECTED_LIVE
                        else f"backend:test_{name}_{case_name}"
                    )
                ],
                "evidenceRefs": [f"artifact://channel/{lane}"],
                "auditRef": f"artifact://audit/{lane}",
                "cleanupRef": f"artifact://cleanup-ref/{lane}",
                "secretScanRef": f"artifact://secret-scan/{lane}",
                "generatedAt": "2026-07-21T00:00:00Z",
                "expiresAt": "2026-07-22T00:00:00Z",
                "revokedAt": None,
                "supersededBy": None,
            }
        evidence_objects[f"artifact://{name}"] = {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": claim,
            "status": "passed",
            "identities": copy.deepcopy(identities),
            "lane": lane,
            "evidenceRefs": [f"artifact://channel/{lane}"],
            "cases": cases,
            "generatedAt": "2026-07-21T00:00:00Z",
            "expiresAt": "2026-07-22T00:00:00Z",
            "revokedAt": None,
            "supersededBy": None,
            "producer": TRUSTED_LANE_PRODUCERS[lane],
            "secretScanRef": f"artifact://secret-scan/{lane}",
            "cleanupRef": f"artifact://cleanup-ref/{lane}",
        }
    retained = sorted(
        ref for ref, item in evidence_objects.items()
        if item.get("kind") != "secret_scan"
    )
    for lane in (LANE_DETERMINISTIC, LANE_PROTECTED_LIVE):
        evidence_objects[f"artifact://secret-scan/{lane}"].update(
            {
                "scannedRefs": sorted(
                    ref
                    for ref, item in evidence_objects.items()
                    if item.get("lane") == lane
                    and item.get("kind") != "secret_scan"
                ),
                "secretFindings": 0,
                "scanCompletedAfterCleanup": True,
            }
        )
    source["cleanup"]["preservedEvidenceRefs"] = retained
    source["cleanup"]["releasedLeaseRefs"] = ["artifact://lease-release"]
    source["secretScan"] = {
        "status": "passed",
        "evidenceRefs": [
            "artifact://secret-scan/deterministic",
            "artifact://secret-scan/protected_live",
        ],
        "scannedRefs": retained,
    }
    source["evidenceObjects"] = evidence_objects
    return source


def _build(source: dict, **kwargs: Any) -> dict:
    kwargs.setdefault("now", _NOW)
    return build_native_chat_acceptance_report(source, **kwargs)


def test_complete_matrix_builds_publishable_issue_3642_report() -> None:
    report = _build(_source())
    assert report["status"] == "passed"
    assert report["issue"] == "MoonLadderStudios/MoonMind#3642"
    assert set(report["scenarios"]) == set(REQUIRED_SCENARIOS)
    # Both lanes are recorded and non-empty; the protected-live lane is required.
    assert report["lanes"][LANE_DETERMINISTIC]
    assert report["lanes"][LANE_PROTECTED_LIVE]
    assert report["evidenceObjects"]
    for name, row in report["scenarios"].items():
        assert row["lane"] == SCENARIO_LANES[name]


def test_expected_commit_must_match_evidence_identity() -> None:
    with pytest.raises(ConformanceContractError, match="different commit"):
        _build(_source(), expected_commit="def456")


@pytest.mark.parametrize("scenario", ["binding-authorization-isolation", "protected-stock-image-journey"])
def test_missing_or_failed_controlling_scenario_refuses_publication(scenario: str) -> None:
    source = _source()
    source["scenarios"][scenario] = {
        "status": "failed",
        "lane": SCENARIO_LANES[scenario],
        "evidenceRefs": ["artifact://failure"],
    }
    with pytest.raises(ConformanceContractError, match="did not pass"):
        _build(source)


def test_protected_live_row_in_wrong_lane_refuses_publication() -> None:
    source = _source()
    # A protected-live row attested only in the deterministic (hermetic) lane must
    # not satisfy the gate.
    source["scenarios"]["protected-stock-image-journey"]["lane"] = LANE_DETERMINISTIC
    with pytest.raises(ConformanceContractError, match="protected_live lane"):
        _build(source)


def test_mutable_image_and_missing_manifest_digest_refuse_publication() -> None:
    mutable = _source()
    mutable["identities"]["images"]["host"] = "host:latest"
    with pytest.raises(ConformanceContractError, match="digest-pinned"):
        _build(mutable)

    no_manifest = _source()
    no_manifest["identities"]["compatibilityManifestDigest"] = "not-a-digest"
    with pytest.raises(ConformanceContractError, match="compatibility manifest digest"):
        _build(no_manifest)


def test_missing_ui_image_refuses_publication() -> None:
    source = _source()
    del source["identities"]["images"]["ui"]
    with pytest.raises(ConformanceContractError, match="digest-pinned"):
        _build(source)


def test_missing_safe_refs_refuse_publication() -> None:
    no_binding = _source()
    del no_binding["safeIdentities"]["bindingRef"]
    with pytest.raises(ConformanceContractError, match="binding identities"):
        _build(no_binding)

    no_profile = _source()
    del no_profile["profilePolicyRefs"]["providerProfileRef"]
    with pytest.raises(ConformanceContractError, match="provider-profile refs"):
        _build(no_profile)


def test_incomplete_cleanup_refuses_publication() -> None:
    source = _source()
    source["cleanup"]["leasesReleased"] = False
    with pytest.raises(ConformanceContractError, match="release leases"):
        _build(source)


def test_secret_like_material_refuses_publication() -> None:
    source = _source()
    source["scenarios"]["credential-browser-isolation"]["note"] = "authorization=unsafe"
    with pytest.raises(ConformanceContractError, match="secret-like"):
        _build(source)


def test_failed_secret_scan_refuses_publication() -> None:
    source = _source()
    source["secretScan"] = {"status": "failed"}
    with pytest.raises(ConformanceContractError, match="secret scan"):
        _build(source)


def test_unresolved_or_identity_mismatched_evidence_refuses_publication() -> None:
    unresolved = _source()
    unresolved["scenarios"]["diagnostic-fallback"]["evidenceRefs"] = ["artifact://missing"]
    with pytest.raises(ConformanceContractError, match="unresolved"):
        _build(unresolved)

    mismatched = _source()
    evidence = mismatched["evidenceObjects"]["artifact://diagnostic-fallback"]
    evidence["identities"]["moonmindCommit"] = "different"
    with pytest.raises(ConformanceContractError, match="different identities"):
        _build(mismatched)


@pytest.mark.parametrize(
    "override,match",
    [
        ({"expiresAt": "2026-07-21T11:59:59Z"}, "validity period"),
        ({"revokedAt": "2026-07-21T11:00:00Z"}, "revoked"),
        ({"supersededBy": "artifact://replacement"}, "superseded"),
        ({"generatedAt": "not-a-time"}, "invalid generation"),
    ],
)
def test_stale_revoked_superseded_or_malformed_evidence_refuses_publication(
    override: dict, match: str
) -> None:
    source = _source()
    source["evidenceObjects"]["artifact://high-security-outbound-scan"].update(override)
    with pytest.raises(ConformanceContractError, match=match):
        _build(source)


def test_expired_report_refuses_publication() -> None:
    source = _source()
    source["expiresAt"] = "2026-07-21T11:00:00Z"
    with pytest.raises(ConformanceContractError, match="already expired"):
        _build(source)


def test_lazy_resolver_resolves_every_ref() -> None:
    source = _source()
    objects = source.pop("evidenceObjects")

    resolved: list[str] = []

    def resolver(ref: str) -> dict:
        resolved.append(ref)
        return objects[ref]

    report = _build(source, evidence_resolver=resolver)
    assert report["status"] == "passed"
    # Every scenario, cleanup, and nested case ref was independently resolved.
    assert any(ref.startswith("artifact://case/") for ref in resolved)


def test_generic_or_incomplete_case_inventory_cannot_publish() -> None:
    source = _source()
    evidence = source["evidenceObjects"]["artifact://binding-authorization-isolation"]
    evidence["cases"] = {
        "generic-authorization-check": {
            "status": "passed",
            "evidenceRefs": ["artifact://case/generic"],
        }
    }
    with pytest.raises(ConformanceContractError, match="case inventory"):
        _build(source)


def test_bare_case_status_or_dangling_nested_ref_cannot_publish() -> None:
    source = _source()
    evidence = source["evidenceObjects"]["artifact://high-security-outbound-scan"]
    case = next(iter(evidence["cases"].values()))
    case["evidenceRefs"] = []
    with pytest.raises(ConformanceContractError, match="case inventory"):
        _build(source)

    source = _source()
    evidence = source["evidenceObjects"]["artifact://high-security-outbound-scan"]
    case = next(iter(evidence["cases"].values()))
    case["evidenceRefs"] = ["artifact://case/dangling"]
    with pytest.raises(ConformanceContractError, match="unresolved"):
        _build(source)


def test_case_without_resolved_boundary_test_cannot_publish() -> None:
    source = _source()
    evidence = source["evidenceObjects"]["artifact://high-security-outbound-scan"]
    case_ref = next(iter(evidence["cases"].values()))["evidenceRefs"][0]
    source["evidenceObjects"][case_ref]["boundaryTests"] = []
    with pytest.raises(ConformanceContractError, match="controlling outcome"):
        _build(source)


def test_partial_post_cleanup_lane_scan_cannot_publish() -> None:
    source = _source()
    source["evidenceObjects"]["artifact://secret-scan/deterministic"][
        "scannedRefs"
    ].pop()
    with pytest.raises(ConformanceContractError, match="complete lane"):
        _build(source)


def test_untrusted_lane_or_report_producer_cannot_publish() -> None:
    source = _source()
    source["producer"] = "manual"
    with pytest.raises(ConformanceContractError, match="trusted workflow producer"):
        _build(source)

    source = _source()
    source["evidenceObjects"]["artifact://diagnostic-fallback"]["producer"] = "manual"
    with pytest.raises(ConformanceContractError, match="trusted lane provenance"):
        _build(source)
