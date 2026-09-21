"""Compose-backed isolation evidence for the operator boundary (#4347).

Renders the test-only topology in
``docker-compose.operator-admission-test.yaml`` and pins its isolation
properties without requiring a running Docker daemon:

- the backend and the agent-like probe share no network (no Docker
  bridge, host-gateway, or shared-network path exists);
- neither service uses ``network_mode: host``;
- the backend publishes on the loopback-bound publish host by default;
- the probe's bypass attempts target the operator routes that the real
  admission code protects (unit-proven denial in
  ``test_operator_admission_4347.py`` and ``test_operator_boundary_mount_4347.py``).

The running variant (``docker compose -f
docker-compose.operator-admission-test.yaml up``) executes the same
bypass attempts from an actually isolated container; CI owns that run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

TOPOLOGY = Path("docker-compose.operator-admission-test.yaml")


def _compose() -> dict:
    return yaml.safe_load(TOPOLOGY.read_text(encoding="utf-8"))


def test_topology_file_exists():
    assert TOPOLOGY.is_file()


def test_backend_and_probe_share_no_network():
    compose = _compose()
    services = compose["services"]
    backend_networks = set(services["operator-test-api"].get("networks") or [])
    if isinstance(services["operator-test-api"].get("networks"), dict):
        backend_networks = set(services["operator-test-api"]["networks"].keys())
    probe_networks = set(services["agent-like-probe"].get("networks") or [])
    assert backend_networks, "backend must be attached to its isolated network"
    assert probe_networks, "probe must be attached to its isolated network"
    assert backend_networks.isdisjoint(probe_networks), (
        f"backend {backend_networks} and probe {probe_networks} must share "
        "no network: no bridge/shared-network bypass path may exist"
    )


def test_no_host_network_mode():
    compose = _compose()
    for name, service in compose["services"].items():
        assert service.get("network_mode") != "host", (
            f"{name}: host network mode would share the loopback namespace"
        )


def test_backend_publishes_on_loopback_by_default():
    compose = _compose()
    ports = compose["services"]["operator-test-api"]["ports"]
    assert ports, "backend must publish the operator port"
    mapping = str(ports[0])
    assert "MOONMIND_API_PUBLISH_HOST" in mapping, mapping
    assert mapping.startswith("${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}"), mapping


def test_probe_attempts_cover_published_and_internal_paths():
    compose = _compose()
    command = " ".join(compose["services"]["agent-like-probe"]["command"])
    assert "127.0.0.1" in command, "probe must attempt the host-published path"
    assert "operator-test-backend" in command, "probe must attempt the internal path"
    assert "/api/v1/operator/status" in command, (
        "probe must target the real admitted operator route"
    )
    assert "X-Moonmind-User" in command, "probe must attempt forged headers"


def test_isolation_networks_are_internal():
    compose = _compose()
    networks = compose.get("networks") or {}
    assert networks.get("operator-test-backend", {}).get("internal") is True
    assert networks.get("operator-test-agent", {}).get("internal") is True
