"""Acceptance-gate evidence tests for MoonLadderStudios/MoonMind#3425."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.embedded_evidence import (
    EMBEDDED_EVIDENCE_SCHEMA_VERSION,
    EMBEDDED_PROTOCOL_PROFILE,
    EmbeddedEvidenceError,
    validate_embedded_evidence,
)
from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT


NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
SHA = "a" * 64


def _claim(**overrides):
    claim = {
        "schemaVersion": EMBEDDED_EVIDENCE_SCHEMA_VERSION,
        "claimType": "live_smoke",
        "status": "passed",
        "moonmindBuildIdentity": "build-3425",
        "bridgeConfigSha256": SHA,
        "omnigentSourceCommit": PINNED_OMNIGENT_COMMIT,
        "protocolProfile": EMBEDDED_PROTOCOL_PROFILE,
        "images": {
            "server": f"ghcr.io/omnigent/server@sha256:{'1' * 64}",
            "host": f"ghcr.io/omnigent/host@sha256:{'2' * 64}",
        },
        "testMatrix": {
            "stock-host-codex": {
                "status": "passed",
                "evidenceRefs": ["artifact-id"],
            }
        },
        "generatedAt": NOW.isoformat(),
        "expiresAt": (NOW + timedelta(days=1)).isoformat(),
        "supersededBy": None,
        "revokedAt": None,
        "secretScan": "passed",
        "cleanup": "passed",
        "producer": "workflow:omnigent-embedded-conformance",
    }
    claim.update(overrides)
    return claim


def test_accepts_current_passing_policy_bound_claim() -> None:
    result = validate_embedded_evidence(
        _claim(),
        expected_claim_type="live_smoke",
        moonmind_build_identity="build-3425",
        bridge_config_sha256=SHA,
        now=NOW,
    )

    assert result.status == "passed"


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"status": "failed"}, "malformed"),
        ({"revokedAt": NOW.isoformat()}, "revoked"),
        ({"supersededBy": "new-artifact"}, "superseded"),
        ({"secretScan": "failed"}, "malformed"),
        ({"cleanup": "failed"}, "malformed"),
        ({"images": {}}, "images"),
    ],
)
def test_rejects_failed_revoked_or_incomplete_claims(override, match) -> None:
    with pytest.raises(EmbeddedEvidenceError, match=match):
        validate_embedded_evidence(
            _claim(**override),
            expected_claim_type="live_smoke",
            moonmind_build_identity="build-3425",
            bridge_config_sha256=SHA,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"expected_claim_type": "proxy_conformance"}, "claim type"),
        ({"moonmind_build_identity": "other-build"}, "different MoonMind"),
        ({"bridge_config_sha256": "b" * 64}, "different bridge"),
        ({"now": NOW + timedelta(days=2)}, "expired"),
    ],
)
def test_rejects_stale_or_incompatible_policy(kwargs, match) -> None:
    arguments = {
        "expected_claim_type": "live_smoke",
        "moonmind_build_identity": "build-3425",
        "bridge_config_sha256": SHA,
        "now": NOW,
    }
    arguments.update(kwargs)
    with pytest.raises(EmbeddedEvidenceError, match=match):
        validate_embedded_evidence(_claim(), **arguments)


def test_rejects_not_yet_valid_claim() -> None:
    with pytest.raises(EmbeddedEvidenceError, match="not yet valid"):
        validate_embedded_evidence(
            _claim(
                generatedAt=(NOW + timedelta(minutes=1)).isoformat(),
                expiresAt=(NOW + timedelta(days=1)).isoformat(),
            ),
            expected_claim_type="live_smoke",
            moonmind_build_identity="build-3425",
            bridge_config_sha256=SHA,
            now=NOW,
        )
