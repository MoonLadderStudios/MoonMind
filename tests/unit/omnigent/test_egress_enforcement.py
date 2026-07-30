"""Negative egress conformance + attestation suite (MoonMind#3516).

Proves that a compiled egress rule set allows approved destinations and denies
representative bypass/forbidden destinations at the network-layer model, and
that attestation is evidence-backed and fails closed.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.egress_enforcement import (
    ConnectionAttempt,
    EgressLaunchConstraintError,
    attest_enforced_networks,
    attest_profile,
    compile_ruleset,
    evaluate,
    run_conformance,
    validate_launch_constraints,
)
from moonmind.omnigent.egress_profiles import (
    EgressLimits,
    EgressLogging,
    EgressProfile,
    get_egress_profile,
)
from moonmind.omnigent.execution_profiles import POLICIES

BASELINE = get_egress_profile("egress-omnigent-baseline@1")


def _resolver(host: str):
    # api.openai.com is the canonical allowed host for these tests.
    return {"api.openai.com": ["203.0.113.10"]}.get(host.lower().rstrip("."), ["203.0.113.250"])


def test_compiled_ruleset_is_default_deny_and_digested() -> None:
    ruleset = compile_ruleset(BASELINE)
    assert ruleset.default_action == "deny"
    assert ruleset.applied_rule_digest.startswith("sha256:")
    # Implicit internal-range denials precede any allow rule.
    first_allow = next(i for i, r in enumerate(ruleset.rules) if r.action == "allow")
    assert all(r.action == "deny" for r in ruleset.rules[:first_allow])


def test_allowed_host_over_tls_is_permitted() -> None:
    ruleset = compile_ruleset(BASELINE)
    decision = evaluate(
        ruleset,
        BASELINE,
        ConnectionAttempt(dest_ip="203.0.113.10", port=443, host="api.openai.com"),
        resolver=_resolver,
    )
    assert decision.allowed is True


@pytest.mark.parametrize(
    ("label", "attempt", "reason"),
    [
        (
            "metadata",
            ConnectionAttempt(dest_ip="169.254.169.254", port=443, host=None),
            "metadata-endpoint",
        ),
        (
            "ipv4-mapped-metadata",
            ConnectionAttempt(dest_ip="::ffff:169.254.169.254", port=443, host=None),
            "metadata-endpoint",
        ),
        (
            "loopback",
            ConnectionAttempt(dest_ip="127.0.0.1", port=443, host=None),
            "forbidden-internal-range",
        ),
        (
            "docker-gateway",
            ConnectionAttempt(dest_ip="172.17.0.1", port=443, host=None),
            "forbidden-internal-range",
        ),
        (
            "direct-ip",
            ConnectionAttempt(dest_ip="198.51.100.7", port=443, host=None),
            "direct-ip-not-allowed",
        ),
        (
            "bad-port",
            ConnectionAttempt(dest_ip="203.0.113.10", port=22, host="api.openai.com"),
            "port-not-allowed",
        ),
        (
            "ipv6-denied",
            ConnectionAttempt(dest_ip="2606:4700::1111", port=443, host=None),
            "ipv6-denied",
        ),
    ],
)
def test_bypass_destinations_are_denied(label, attempt, reason) -> None:
    ruleset = compile_ruleset(BASELINE)
    decision = evaluate(ruleset, BASELINE, attempt, resolver=_resolver)
    assert decision.allowed is False
    assert decision.reason == reason


def test_dns_rebinding_is_denied() -> None:
    ruleset = compile_ruleset(BASELINE)
    # Allowed host name, but the connection targets an attacker-controlled IP
    # that the resolver never returns for that name.
    decision = evaluate(
        ruleset,
        BASELINE,
        ConnectionAttempt(dest_ip="198.51.100.9", port=443, host="api.openai.com"),
        resolver=_resolver,
    )
    assert decision.allowed is False
    assert decision.reason == "dns-rebinding"


def test_redirect_to_unapproved_host_is_denied() -> None:
    ruleset = compile_ruleset(BASELINE)
    decision = evaluate(
        ruleset,
        BASELINE,
        ConnectionAttempt(
            dest_ip="198.51.100.11",
            port=443,
            host="evil.example.com",
            redirect_from="api.openai.com",
        ),
        resolver=_resolver,
    )
    assert decision.allowed is False


def test_unapproved_proxy_is_denied() -> None:
    ruleset = compile_ruleset(BASELINE)
    decision = evaluate(
        ruleset,
        BASELINE,
        ConnectionAttempt(
            dest_ip="203.0.113.10",
            port=443,
            host="api.openai.com",
            proxy_endpoint="attacker.example.com:8080",
        ),
        resolver=_resolver,
    )
    assert decision.allowed is False
    assert decision.reason == "proxy-not-approved"


def test_conformance_probe_passes_for_validated_profile() -> None:
    report = run_conformance(BASELINE)
    assert report.passed is True, report.failures
    assert report.total > 0
    # Denied counters are bounded diagnostics, no payloads.
    assert all(isinstance(v, int) for v in report.denied_counters.values())


def test_deny_all_profile_permits_nothing() -> None:
    deny_all = get_egress_profile("egress-deny-all@1")
    ruleset = compile_ruleset(deny_all)
    decision = evaluate(
        ruleset,
        deny_all,
        ConnectionAttempt(dest_ip="203.0.113.10", port=443, host="api.openai.com"),
    )
    assert decision.allowed is False


# --- launch-constraint guard (AC4) ----------------------------------------
@pytest.mark.parametrize(
    "spec",
    [
        {"network_mode": "host"},
        {"network_mode": "bridge"},
        {"privileged": True},
        {"cap_add": ["NET_ADMIN"]},
        {"cap_add": ["NET_RAW"]},
        {"networks": ["local-network", "rogue-net"]},
        {"extra_hosts": ["api.openai.com:10.0.0.5"]},
        {"routes": [{"dst": "0.0.0.0/0"}]},
        {"sysctls": {"net.ipv4.conf.all.forwarding": "1"}},
        {"devices": ["/dev/net/tun"]},
    ],
)
def test_launch_constraints_reject_bypass_authority(spec) -> None:
    with pytest.raises(EgressLaunchConstraintError):
        validate_launch_constraints(spec, approved_network="local-network")


def test_launch_constraints_accept_approved_spec() -> None:
    validate_launch_constraints(
        {"networks": ["local-network"], "cap_add": []},
        approved_network="local-network",
    )


# --- attestation / evidence (AC5, AC6, AC7) --------------------------------
def test_attestation_is_evidence_backed_and_attested() -> None:
    attestation = attest_profile(
        BASELINE,
        network_ref="local-network",
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
    )
    assert attestation.attested is True
    evidence = attestation.to_evidence()
    assert evidence["profileDigest"] == BASELINE.digest
    assert evidence["appliedRuleDigest"].startswith("sha256:")
    assert evidence["validationResult"] == "attested"
    assert evidence["networkRef"] == "local-network"
    assert evidence["securityReviewRef"] == BASELINE.security_review_ref
    # No credential/payload leakage in the evidence record.
    assert "password" not in str(evidence).lower()


def test_attestation_fails_closed_for_missing_profile() -> None:
    attestation = attest_profile(
        None,
        network_ref="local-network",
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
    )
    assert attestation.attested is False
    assert "profile-missing" in attestation.reasons


def test_attestation_fails_closed_for_non_enforcing_backend() -> None:
    attestation = attest_profile(
        BASELINE,
        network_ref="local-network",
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
        backend_enforcing=False,
    )
    assert attestation.attested is False
    assert "backend-non-enforcing" in attestation.reasons


def test_attestation_fails_closed_for_unvalidated_profile() -> None:
    draft = EgressProfile(
        profileId="egress-draft",
        version=1,
        owner="tester",
        allowedDns=("example.com",),
        dnsServers=("1.1.1.1",),
        limits=EgressLimits(
            maxConnections=1, maxRatePerMinute=1, maxBytes=1, idleSeconds=1
        ),
        logging=EgressLogging(retentionDays=1, redaction="required"),
        validationState="draft",
        workloadClasses=("container_job",),
    )
    attestation = attest_profile(
        draft,
        network_ref="local-network",
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
    )
    assert attestation.attested is False
    assert any(r.startswith("profile-not-validated") for r in attestation.reasons)


@pytest.mark.asyncio
async def test_attest_enforced_networks_reports_only_attested_refs() -> None:
    async def network_ready(ref: str) -> bool:
        return True

    attestations = await attest_enforced_networks(
        POLICIES.values(),
        network_ready=network_ready,
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
    )
    assert attestations
    assert all(a.attested for a in attestations)
    assert {a.network_ref for a in attestations} == {"local-network"}


@pytest.mark.asyncio
async def test_attest_enforced_networks_fails_closed_when_network_down() -> None:
    async def network_ready(ref: str) -> bool:
        return False

    attestations = await attest_enforced_networks(
        POLICIES.values(),
        network_ready=network_ready,
        backend_ref="docker-local",
        now="2026-07-30T00:00:00+00:00",
    )
    assert attestations
    assert all(not a.attested for a in attestations)
    assert all("network-not-ready" in a.reasons for a in attestations)
