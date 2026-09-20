"""Restricted-egress policy and attestation coverage for #3516."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.security.egress import (
    _EXPECTED_GATEWAY_NETWORKS,
    CONTROL_PLANE_NETWORK_REF,
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_FILE_DIGESTS,
    EGRESS_NETWORK_REF,
    EGRESS_PROFILE_SET_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_NETWORK_REF,
    EgressAttestation,
    attest_docker_egress,
    attest_docker_workload_egress,
    bounded_denial_diagnostics,
    denied_connection_count,
    omnigent_proxy_env,
    restricted_proxy_env,
)


def test_openrouter_destination_is_scoped_to_omnigent(configured_egress):
    module = configured_egress("openrouter.ai\n")

    assert any(
        destination.dns_name == "openrouter.ai" and destination.ports == (443,)
        for destination in module.OMNIGENT_EGRESS_PROFILE.destinations
    )
    assert all(
        destination.dns_name != "openrouter.ai"
        for destination in module.DEFAULT_EGRESS_PROFILE.destinations
    )


@pytest.mark.asyncio
async def test_default_candidate_qualifies_against_v1_gateway():
    from moonmind.security import egress

    for profile in (egress.DEFAULT_EGRESS_PROFILE, egress.OMNIGENT_EGRESS_PROFILE):
        evidence = await egress.attest_docker_egress(
            runner=_legacy_gateway_runner(), profile=profile, backend_ref="candidate"
        )
        assert evidence.validation_result == "passed"
        assert evidence.enforcer_implementation == "docker-internal-proxy/v1"
        assert evidence.config_digest == _LEGACY_CONFIG_DIGEST


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "api",
        "provider.local",
        "provider.internal",
        "provider.test",
        "provider.invalid",
        "provider.localhost",
        "127.0.0.1",
        "8.8.8.8",
        "::1",
        "*.provider.com",
        ".provider.com",
        "https://provider.com",
        "provider.com:443",
        "user@provider.com",
        "provider.com/path",
        "provider.com other.com",
        "-bad.com",
        "bad-.com",
        "bad..com",
        "provider.com.",
        "provider.123",
        "provider.com\nhttp_access allow all",
        "a" * 64 + ".com",
        "provider.com\r",
        "provider.com\vother.com",
        "provider.com\fother.com",
    ],
)
def test_deployment_provider_file_rejects_non_exact_public_dns(tmp_path, value):
    from moonmind.security import egress

    policy = tmp_path / "omnigent-provider-domains.txt"
    policy.write_text(value + "\n")
    with pytest.raises(ValueError, match="public DNS"):
        egress.load_omnigent_provider_destinations(policy)


def test_deployment_provider_file_is_the_only_extra_destination_source(tmp_path):
    from moonmind.security import egress

    policy = tmp_path / "omnigent-provider-domains.txt"
    policy.write_text("openrouter.ai\napi.future-provider.com\n")
    destinations = egress.load_omnigent_provider_destinations(policy)
    assert [(d.dns_name, d.ports) for d in destinations] == [
        ("openrouter.ai", (443,)),
        ("api.future-provider.com", (443,)),
    ]
    policy.write_text("")
    assert egress.load_omnigent_provider_destinations(policy) == ()
    policy.unlink()
    assert egress.load_omnigent_provider_destinations(policy) == ()


def _attestation() -> EgressAttestation:
    return EgressAttestation(
        profileRef=DEFAULT_EGRESS_PROFILE.ref,
        profileDigest=DEFAULT_EGRESS_PROFILE.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef="test",
        networkRef=DEFAULT_EGRESS_PROFILE.network_ref,
        gatewayRef=DEFAULT_EGRESS_PROFILE.gateway_ref,
        appliedRuleDigest="sha256:" + "a" * 64,
        configDigest=EGRESS_CONFIG_DIGEST,
        gatewayImageDigest="sha256:" + "b" * 64,
        healthResult="healthy",
        validatedAt=datetime(2026, 8, 12, tzinfo=UTC),
        validationResult="passed",
    )


def test_denial_diagnostics_are_bounded_scoped_and_strip_request_data():
    lines = [
        # Explicit ``+`` keeps each list element a single squid log line while
        # avoiding implicit string concatenation that reads like a missing comma.
        b"1 2 172.31.0.7 TCP_DENIED/403 0 CONNECT "
        + b"metadata.invalid:443/path?token=secret - HIER_NONE/- text/html",
        b"1 2 172.31.0.8 TCP_DENIED/403 0 CONNECT "
        + b"other.invalid:443/ - HIER_NONE/- text/html",
    ] * 30

    diagnostics = bounded_denial_diagnostics(
        b"\n".join(lines), client_address="172.31.0.7"
    )

    assert len(diagnostics) == 20
    assert diagnostics[0] == "denied metadata.invalid:443 TCP_DENIED/403"
    assert all(
        "secret" not in item and "172.31.0.8" not in item for item in diagnostics
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
    assert (
        "secret" not in bounded_denial_diagnostics(line, client_address="172.31.0.7")[0]
    )


def test_denial_diagnostics_scope_to_container_lifetime():
    start = datetime(2026, 8, 4, 0, 0, 30, tzinfo=UTC)
    finish = datetime(2026, 8, 4, 0, 5, 0, tzinfo=UTC)
    prior = start.timestamp() - 300
    inside = start.timestamp() + 10
    after = finish.timestamp() + 300
    log = (
        f"{prior} 2 172.31.0.7 TCP_DENIED/403 0 CONNECT prior.invalid:443/ "
        "- HIER_NONE/- text/html\n"
        f"{inside} 2 172.31.0.7 TCP_DENIED/403 0 CONNECT current.invalid:443/ "
        "- HIER_NONE/- text/html\n"
        f"{after} 2 172.31.0.7 TCP_DENIED/403 0 CONNECT later.invalid:443/ "
        "- HIER_NONE/- text/html\n"
    ).encode()

    diagnostics = bounded_denial_diagnostics(
        log, client_address="172.31.0.7", started_at=start, finished_at=finish
    )

    # Only the denial inside the container lifetime is attributed to this launch.
    assert diagnostics == ("denied current.invalid:443 TCP_DENIED/403",)


def test_denied_connection_count_ignores_diagnostic_cap():
    lines = [
        f"1 2 172.31.0.7 TCP_DENIED/403 0 CONNECT blocked{index}.invalid:443/ "
        "- HIER_NONE/- text/html"
        for index in range(30)
    ]
    log = ("\n".join(lines) + "\n").encode()

    diagnostics = bounded_denial_diagnostics(log, client_address="172.31.0.7")
    count = denied_connection_count(log, client_address="172.31.0.7")

    # The retained sample stays capped while the counter reflects every denial.
    assert len(diagnostics) == 20
    assert count == 30


def test_denied_connection_count_excludes_prior_ip_holder():
    start = datetime(2026, 8, 4, 0, 0, 30, tzinfo=UTC)
    prior = start.timestamp() - 300
    inside = start.timestamp() + 10
    log = (
        f"{prior} 2 172.31.0.7 TCP_DENIED/403 0 CONNECT prior.invalid:443/ "
        "- HIER_NONE/- text/html\n"
        f"{inside} 2 172.31.0.7 TCP_DENIED/403 0 CONNECT current.invalid:443/ "
        "- HIER_NONE/- text/html\n"
    ).encode()

    assert (
        denied_connection_count(log, client_address="172.31.0.7", started_at=start) == 1
    )


@pytest.mark.parametrize("target", ["-", "http://", "http://[bad", "http://:bad"])
def test_denial_diagnostics_drop_malformed_targets(target):
    line = (
        f"1 2 172.31.0.7 TCP_DENIED/403 0 GET {target} " "- HIER_NONE/- text/html"
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
            return 0, json.dumps({"Internal": True, "EnableIPv6": False}).encode(), b""
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
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
                            CONTROL_PLANE_NETWORK_REF: {},
                        },
                        "image": "sha256:gateway-image",
                        "health": "healthy",
                    }
                ).encode(),
                b"",
            )
        return (
            0,
            "".join(
                f"{EGRESS_FILE_DIGESTS[path.rsplit('/', 1)[-1]].removeprefix('sha256:')}  {path}\n"
                for path in args[3:]
            ).encode(),
            b"",
        )

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
async def test_workload_attestation_binds_exact_sole_attachment_image_and_denials():
    attestation = _attestation()
    client_address = "172.31.0.7"
    denial_time = datetime(2026, 8, 12, tzinfo=UTC).timestamp()

    async def runner(args):
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
                    {
                        "labels": {
                            "moonmind.egress.profile": attestation.profile_ref,
                            "moonmind.egress.profile_digest": attestation.profile_digest,
                            "moonmind.egress.applied_rule_digest": (
                                attestation.applied_rule_digest
                            ),
                        },
                        "networks": {
                            DEFAULT_EGRESS_PROFILE.network_ref: {
                                "NetworkID": "network-id",
                                "EndpointID": "endpoint-id",
                                "IPAddress": client_address,
                            }
                        },
                        "imageRef": "image@sha256:" + "b" * 64,
                        "image": "sha256:" + "c" * 64,
                    }
                ).encode(),
                b"",
            )
        if args[0:2] == ("image", "inspect"):
            return 0, b'"amd64"', b""
        assert args[0:2] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref)
        return (
            0,
            (
                f"{denial_time} 2 {client_address} TCP_DENIED/403 0 CONNECT "
                "metadata.invalid:443/ - HIER_NONE/- text/html\n"
            ).encode(),
            b"",
        )

    evidence = await attest_docker_workload_egress(
        runner=runner,
        profile=DEFAULT_EGRESS_PROFILE,
        attestation=attestation,
        attachment_identity="container-id",
        expected_image_ref="image@sha256:" + "b" * 64,
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        finished_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert evidence["attachmentIdentity"] == "container-id"
    assert evidence["networkIdentity"] == "network-id"
    assert evidence["endpointIdentity"] == "endpoint-id"
    assert evidence["workloadImageDigest"] == "sha256:" + "c" * 64
    assert evidence["workloadImageRef"] == "image@sha256:" + "b" * 64
    assert evidence["architecture"] == "amd64"
    assert evidence["deniedConnectionCount"] == 1
    assert evidence["denialDiagnostics"] == [
        "denied metadata.invalid:443 TCP_DENIED/403"
    ]
    assert client_address not in json.dumps(evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("secondary_network", "sole approved network"),
        ("stale_label", "labels are unattested"),
        ("mutable_image", "image is unattested"),
        ("image_ref_mismatch", "does not match launch authority"),
    ],
)
async def test_workload_attestation_rejects_bypass_or_unbound_authority(
    mutation, message
):
    attestation = _attestation()
    labels = {
        "moonmind.egress.profile": attestation.profile_ref,
        "moonmind.egress.profile_digest": attestation.profile_digest,
        "moonmind.egress.applied_rule_digest": attestation.applied_rule_digest,
    }
    networks = {
        DEFAULT_EGRESS_PROFILE.network_ref: {
            "NetworkID": "network-id",
            "EndpointID": "endpoint-id",
            "IPAddress": "172.31.0.7",
        }
    }
    image = "sha256:" + "c" * 64
    image_ref = "image@sha256:" + "b" * 64
    if mutation == "secondary_network":
        networks["bridge"] = {"IPAddress": "172.17.0.2"}
    elif mutation == "stale_label":
        labels["moonmind.egress.applied_rule_digest"] = "sha256:stale"
    elif mutation == "mutable_image":
        image = "mutable:latest"
    else:
        image_ref = "image@sha256:" + "0" * 64

    async def runner(args):
        assert args[0] == "inspect"
        return (
            0,
            json.dumps(
                {
                    "labels": labels,
                    "networks": networks,
                    "imageRef": image_ref,
                    "image": image,
                }
            ).encode(),
            b"",
        )

    with pytest.raises(RuntimeError, match=message):
        await attest_docker_workload_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            attestation=attestation,
            attachment_identity="container-id",
            expected_image_ref="image@sha256:" + "b" * 64,
        )


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
            {"moonmind.egress.enforcer": "docker-internal-proxy/v1"},
            {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                OMNIGENT_EGRESS_NETWORK_REF: {},
                CONTROL_PLANE_NETWORK_REF: {},
            },
            "implementation is unattested",
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
                CONTROL_PLANE_NETWORK_REF: {},
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
        payload = (
            network
            if args[0] == "network"
            else {
                "labels": labels,
                "networks": networks,
                "image": "sha256:gateway-image",
                "health": "healthy",
            }
        )
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
            return 0, json.dumps({"Internal": True, "EnableIPv6": False}).encode(), b""
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
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
                            CONTROL_PLANE_NETWORK_REF: {},
                        },
                        "image": "sha256:gateway-image",
                        "health": health,
                    }
                ).encode(),
                b"",
            )
        return (
            0,
            (
                f"{config_digest.removeprefix('sha256:')}  " "/etc/squid/squid.conf\n"
            ).encode(),
            b"",
        )

    with pytest.raises(RuntimeError, match=message):
        await attest_docker_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="local",
        )


@pytest.mark.asyncio
async def test_attestation_rejects_main_config_only_evidence():
    async def runner(args):
        if args[0] == "network":
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
                    {
                        "labels": {
                            "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                            "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                            "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                        },
                        "networks": {
                            EGRESS_NETWORK_REF: {},
                            "moonmind_sandbox-egress-network": {},
                            OMNIGENT_EGRESS_NETWORK_REF: {},
                            CONTROL_PLANE_NETWORK_REF: {},
                        },
                        "image": "sha256:gateway-image",
                        "health": "healthy",
                    }
                ).encode(),
                b"",
            )
        return (
            0,
            (
                EGRESS_CONFIG_DIGEST.removeprefix("sha256:")
                + "  /etc/squid/squid.conf\n"
            ).encode(),
            b"",
        )

    with pytest.raises(RuntimeError, match="config"):
        await attest_docker_egress(
            runner=runner, profile=DEFAULT_EGRESS_PROFILE, backend_ref="test"
        )


@pytest.fixture
def configured_egress(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    from moonmind.security import egress

    policy = tmp_path / "deployment-policy"
    policy.mkdir()
    monkeypatch.setenv("MOONMIND_EGRESS_POLICY_DIRECTORY", str(policy))

    def load(value=None):
        path = policy / "omnigent-provider-domains.txt"
        if value is not None:
            path.write_text(value)
        spec = importlib.util.spec_from_file_location(
            "configured_egress", Path(egress.__file__)
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        return module

    return load


def test_absent_provider_file_preserves_installed_destinations(configured_egress):
    import hashlib

    module = configured_egress()
    assert (
        module.OMNIGENT_EGRESS_PROFILE.destinations
        == module.DEFAULT_EGRESS_PROFILE.destinations
    )
    assert module.OMNIGENT_EGRESS_PROFILE.ref != module.DEFAULT_EGRESS_PROFILE.ref
    assert module.EGRESS_FILE_DIGESTS["omnigent-provider-domains.txt"] == (
        "sha256:" + hashlib.sha256(b"").hexdigest()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "api.openai.com\n"])
async def test_candidate_qualifies_against_unchanged_v1_gateway(
    configured_egress, value
):
    module = configured_egress(value)
    for profile in (module.DEFAULT_EGRESS_PROFILE, module.OMNIGENT_EGRESS_PROFILE):
        evidence = await module.attest_docker_egress(
            runner=_legacy_gateway_runner(), profile=profile, backend_ref="candidate"
        )
        assert evidence.validation_result == "passed"
        assert evidence.enforcer_implementation == "docker-internal-proxy/v1"
        assert evidence.config_digest == _LEGACY_CONFIG_DIGEST


_LEGACY_CONFIG_DIGEST = (
    "sha256:f79931d832bcc9901928ce17931720b6a42fba7bb09b531c211bff9325b12dfa"
)
_LEGACY_PROFILE_SET_DIGEST = (
    "sha256:ce9e19f22079cd8dc4dd4d14f943b4055b5788bed49188b5c9085b5af82b7ebc"
)


def _legacy_gateway_runner(*, digest=_LEGACY_CONFIG_DIGEST, labels=None):
    async def runner(args):
        if args[0] == "network":
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
                    {
                        "labels": (
                            labels
                            if labels is not None
                            else {
                                "moonmind.egress.enforcer": "docker-internal-proxy/v1",
                                "moonmind.egress.config-digest": _LEGACY_CONFIG_DIGEST,
                                "moonmind.egress.profile-set-digest": _LEGACY_PROFILE_SET_DIGEST,
                            }
                        ),
                        "networks": dict.fromkeys(_EXPECTED_GATEWAY_NETWORKS, {}),
                        "image": "sha256:gateway-image",
                        "health": "healthy",
                    }
                ).encode(),
                b"",
            )
        assert args == (
            "exec",
            DEFAULT_EGRESS_PROFILE.gateway_ref,
            "sha256sum",
            "/etc/squid/squid.conf",
        )
        return (
            0,
            f"{digest.removeprefix('sha256:')}  /etc/squid/squid.conf\n".encode(),
            b"",
        )

    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["openrouter.ai\n", "api.future-provider.com\n", "notopenai.com\n"]
)
async def test_new_destinations_require_compatible_gateway(configured_egress, value):
    module = configured_egress(value)
    with pytest.raises(RuntimeError, match="compatible gateway"):
        await module.attest_docker_egress(
            runner=_legacy_gateway_runner(),
            profile=module.OMNIGENT_EGRESS_PROFILE,
            backend_ref="candidate",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["config", "config-label", "profile-label", "missing-labels"]
)
async def test_legacy_transition_preserves_integrity(configured_egress, mutation):
    module = configured_egress()
    labels = {
        "moonmind.egress.enforcer": "docker-internal-proxy/v1",
        "moonmind.egress.config-digest": _LEGACY_CONFIG_DIGEST,
        "moonmind.egress.profile-set-digest": _LEGACY_PROFILE_SET_DIGEST,
    }
    digest = _LEGACY_CONFIG_DIGEST
    if mutation == "config":
        digest = "sha256:" + "f" * 64
    elif mutation == "missing-labels":
        labels = {"moonmind.egress.enforcer": "docker-internal-proxy/v1"}
    else:
        key = "config-digest" if mutation == "config-label" else "profile-set-digest"
        labels["moonmind.egress." + key] = "sha256:" + "f" * 64
    with pytest.raises(RuntimeError):
        await module.attest_docker_egress(
            runner=_legacy_gateway_runner(digest=digest, labels=labels),
            profile=module.OMNIGENT_EGRESS_PROFILE,
            backend_ref="candidate",
        )


def test_proxy_environment_limits_bypass_variables_to_safe_boundaries():
    values = restricted_proxy_env()
    assert "NO_PROXY=" in values
    assert "no_proxy=" in values
    assert all("169.254.169.254" not in value for value in values)

    omnigent_values = omnigent_proxy_env()
    assert "HTTP_PROXY=http://omnigent-egress-proxy:3129" in omnigent_values
    assert "NO_PROXY=localhost,127.0.0.1" in omnigent_values
    assert "no_proxy=localhost,127.0.0.1" in omnigent_values


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
    monkeypatch.setenv("MOONMIND_CONTROL_PLANE_NETWORK", "custom_control_plane")
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
        assert "custom_control_plane" in reloaded._EXPECTED_GATEWAY_NETWORKS
    finally:
        # Restore module-level defaults so later tests see the shipped values.
        monkeypatch.undo()
        importlib.reload(egress_module)


PACKAGE_REGISTRY_HOSTS = {"pypi.org", "files.pythonhosted.org", "registry.npmjs.org"}


def test_package_registry_egress_is_allowed_by_default(configured_egress):
    """Installing declared dependencies is a supported default, not opt-in."""
    module = configured_egress()

    names = {d.dns_name for d in module.DEFAULT_EGRESS_PROFILE.destinations}
    assert PACKAGE_REGISTRY_HOSTS <= names
    assert all(
        d.ports == (443,)
        for d in module.DEFAULT_EGRESS_PROFILE.destinations
        if d.dns_name in PACKAGE_REGISTRY_HOSTS
    )


def test_package_registry_egress_can_be_disabled(configured_egress, monkeypatch):
    """An operator who prefers a closed sandbox turns the whole class off."""
    monkeypatch.setenv("MOONMIND_PACKAGE_REGISTRY_EGRESS_ENABLED", "false")
    module = configured_egress()

    names = {d.dns_name for d in module.DEFAULT_EGRESS_PROFILE.destinations}
    assert not (PACKAGE_REGISTRY_HOSTS & names)
    # Disabling package installs never withdraws the source-control and
    # provider destinations the runtime itself depends on.
    assert {"github.com", "ghcr.io"} <= names


def test_package_registry_policy_digest_tracks_the_enabled_setting(
    configured_egress, monkeypatch
):
    """The attested digest distinguishes an open sandbox from a closed one."""
    enabled = configured_egress().EGRESS_CONFIG_DIGEST
    monkeypatch.setenv("MOONMIND_PACKAGE_REGISTRY_EGRESS_ENABLED", "false")
    assert configured_egress().EGRESS_CONFIG_DIGEST != enabled
