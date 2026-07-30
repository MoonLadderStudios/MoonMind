"""Egress profile schema tests (MoonLadderStudios/MoonMind#3516)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.omnigent.egress_profiles import (
    EGRESS_PROFILES,
    EgressLimits,
    EgressLogging,
    EgressProfile,
    get_egress_profile,
    is_implicitly_denied,
    public_egress_catalog,
)


def _limits() -> EgressLimits:
    return EgressLimits(
        maxConnections=10, maxRatePerMinute=60, maxBytes=1024, idleSeconds=30
    )


def _logging() -> EgressLogging:
    return EgressLogging(retentionDays=30, redaction="required")


def _profile(**overrides) -> EgressProfile:
    base = dict(
        profileId="egress-test",
        version=1,
        owner="tester",
        allowedDns=("example.com",),
        allowedPorts=(443,),
        dnsServers=("1.1.1.1",),
        limits=_limits(),
        logging=_logging(),
        validationState="validated",
        securityReviewRef="sec-review:test@1",
        workloadClasses=("container_job",),
    )
    base.update(overrides)
    return EgressProfile(**base)


def test_builtin_catalog_is_validated_and_credential_free() -> None:
    assert "egress-omnigent-baseline@1" in EGRESS_PROFILES
    assert "egress-deny-all@1" in EGRESS_PROFILES
    for profile in EGRESS_PROFILES.values():
        assert profile.validation_state == "validated"
        assert profile.security_review_ref
    dumped = str(public_egress_catalog()).lower()
    assert "password" not in dumped and "token" not in dumped


def test_profile_is_content_addressed_and_immutable() -> None:
    profile = _profile()
    assert profile.digest.startswith("sha256:")
    assert profile.digest == _profile().digest
    with pytest.raises(ValidationError):
        profile.allowed_ports = (80,)  # type: ignore[misc]


def test_version_bump_changes_digest() -> None:
    assert _profile(version=1).digest != _profile(version=2).digest


def test_validated_profile_requires_security_review() -> None:
    with pytest.raises(ValidationError, match="securityReviewRef"):
        _profile(securityReviewRef=None)


def test_rejects_executable_firewall_command_in_profile() -> None:
    with pytest.raises(ValidationError, match="executable firewall"):
        _profile(owner="tester; iptables -F")


def test_rejects_credential_material() -> None:
    with pytest.raises(ValidationError):
        EgressProfile(
            profileId="egress-bad",
            version=1,
            owner="tester",
            allowedDns=("example.com",),
            dnsServers=("1.1.1.1",),
            limits=_limits(),
            logging=_logging(),
            workloadClasses=("container_job",),
            # ``extra=forbid`` + secret-key scan both reject this.
            token="abc",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize("cidr", ["10.0.0.0/24", "169.254.169.254/32", "127.0.0.1/32"])
def test_allowlist_cannot_open_implicit_denied_ranges(cidr: str) -> None:
    with pytest.raises(ValidationError, match="implicitly denied"):
        _profile(allowedDns=(), allowedCidrs=(cidr,))


def test_narrow_internal_optin_requires_justification() -> None:
    with pytest.raises(ValidationError, match="internalRangeJustification"):
        _profile(
            allowedDns=(),
            allowedCidrs=("10.1.2.0/24",),
            allowInternalRanges=True,
        )
    # With a justification it is accepted.
    profile = _profile(
        allowedDns=(),
        allowedCidrs=("10.1.2.0/24",),
        allowInternalRanges=True,
        internalRangeJustification="isolated lab gateway",
    )
    assert profile.allow_internal_ranges is True


def test_ipv6_cidr_requires_allow_listed_policy() -> None:
    with pytest.raises(ValidationError, match="ipv6Policy"):
        _profile(allowedDns=(), allowedCidrs=("2001:db8::/48",))


def test_dns_names_require_explicit_resolver_when_profile_dns_only() -> None:
    with pytest.raises(ValidationError, match="dnsServers"):
        _profile(dnsServers=())


def test_invalid_dns_name_rejected() -> None:
    with pytest.raises(ValidationError, match="valid DNS name"):
        _profile(allowedDns=("not a host",))


@pytest.mark.parametrize(
    "address",
    ["169.254.169.254", "127.0.0.1", "10.0.0.1", "172.17.0.1", "::1", "::ffff:127.0.0.1"],
)
def test_is_implicitly_denied_covers_internal_ranges(address: str) -> None:
    assert is_implicitly_denied(address) is True


@pytest.mark.parametrize("address", ["203.0.113.5", "8.8.8.8"])
def test_public_addresses_not_implicitly_denied(address: str) -> None:
    assert is_implicitly_denied(address) is False


def test_get_profile_and_workload_class() -> None:
    baseline = get_egress_profile("egress-omnigent-baseline@1")
    assert baseline is not None
    assert baseline.permits_workload_class("omnigent_host")
    assert not baseline.permits_workload_class("unknown_class")
