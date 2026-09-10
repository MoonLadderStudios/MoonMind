"""Replay the healthy-container/LAN-lockout incident at the replacement gate."""

import json
import os

import pytest

from moonmind.deployment_access import DeploymentAccessError, validate_access
from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure


def candidate(host="192.0.2.10"):
    return {
        "name": "moonmind-test-access",
        "services": {
            "api": {
                "ports": [{"host_ip": host, "published": "7000", "target": 8000}],
                "environment": {
                    "AUTH_PROVIDER": "disabled",
                    "MOONMIND_TRUSTED_INGRESS": "1",
                },
            }
        },
    }


def installed(host="192.0.2.10"):
    return [
        {
            "HostConfig": {
                "PortBindings": {
                    "8000/tcp": [{"HostIp": host, "HostPort": "7000"}],
                }
            },
            "Config": {"Env": ["AUTH_PROVIDER=disabled", "MOONMIND_TRUSTED_INGRESS=1"]},
        }
    ]


@pytest.mark.parametrize("host", ["127.0.0.1", "192.0.2.10", "0.0.0.0", "::1"])
def test_preserved_access_and_fresh_install(host):
    validate_access(candidate(host), installed(host))
    validate_access(candidate(host), [])


@pytest.mark.parametrize("change", ["host", "port", "removed", "auth", "trust", "url"])
def test_access_changes_require_explicit_migration(change):
    proposed = candidate()
    api = proposed["services"]["api"]
    if change == "host":
        api["ports"][0]["host_ip"] = "127.0.0.1"
    elif change == "port":
        api["ports"][0]["published"] = "7001"
    elif change == "removed":
        proposed["services"].clear()
    else:
        key = {
            "auth": "AUTH_PROVIDER",
            "trust": "MOONMIND_TRUSTED_INGRESS",
            "url": "MOONMIND_PUBLIC_BASE_URL",
        }[change]
        api["environment"][key] = "changed"
    with pytest.raises(DeploymentAccessError):
        validate_access(proposed, installed())


def test_historical_wildcard_and_dual_stack_inspect_shape():
    old = installed("")
    proposed = candidate("")
    validate_access(proposed, old)
    with pytest.raises(DeploymentAccessError, match="published interfaces/ports"):
        validate_access(candidate("0.0.0.0"), old)
    old = installed("0.0.0.0")
    old[0]["HostConfig"]["PortBindings"]["8000/tcp"].append(
        {"HostIp": "::", "HostPort": "7000"}
    )
    proposed = candidate("0.0.0.0")
    with pytest.raises(DeploymentAccessError, match="published interfaces/ports"):
        validate_access(proposed, old)
    proposed["services"]["api"]["ports"].append(
        {"host_ip": "::", "published": "7000", "target": 8000}
    )
    validate_access(proposed, old)


def test_unpublished_api_behind_proxy():
    old = installed()
    old[0]["HostConfig"]["PortBindings"] = None
    proposed = candidate()
    proposed["services"]["api"]["ports"] = []
    validate_access(proposed, old)


@pytest.mark.asyncio
@pytest.mark.parametrize("preserved", [True, False])
async def test_worker_runner_blocks_before_real_up_subprocess(
    tmp_path, monkeypatch, preserved
):
    # The public runner invocation uses real subprocesses and the same gate as
    # host scripts. Only the Docker daemon is replaced; no deployment is touched.
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    proposed = candidate("192.0.2.10" if preserved else "127.0.0.1")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"candidate": proposed, "installed": installed()}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "docker"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
data = json.loads(Path(os.environ["ACCESS_FIXTURE"]).read_text())
if args[-3:] == ["config", "--format", "json"]:
    print(json.dumps(data["candidate"]))
elif args[0] == "ps":
    assert "label=com.docker.compose.project=moonmind-test-access" in args
    print("installed-api")
elif args[0] == "inspect":
    print(json.dumps(data["installed"]))
elif "up" in args:
    Path(os.environ["ACCESS_UP_MARKER"]).write_text("recreated")
else:
    sys.exit(2)
""")
    fake.chmod(0o755)
    marker = tmp_path / "up"
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ACCESS_FIXTURE", str(fixture))
    monkeypatch.setenv("ACCESS_UP_MARKER", str(marker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path), project_name="moonmind-test-access"
    )
    if preserved:
        result = await runner.up(
            stack="moonmind",
            command=("docker", "compose", "up", "-d", "api"),
            requested_image="moonmind:test",
        )
        assert result["exitCode"] == 0
        assert marker.exists()
    else:
        with pytest.raises(ToolFailure) as exc:
            await runner.up(
                stack="moonmind",
                command=("docker", "compose", "up", "-d", "api"),
                requested_image="moonmind:test",
            )
        assert exc.value.error_code == "DEPLOYMENT_ACCESS_CHANGED"
        assert not marker.exists()
