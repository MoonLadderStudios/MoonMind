"""Replay the healthy-container/LAN-lockout incident at the replacement gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from moonmind.deployment_access import (
    DeploymentAccessError,
    check_compose_access,
    validate_access,
)
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
            "NetworkSettings": {"Networks": {"moonmind-test-access_default": {}}},
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
@pytest.mark.parametrize("target", [None, "api", "postgres", "client"])
@pytest.mark.parametrize("entrypoint", ["worker", "cli"])
async def test_worker_runner_blocks_before_real_up_subprocess(
    tmp_path, monkeypatch, preserved, target, entrypoint
):
    # The public runner invocation uses real subprocesses and the same gate as
    # host scripts. Only the Docker daemon is replaced; no deployment is touched.
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    proposed = candidate("192.0.2.10" if preserved else "127.0.0.1")
    proposed["services"]["postgres"] = {"image": "postgres:test"}
    proposed["services"]["client"] = {"image": "client:test", "depends_on": {"api": {}}}
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
    allowed = preserved or target == "postgres"
    targets = (target,) if target else ()
    if entrypoint == "cli":
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "moonmind/deployment_access.py"),
        ]
        if target:
            command.extend(["--service", target])
        result = subprocess.run(
            [*command, "--", "docker", "compose"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) == allowed
        if not allowed:
            assert "published interfaces/ports" in result.stderr
        assert not marker.exists()
        return
    if allowed:
        result = await runner.up(
            stack="moonmind",
            command=("docker", "compose", "up", "-d", *targets),
            requested_image="moonmind:test",
        )
        assert result["exitCode"] == 0
        assert marker.exists()
    else:
        with pytest.raises(ToolFailure) as exc:
            await runner.up(
                stack="moonmind",
                command=("docker", "compose", "up", "-d", *targets),
                requested_image="moonmind:test",
            )
        assert exc.value.error_code == "DEPLOYMENT_ACCESS_CHANGED"
        assert not marker.exists()


@pytest.mark.parametrize("published", [True, False])
def test_ingress_network_name_survives_compose_key_rename(published):
    old = installed()
    proposed = candidate()
    if not published:
        old[0]["HostConfig"]["PortBindings"] = None
        proposed["services"]["api"]["ports"] = []
    old[0]["NetworkSettings"]["Networks"] = {"operator-ingress": {}}
    proposed["services"]["api"]["networks"] = {"renamed-key": None}
    proposed["networks"] = {
        "renamed-key": {"name": "operator-ingress", "external": True}
    }
    validate_access(proposed, old)
    proposed["networks"]["renamed-key"]["name"] = "different-ingress"
    with pytest.raises(DeploymentAccessError, match="API network attachments"):
        validate_access(proposed, old)
    proposed["services"]["api"].pop("networks")
    with pytest.raises(DeploymentAccessError, match="API network attachments"):
        validate_access(proposed, old)


@pytest.mark.parametrize(
    "name", ["OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
)
@pytest.mark.parametrize("replacement", [None, "changed-sensitive-value"])
def test_oidc_login_inputs_cannot_change_silently(name, replacement):
    old = installed()
    old[0]["Config"]["Env"] = [
        "AUTH_PROVIDER=oidc",
        "MOONMIND_TRUSTED_INGRESS=1",
        f"{name}=original-sensitive-value",
    ]
    proposed = candidate()
    proposed["services"]["api"]["environment"].update(
        {"AUTH_PROVIDER": "oidc", name: "original-sensitive-value"}
    )
    validate_access(proposed, old)
    proposed["services"]["api"]["environment"][name] = replacement
    with pytest.raises(DeploymentAccessError) as exc:
        validate_access(proposed, old)
    assert name in str(exc.value)
    assert "sensitive-value" not in str(exc.value)


def test_unrelated_service_still_protects_orphaned_api(tmp_path, monkeypatch):
    proposed = candidate()
    proposed["services"] = {"postgres": {}}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "config" in command:
            payload = proposed
        elif "ps" in command:
            return subprocess.CompletedProcess(command, 0, "installed-api", "")
        else:
            payload = installed()
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(DeploymentAccessError, match="removes the installed API"):
        check_compose_access(
            ["docker", "compose"], services=["postgres"], remove_orphans=True
        )
    calls.clear()
    check_compose_access(
        ["docker", "compose"], services=["postgres"], remove_orphans=False
    )
    assert len(calls) == 1


def test_no_deps_does_not_gate_untouched_api(monkeypatch):
    proposed = candidate()
    proposed["services"]["client"] = {"depends_on": {"api": {}}}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "config" in command
        return subprocess.CompletedProcess(command, 0, json.dumps(proposed), "")

    monkeypatch.setattr(subprocess, "run", run)
    check_compose_access(
        ["docker", "compose"], services=["client"], include_dependencies=False
    )
    assert len(calls) == 1
