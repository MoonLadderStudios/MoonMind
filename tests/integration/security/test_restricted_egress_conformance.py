"""Contract checks for the live restricted-egress conformance topology.

The network-layer journey itself is executed by ``tools/test_integration.sh``
before pytest. These checks keep its production safety properties reviewable
in the required ``integration_ci`` suite.
"""

from pathlib import Path

import pytest
import yaml


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[3]


def test_conformance_runner_has_no_network_or_namespace_bypass_authority() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.egress-conformance.yaml").read_text()
    )
    runner = compose["services"]["conformance-runner"]
    assert runner["networks"] == ["restricted-network"]
    assert runner["cap_drop"] == ["ALL"]
    assert runner["read_only"] is True
    assert runner["security_opt"] == ["no-new-privileges:true"]
    assert "network_mode" not in runner
    assert "extra_hosts" not in runner
    assert compose["networks"]["restricted-network"]["internal"] is True
    assert set(compose["services"]["egress-proxy"]["networks"]) == {
        "fixture-network",
        "restricted-network",
    }
    helper = compose["services"]["managed-helper"]
    assert helper["networks"] == ["restricted-network"]
    assert helper["cap_drop"] == ["ALL"]
    assert helper["read_only"] is True
    assert helper["security_opt"] == ["no-new-privileges:true"]
    assert "network_mode" not in helper
    assert "extra_hosts" not in helper
    assert "volumes" in helper
    assert all("/var/run/docker.sock" not in volume for volume in helper["volumes"])


def test_live_journey_covers_required_representative_denials() -> None:
    script = (
        ROOT / "tests/integration/security/run_conformance.sh"
    ).read_text()
    for attempt in (
        "forbidden.test",
        "93.184.216.34",
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "redirect-forbidden",
        "--request TRACE",
        "NO_PROXY=allowed.test",
        "cname.allowed.test",
        "mixed.allowed.test",
        "rebind.allowed.test",
        "198.19.0.1",
        "host.docker.internal",
        "busybox route add default",
        "/var/run/docker.sock",
    ):
        assert attempt in script
    integration_runner = (ROOT / "tools/test_integration.sh").read_text()
    assert "run --rm conformance-runner" in integration_runner
    assert "run --rm managed-helper" in integration_runner


def test_conformance_proxy_uses_controllable_dns_at_request_time() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.egress-conformance.yaml").read_text()
    )
    dns = compose["services"]["dns-fixture"]
    assert dns["networks"]["fixture-network"]["ipv4_address"] == "198.18.0.53"
    squid = (ROOT / "tests/integration/security/squid.conf").read_text()
    assert "dns_nameservers 198.18.0.53" in squid
    assert "positive_dns_ttl 1 seconds" in squid
    fixture = (ROOT / "tests/integration/security/dns_fixture.py").read_text()
    assert "cname.allowed.test" in fixture
    assert "mixed.allowed.test" in fixture
    assert "rebind.allowed.test" in fixture
