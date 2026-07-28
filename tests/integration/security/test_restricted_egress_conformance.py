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
    ):
        assert attempt in script
