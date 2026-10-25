"""Keep the existing capability policy while changing the HTTP transport."""

import importlib.util
import re
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / "docker/docker-proxy/start.py"
spec = importlib.util.spec_from_file_location("docker_proxy_policy", path)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


def permits(env, method, path):
    return re.fullmatch(proxy.policy(env)[method], path) is not None


@pytest.mark.parametrize("prefix", ["", "/v1.48", "/v1.55"])
def test_default_policy_only_exposes_readonly_discovery(prefix):
    assert permits({}, "GET", prefix + "/version")
    assert permits({}, "GET", prefix + "/_ping")
    assert not permits({}, "GET", prefix + "/containers/json")
    assert not permits({}, "POST", prefix + "/version")


@pytest.mark.parametrize("section", proxy.SECTIONS)
@pytest.mark.parametrize("value", ["0", "1", "false", "true"])
def test_each_capability_controls_its_endpoint(section, value):
    endpoint = "_ping" if section == "PING" else section.lower()
    env = {name: "0" for name in proxy.SECTIONS}
    env[section] = value
    assert permits(env, "GET", "/v1.48/" + endpoint) == proxy.enabled(value)
    assert not permits(env, "POST", "/v1.48/" + endpoint)
    env["POST"] = "1"
    assert permits(env, "POST", "/v1.48/" + endpoint) == proxy.enabled(value)


def test_enabled_workload_policy_does_not_grant_other_sections():
    env = {
        name: "1"
        for name in (
            "CONTAINERS",
            "EXEC",
            "IMAGES",
            "BUILD",
            "SESSION",
            "NETWORKS",
            "VOLUMES",
            "INFO",
            "POST",
        )
    }
    for method in proxy.METHODS:
        for endpoint in (
            "secrets",
            "configs",
            "services",
            "swarm",
            "system",
            "plugins",
            "auth",
            "unknown",
        ):
            assert not permits(env, method, "/v1.48/" + endpoint)
    assert permits(env, "POST", "/v1.48/session")
    assert permits(env, "POST", "/v1.48/containers/create")
    assert permits(env, "DELETE", "/v1.48/volumes/test")
    env["POST"] = "0"
    for method in set(proxy.METHODS) - {"GET"}:
        assert not permits(env, method, "/v1.48/containers/create")


def test_restart_override_keeps_other_container_operations_denied():
    env = {"ALLOW_RESTARTS": "1", "POST": "1"}
    for operation in ("stop", "restart", "kill"):
        assert permits(env, "POST", "/v1.48/containers/test-1/" + operation)
    for operation in ("start", "exec", "json"):
        assert not permits(env, "POST", "/v1.48/containers/test-1/" + operation)
    assert not permits(env, "POST", "/v1.48/containers/create")
    env["POST"] = "0"
    assert not permits(env, "POST", "/v1.48/containers/test-1/stop")


def test_unknown_flag_values_fail_closed_and_cannot_inject_regex():
    assert not permits({"CONTAINERS": ".*"}, "GET", "/containers/json")
    assert not permits({"POST": "anything"}, "POST", "/version")
