"""Exercise the proxy harness's host-side boundary before starting Docker."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class ProxyStartupReached(Exception):
    """Stop the fixture after host validation without launching containers."""


@pytest.fixture
def harness(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "tools/test_docker_proxy.py"
    spec = importlib.util.spec_from_file_location("docker_proxy_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setitem(sys.modules, "engine", SimpleNamespace())
    return module


@pytest.fixture
def rendered_config(harness, monkeypatch):
    config = {
        "services": {
            "docker-proxy": {
                "image": "proxy:test",
                "volumes": [
                    {
                        "source": "/var/run/docker.sock",
                        "target": "/var/run/docker.sock",
                    },
                ],
            },
            "worker": {"volumes": []},
        },
        "networks": {"docker-proxy-network": {"internal": True}},
    }

    def docker(*args, **kwargs):
        if "config" in args:
            return SimpleNamespace(stdout=json.dumps(config), returncode=0)
        if args[-3:] == ("up", "-d", "docker-proxy"):
            raise ProxyStartupReached
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(harness, "docker", docker)
    return config


@pytest.mark.parametrize("mount_type", ["tmpfs", "volume"])
def test_host_checks_accept_mounts_without_sources(
    harness, rendered_config, mount_type
):
    rendered_config["services"]["worker"]["volumes"].append(
        {"type": mount_type, "target": "/scratch"}
    )

    with pytest.raises(ProxyStartupReached):
        harness.host_checks()


def test_host_checks_reject_socket_mounts_outside_proxy(harness, rendered_config):
    rendered_config["services"]["worker"]["volumes"].append(
        {"type": "bind", "source": "/var/run/docker.sock", "target": "/docker.sock"}
    )

    with pytest.raises(AssertionError, match="worker"):
        harness.host_checks()
