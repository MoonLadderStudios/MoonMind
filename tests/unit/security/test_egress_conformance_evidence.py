"""Hermetic coverage for versioned egress conformance evidence.

MoonLadderStudios/MoonMind#3625.  Proves per-row egress evidence is
tamper-evident (digest-bound and re-verifiable after cleanup) and secret-clean
before it is ever published, without requiring a live Docker daemon.
"""

from __future__ import annotations

import json

import pytest

from moonmind.security.egress_conformance_evidence import (
    EGRESS_EVIDENCE_DIGEST_KEY,
    EgressEvidenceDigestError,
    EgressEvidenceSecretError,
    attach_evidence_digest,
    evidence_content_digest,
    parse_and_verify_conformance_evidence,
    publish_conformance_evidence,
    serialize_conformance_evidence,
    verify_evidence_digest,
)


def _row() -> dict:
    """A representative passing conformance row binding immutable authority."""

    return {
        "schemaVersion": 1,
        "kind": "restricted-egress-launch-attestation",
        "conformanceRow": "generic_container_job",
        "profileRef": "moonmind-provider-egress@1",
        "profileDigest": "sha256:" + "a" * 64,
        "enforcerImplementation": "docker-internal-proxy/v1",
        "appliedRuleDigest": "sha256:" + "b" * 64,
        "networkRef": "moonmind_restricted-egress-network",
        "gatewayRef": "moonmind-sandbox-egress-proxy",
        "attachmentIdentity": "container:abc123",
        "gatewayImageDigest": "sha256:" + "c" * 64,
        "architecture": "amd64",
        "hostMode": "on_demand_docker",
        "validatedAt": "2026-08-07T00:00:00+00:00",
        "validationResult": "passed",
        "deniedConnectionCount": 3,
        "denialDiagnostics": ["denied example.com:443 TCP_DENIED/403"],
        "cleanupResult": "succeeded",
        "reconciliationResult": "succeeded",
    }


def test_attach_evidence_digest_is_stable_and_excludes_itself() -> None:
    row = _row()
    bound = attach_evidence_digest(row)
    assert bound[EGRESS_EVIDENCE_DIGEST_KEY].startswith("sha256:")
    # Rebinding an already-bound payload does not fold the digest into itself.
    assert attach_evidence_digest(bound) == bound
    assert bound[EGRESS_EVIDENCE_DIGEST_KEY] == evidence_content_digest(row)


def test_verify_evidence_digest_accepts_bound_body() -> None:
    bound = attach_evidence_digest(_row())
    verify_evidence_digest(bound)


def test_verify_evidence_digest_rejects_missing_digest() -> None:
    with pytest.raises(EgressEvidenceDigestError, match="missing"):
        verify_evidence_digest(_row())


def test_verify_evidence_digest_detects_tampered_body() -> None:
    bound = attach_evidence_digest(_row())
    bound["cleanupResult"] = "failed"
    with pytest.raises(EgressEvidenceDigestError, match="does not match"):
        verify_evidence_digest(bound)


def test_serialize_binds_digest_and_survives_json_roundtrip() -> None:
    data = serialize_conformance_evidence(_row(), location="egress-attestation")
    resolved = json.loads(data)
    assert resolved[EGRESS_EVIDENCE_DIGEST_KEY]
    # A resolver that reads the artifact back after cleanup re-verifies it.
    verified = parse_and_verify_conformance_evidence(
        data, location="egress-attestation"
    )
    assert verified["conformanceRow"] == "generic_container_job"


def test_parse_and_verify_rejects_post_cleanup_tampering() -> None:
    data = serialize_conformance_evidence(_row(), location="egress-lifecycle")
    resolved = json.loads(data)
    resolved["attachmentIdentity"] = "container:attacker"
    with pytest.raises(EgressEvidenceDigestError):
        parse_and_verify_conformance_evidence(
            json.dumps(resolved).encode(), location="egress-lifecycle"
        )


def test_serialize_fails_closed_on_secret_like_content() -> None:
    row = _row()
    row["denialDiagnostics"] = ["ghp_" + "a" * 36]
    with pytest.raises(EgressEvidenceSecretError, match="secret scan"):
        serialize_conformance_evidence(row, location="egress-attestation")


def test_secret_scan_blocks_bearer_and_credential_assignments() -> None:
    row = _row()
    row["note"] = "Authorization: Bearer sk-live-not-a-real-token-value-1234567890"
    with pytest.raises(EgressEvidenceSecretError):
        serialize_conformance_evidence(row, location="egress-lifecycle")


@pytest.mark.asyncio
async def test_publish_conformance_evidence_digest_binds_and_scans() -> None:
    published: dict[str, bytes] = {}

    async def publisher(_request, name, data):
        published[name] = data
        return f"artifact:{name}"

    ref = await publish_conformance_evidence(
        object(),
        "job-egress-attestation.json",
        _row(),
        publisher=publisher,
    )
    assert ref == "artifact:job-egress-attestation.json"
    # The published bytes are the exact digest-bound, secret-scanned evidence a
    # resolver later re-verifies once the live workload is gone.
    verified = parse_and_verify_conformance_evidence(
        published["job-egress-attestation.json"], location="publish"
    )
    assert verified["profileRef"] == "moonmind-provider-egress@1"


@pytest.mark.asyncio
async def test_publish_conformance_evidence_never_persists_secrets() -> None:
    published: dict[str, bytes] = {}

    async def publisher(_request, name, data):
        published[name] = data
        return f"artifact:{name}"

    row = _row()
    row["denialDiagnostics"] = ["password=hunter2-should-never-be-persisted"]
    with pytest.raises(EgressEvidenceSecretError):
        await publish_conformance_evidence(
            object(),
            "job-egress-attestation.json",
            row,
            publisher=publisher,
        )
    # Fail closed: nothing containing the secret reached the publisher.
    assert published == {}
