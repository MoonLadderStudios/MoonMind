"""Restricted-egress policy and attestation coverage for #3516."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_NETWORK_REF,
    EGRESS_PROFILE_SET_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_NETWORK_REF,
    attest_docker_egress,
    bounded_denial_diagnostics,
    omnigent_proxy_env,
    restricted_proxy_env,
)


def test_denial_diagnostics_are_bounded_scoped_and_strip_request_data():
    lines = [
        b"1 2 172.31.0.7 TCP_DENIED/403 0 CONNECT "
        b"metadata.invalid:443/path?token=secret - HIER_NONE/- text/html",
        b"1 2 172.31.0.8 TCP_DENIED/403 0 CONNECT "
        b"other.invalid:443/ - HIER_NONE/- text/html",
    ] * 30

    diagnostics = bounded_denial_diagnostics(
        b"\n".join(lines), client_address="172.31.0.7"
    )

    assert len(diagnostics) == 20
    assert diagnostics[0] == "denied metadata.invalid:443 TCP_DENIED/403"
    assert all(
        "secret" not in item and "172.31.0.8" not in item
        for item in diagnostics
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("CONNECT", "metadata.invalid:443"),
        ("HTTP", "example.invalid:8080"),
        ("HTTP_IPV6", "[2001:db8::1]:8443"),
    ],
)
def test_denial_diagnostics_normalize_authority_forms(target, expected):
    request_target = {
        "CONNECT": "user:secret@metadata.invalid:443/path?token=secret",
        "HTTP": "http://user:secret@example.invalid:8080/private?token=secret",
        "HTTP_IPV6": "https://user:secret@[2001:db8::1]:8443/private",
    }[target]
    line = (
        f"1 2 172.31.0.7 TCP_DENIED/403 0 GET {request_target} "
        "- HIER_NONE/- text/html"
    ).encode()

    assert bounded_denial_diagnostics(line, client_address="172.31.0.7") == (
        f"denied {expected} TCP_DENIED/403",
    )
    assert "secret" not in bounded_denial_diagnostics(
        line, client_address="172.31.0.7"
    )[0]


@pytest.mark.parametrize("target", ["-", "http://", "http://[bad", "http://:bad"])
def test_denial_diagnostics_drop_malformed_targets(target):
    line = (
        f"1 2 172.31.0.7 TCP_DENIED/403 0 GET {target} "
        "- HIER_NONE/- text/html"
    ).encode()
    assert bounded_denial_diagnostics(line, client_address="172.31.0.7") == ()


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
                        "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                        "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                        "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                    },
                    "networks": {
                        EGRESS_NETWORK_REF: {},
                        "moonmind_sandbox-egress-network": {},
                        OMNIGENT_EGRESS_NETWORK_REF: {},
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
            {"moonmind.egress.profile-set-digest": "sha256:stale"},
            {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                OMNIGENT_EGRESS_NETWORK_REF: {},
                "local-network": {},
            },
            "stale",
        ),
        (
            {"Internal": True, "EnableIPv6": False},
            {
                "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
            },
            {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                OMNIGENT_EGRESS_NETWORK_REF: {},
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
                        "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                        "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                        "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                    },
                    "networks": {
                        EGRESS_NETWORK_REF: {},
                        "moonmind_sandbox-egress-network": {},
                        OMNIGENT_EGRESS_NETWORK_REF: {},
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

    omnigent_values = omnigent_proxy_env()
    assert "HTTP_PROXY=http://omnigent-egress-proxy:3129" in omnigent_values
    assert "NO_PROXY=" in omnigent_values


@pytest.mark.asyncio
async def test_attestation_rejects_unapproved_profile_bounds_before_docker_calls():
    profile = _profile(idleSeconds=301)
    called = False

    async def runner(_args):
        nonlocal called
        called = True
        return 0, b"", b""

    with pytest.raises(RuntimeError, match="profile is not approved"):
        await attest_docker_egress(
            runner=runner,
            profile=profile,
            backend_ref="local",
        )

    assert called is False


def test_network_ref_resolves_configured_override(monkeypatch):
    """The documented compose override feeds the immutable profile/attestation.

    Setting ``MOONMIND_RESTRICTED_EGRESS_NETWORK`` must change the network the
    profile declares and the attestation inspects, instead of leaving a hard
    coded default the backend would fail closed against.
    """

    import importlib

    from moonmind.security import egress as egress_module

    original_profile_set_digest = egress_module.EGRESS_PROFILE_SET_DIGEST
    original_provider_digest = egress_module.DEFAULT_EGRESS_PROFILE.digest
    monkeypatch.setenv("MOONMIND_RESTRICTED_EGRESS_NETWORK", "custom_restricted_net")
    monkeypatch.setenv("MOONMIND_SANDBOX_EGRESS_NETWORK", "custom_sandbox_net")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EGRESS_NETWORK", "custom_omnigent_net")
    try:
        reloaded = importlib.reload(egress_module)
        assert reloaded.EGRESS_NETWORK_REF == "custom_restricted_net"
        assert reloaded.DEFAULT_EGRESS_PROFILE.network_ref == "custom_restricted_net"
        assert reloaded.OMNIGENT_EGRESS_PROFILE.network_ref == "custom_omnigent_net"
        assert reloaded.DEFAULT_EGRESS_PROFILE.digest != original_provider_digest
        assert reloaded.EGRESS_PROFILE_SET_DIGEST == original_profile_set_digest
        assert "custom_restricted_net" in reloaded._EXPECTED_GATEWAY_NETWORKS
        assert "custom_sandbox_net" in reloaded._EXPECTED_GATEWAY_NETWORKS
        assert "custom_omnigent_net" in reloaded._EXPECTED_GATEWAY_NETWORKS
    finally:
        # Restore module-level defaults so later tests see the shipped values.
        monkeypatch.undo()
        importlib.reload(egress_module)
