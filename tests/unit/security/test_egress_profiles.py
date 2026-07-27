from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.security.egress_profiles import (
    ATTESTATION_LABELS,
    ENFORCER_IMPLEMENTATION,
    EgressDestination,
    EgressProfile,
    attestation_from_network_labels,
)


def _profile(**overrides) -> EgressProfile:
    values = {
        "profileId": "test-provider",
        "version": 1,
        "owner": "security",
        "allowedDestinations": (
            EgressDestination(dnsName="api.example.com", ports=(443,)),
        ),
        "resolutionMode": "continuous",
        "dnsServers": ("1.1.1.1",),
        "ipv6Policy": "deny",
        "permittedWorkloadClasses": ("container-job",),
        "securityReviewRef": "github:MoonLadderStudios/MoonMind#3516",
        "validationState": "approved",
        "maxConnections": 10,
        "maxBytes": 1000,
        "idleSeconds": 30,
        "diagnosticsRetentionDays": 7,
    }
    values.update(overrides)
    return EgressProfile(**values)


def test_profile_digest_is_stable_and_profile_has_no_executable_authority() -> None:
    profile = _profile()
    assert profile.digest == _profile().digest
    with pytest.raises(ValidationError):
        _profile(firewallCommands=("iptables -F",))


@pytest.mark.parametrize(
    "destination",
    [
        {"cidr": "127.0.0.0/8", "ports": [443]},
        {"cidr": "169.254.169.254/32", "ports": [80]},
        {"cidr": "172.17.0.0/16", "ports": [443]},
        {"cidr": "::ffff:127.0.0.1/128", "ports": [443]},
    ],
)
def test_profile_rejects_local_metadata_docker_and_mapped_ipv6(destination) -> None:
    with pytest.raises(ValidationError, match="globally routable"):
        _profile(allowedDestinations=(destination,))


def test_attestation_requires_exact_current_profile_and_rule_digest() -> None:
    profile = _profile()
    key = b"k" * 32
    now = datetime.now(UTC).isoformat()
    labels = {
        ATTESTATION_LABELS["profile_ref"]: profile.ref,
        ATTESTATION_LABELS["profile_digest"]: profile.digest,
        ATTESTATION_LABELS["rules_digest"]: "sha256:" + "a" * 64,
        ATTESTATION_LABELS["enforcer"]: ENFORCER_IMPLEMENTATION,
        ATTESTATION_LABELS["validated"]: "true",
        ATTESTATION_LABELS["validated_at"]: now,
    }
    labels[ATTESTATION_LABELS["signature"]] = hmac.new(
        key,
        "\n".join(
            (
                profile.ref,
                profile.digest,
                labels[ATTESTATION_LABELS["rules_digest"]],
                ENFORCER_IMPLEMENTATION,
                now,
                "egress-1",
                "system",
            )
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    evidence = attestation_from_network_labels(
        profile=profile,
        network_ref="egress-1",
        backend_ref="system",
        labels=labels,
        attestation_key=key,
    )
    assert evidence.profile_digest == profile.digest
    assert evidence.applied_rule_digest == "sha256:" + "a" * 64

    labels[ATTESTATION_LABELS["profile_digest"]] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="current restricted-egress attestation"):
        attestation_from_network_labels(
            profile=profile,
            network_ref="egress-1",
            backend_ref="system",
            labels=labels,
            attestation_key=key,
        )


def test_attestation_rejects_self_declared_or_wrong_backend_labels() -> None:
    profile = _profile()
    labels = {
        ATTESTATION_LABELS["profile_ref"]: profile.ref,
        ATTESTATION_LABELS["profile_digest"]: profile.digest,
        ATTESTATION_LABELS["rules_digest"]: "sha256:" + "a" * 64,
        ATTESTATION_LABELS["enforcer"]: ENFORCER_IMPLEMENTATION,
        ATTESTATION_LABELS["validated"]: "true",
        ATTESTATION_LABELS["validated_at"]: datetime.now(UTC).isoformat(),
    }
    with pytest.raises(ValueError, match="authenticated"):
        attestation_from_network_labels(
            profile=profile,
            network_ref="egress-1",
            backend_ref="system",
            labels=labels,
            attestation_key=b"k" * 32,
        )
import hashlib
import hmac
