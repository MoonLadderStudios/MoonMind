"""Restricted-egress policy and attestation coverage for #3516."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_NETWORK_REF,
    ENFORCER_IMPLEMENTATION,
    attest_docker_egress,
    restricted_proxy_env,
)


def _profile(**updates):
    payload = DEFAULT_EGRESS_PROFILE.model_dump(by_alias=True, mode="json")
    payload.update(updates)
    return type(DEFAULT_EGRESS_PROFILE).model_validate(payload)


@pytest.mark.parametrize(
    "cidr",
    [
        "127.0.0.1/32",
        "169.254.169.254/32",
        "172.17.0.0/16",
        "192.168.1.0/24",
        "::1/128",
        "fc00::/7",
        "::ffff:127.0.0.1/128",
    ],
)
def test_profile_rejects_local_metadata_docker_and_mapped_ranges(cidr):
    with pytest.raises(ValidationError, match="prohibited address range"):
        _profile(destinations=[{"cidr": cidr, "ports": [443]}])


@pytest.mark.parametrize(
    "name", ["localhost", "service.internal", "*.openai.com", "10.0.0.1"]
)
def test_profile_rejects_internal_wildcard_and_direct_ip_names(name):
    with pytest.raises(ValidationError):
        _profile(destinations=[{"dnsName": name, "ports": [443]}])


def test_profile_is_immutable_digest_stable_and_has_no_execution_fields():
    # Rebuild the profile from its own serialized content and confirm the digest
    # is content-stable. Comparing the property to itself would be a tautology
    # (identical operands) and would not prove stability across construction.
    rebuilt = _profile()
    assert rebuilt.digest == DEFAULT_EGRESS_PROFILE.digest
    assert "command" not in type(DEFAULT_EGRESS_PROFILE).model_fields
    assert "credential" not in type(DEFAULT_EGRESS_PROFILE).model_fields
    with pytest.raises(ValidationError):
        _profile(firewallCommands=["iptables -F"])


@pytest.mark.asyncio
async def test_attestation_proves_internal_ipv4_network_and_exact_gateway():
    calls = []

    async def runner(args):
        calls.append(tuple(args))
        if args[0] == "network":
            return 0, json.dumps(
                {"Internal": True, "EnableIPv6": False}
            ).encode(), b""
        if args[0] == "inspect":
            return 0, json.dumps(
                {
                    "labels": {
                        "moonmind.egress.profile": DEFAULT_EGRESS_PROFILE.ref,
                        "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                        "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                    },
                    "networks": {
                        EGRESS_NETWORK_REF: {},
                        "moonmind_sandbox-egress-network": {},
                        "local-network": {},
                    },
                    "image": "sha256:gateway-image",
                    "health": "healthy",
                }
            ).encode(), b""
        return 0, (
            f"{EGRESS_CONFIG_DIGEST.removeprefix('sha256:')}  "
            "/etc/squid/squid.conf\n"
        ).encode(), b""

    evidence = await attest_docker_egress(
        runner=runner,
        profile=DEFAULT_EGRESS_PROFILE,
        backend_ref="local",
    )

    assert evidence.validation_result == "passed"
    assert evidence.profile_digest == DEFAULT_EGRESS_PROFILE.digest
    assert evidence.applied_rule_digest.startswith("sha256:")
    assert evidence.config_digest == EGRESS_CONFIG_DIGEST
    assert evidence.gateway_image_digest == "sha256:gateway-image"
    assert evidence.health_result == "healthy"
    assert calls[0][0:2] == ("network", "inspect")
    assert calls[-1][0:2] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("network", "labels", "networks", "message"),
    [
        (
            {"Internal": False, "EnableIPv6": False},
            {},
            {},
            "not internal",
        ),
        (
            {"Internal": True, "EnableIPv6": True},
            {},
            {},
            "not internal",
        ),
        (
            {"Internal": True, "EnableIPv6": False},
            {"moonmind.egress.profile": "stale@1"},
            {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                "local-network": {},
            },
            "stale",
        ),
        (
            {"Internal": True, "EnableIPv6": False},
            {
                "moonmind.egress.profile": DEFAULT_EGRESS_PROFILE.ref,
                "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
            },
            {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                "local-network": {},
                "bypass": {},
            },
            "attachment",
        ),
    ],
)
async def test_attestation_fails_closed_on_unproven_or_stale_state(
    network, labels, networks, message
):
    async def runner(args):
        payload = network if args[0] == "network" else {
            "labels": labels,
            "networks": networks,
            "image": "sha256:gateway-image",
            "health": "healthy",
        }
        return 0, json.dumps(payload).encode(), b""

    with pytest.raises(RuntimeError, match=message):
        await attest_docker_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="local",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_digest", "health", "message"),
    [
        ("sha256:" + "0" * 64, "healthy", "config"),
        (EGRESS_CONFIG_DIGEST, "unhealthy", "healthy"),
    ],
)
async def test_attestation_rejects_unobserved_rules_or_unhealthy_gateway(
    config_digest, health, message
):
    async def runner(args):
        if args[0] == "network":
            return 0, json.dumps(
                {"Internal": True, "EnableIPv6": False}
            ).encode(), b""
        if args[0] == "inspect":
            return 0, json.dumps(
                {
                    "labels": {
                        "moonmind.egress.profile": DEFAULT_EGRESS_PROFILE.ref,
                        "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                        "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                    },
                    "networks": {
                        EGRESS_NETWORK_REF: {},
                        "moonmind_sandbox-egress-network": {},
                        "local-network": {},
                    },
                    "image": "sha256:gateway-image",
                    "health": health,
                }
            ).encode(), b""
        return 0, (
            f"{config_digest.removeprefix('sha256:')}  "
            "/etc/squid/squid.conf\n"
        ).encode(), b""

    with pytest.raises(RuntimeError, match=message):
        await attest_docker_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="local",
        )


def test_proxy_environment_clears_bypass_variables():
    values = restricted_proxy_env()
    assert "NO_PROXY=" in values
    assert "no_proxy=" in values
    assert all("169.254.169.254" not in value for value in values)


def test_network_ref_resolves_configured_override(monkeypatch):
    """The documented compose override feeds the immutable profile/attestation.

    Setting ``MOONMIND_RESTRICTED_EGRESS_NETWORK`` must change the network the
    profile declares and the attestation inspects, instead of leaving a hard
    coded default the backend would fail closed against.
    """

    import importlib

    from moonmind.security import egress as egress_module

    monkeypatch.setenv("MOONMIND_RESTRICTED_EGRESS_NETWORK", "custom_restricted_net")
    monkeypatch.setenv("MOONMIND_SANDBOX_EGRESS_NETWORK", "custom_sandbox_net")
    try:
        reloaded = importlib.reload(egress_module)
        assert reloaded.EGRESS_NETWORK_REF == "custom_restricted_net"
        assert reloaded.DEFAULT_EGRESS_PROFILE.network_ref == "custom_restricted_net"
        assert "custom_restricted_net" in reloaded._EXPECTED_GATEWAY_NETWORKS
        assert "custom_sandbox_net" in reloaded._EXPECTED_GATEWAY_NETWORKS
    finally:
        # Restore module-level defaults so later tests see the shipped values.
        monkeypatch.undo()
        importlib.reload(egress_module)
