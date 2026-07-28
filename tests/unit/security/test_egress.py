"""Restricted-egress policy and attestation coverage for #3516."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
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
    assert DEFAULT_EGRESS_PROFILE.digest == DEFAULT_EGRESS_PROFILE.digest
    assert "command" not in DEFAULT_EGRESS_PROFILE.model_fields
    assert "credential" not in DEFAULT_EGRESS_PROFILE.model_fields
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
        return 0, json.dumps(
            {
                "labels": {
                    "moonmind.egress.profile": DEFAULT_EGRESS_PROFILE.ref,
                    "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                },
                "networks": {
                    EGRESS_NETWORK_REF: {},
                    "moonmind_sandbox-egress-network": {},
                    "local-network": {},
                },
            }
        ).encode(), b""

    evidence = await attest_docker_egress(
        runner=runner,
        profile=DEFAULT_EGRESS_PROFILE,
        backend_ref="local",
    )

    assert evidence.validation_result == "passed"
    assert evidence.profile_digest == DEFAULT_EGRESS_PROFILE.digest
    assert evidence.applied_rule_digest.startswith("sha256:")
    assert calls[0][0:2] == ("network", "inspect")


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
        }
        return 0, json.dumps(payload).encode(), b""

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
