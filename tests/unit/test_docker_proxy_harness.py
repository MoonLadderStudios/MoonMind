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
                    {
                        "source": "haproxy.cfg.template",
                        "target": "/usr/local/etc/haproxy/haproxy.cfg.template",
                        "read_only": True,
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


@pytest.mark.parametrize("starts", [True, False])
def test_host_checks_wait_for_proxy_initialization(
    harness, rendered_config, monkeypatch, starts
):
    process_checks = 0
    config_validated = False
    sleeps = []

    def docker(*args, **kwargs):
        nonlocal process_checks, config_validated
        output = ""
        if "config" in args:
            output = json.dumps(rendered_config)
        elif args[-2:] == ("cat", "/proc/1/comm"):
            process_checks += 1
            output = "haproxy\n" if starts and process_checks > 1 else "sh\n"
        elif args[-2:] == ("cat", "/tmp/haproxy.cfg"):
            assert process_checks > 1, "read config before the entrypoint finished"
            output = (
                harness.ROOT / "docker/docker-proxy/haproxy.cfg.template"
            ).read_text()
        elif args[-4:] == ("haproxy", "-c", "-f", "/tmp/haproxy.cfg"):
            config_validated = True
        elif args[-2:] == ("cat", "/proc/1/cmdline"):
            output = "haproxy\0-W\0-db\0-f\0/tmp/haproxy.cfg\0"
        elif args[0] == "inspect":
            output = json.dumps(
                [
                    {
                        "HostConfig": {"PortBindings": {}},
                        "NetworkSettings": {"Networks": {"proxy": {}}},
                    }
                ]
            )
        elif args[0] == "run":
            raise ProxyStartupReached
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr(harness, "docker", docker)
    monkeypatch.setattr(harness.time, "sleep", sleeps.append)
    if starts:
        with pytest.raises(ProxyStartupReached):
            harness.host_checks()
        assert config_validated
        assert sleeps == [0.5]
    else:
        with pytest.raises(RuntimeError, match="proxy.*initializ"):
            harness.host_checks()
        assert not config_validated
        assert 1 < process_checks <= 30
        assert len(sleeps) == process_checks - 1
