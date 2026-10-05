"""Qualify host updater recovery against an isolated real Docker transport.

Opt in with ``MOONMIND_TEST_HOST_UPDATE_TRANSPORT=1`` on a workstation with
Docker Compose and the MoonMind/proxy images available. Only disposable
``moonmind-test-update-transport-*`` projects are created or removed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
UPDATER_PATH = ROOT / ".agents/skills/update-moonmind/scripts/update_release.py"
SPEC = importlib.util.spec_from_file_location("host_update_transport", UPDATER_PATH)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)

pytestmark = [pytest.mark.integration]


def _run(args, *, cwd, env, check=True):
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=90
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def transport_stack(tmp_path):
    if os.environ.get("MOONMIND_TEST_HOST_UPDATE_TRANSPORT") != "1":
        pytest.skip(
            "Set MOONMIND_TEST_HOST_UPDATE_TRANSPORT=1 for real Docker qualification"
        )
    if shutil.which("docker") is None:
        pytest.fail("Docker CLI is required for the requested transport qualification")
    env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "SystemRoot",
            "ProgramData",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "LOCALAPPDATA",
            "APPDATA",
            "TEMP",
            "TMP",
            "TMPDIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_API_VERSION",
        )
        if name in os.environ
    }
    _run(["docker", "info"], cwd=tmp_path, env=env)
    _run(["docker", "compose", "version"], cwd=tmp_path, env=env)
    app_image = os.environ.get(
        "MOONMIND_TEST_TRANSPORT_APP_IMAGE",
        "ghcr.io/moonladderstudios/moonmind:latest",
    )
    proxy_image = os.environ.get(
        "MOONMIND_TEST_TRANSPORT_PROXY_IMAGE", "tecnativa/docker-socket-proxy:0.1.1"
    )
    for image in (app_image, proxy_image):
        _run(["docker", "image", "inspect", image], cwd=tmp_path, env=env)
    project = f"moonmind-test-update-transport-{uuid4().hex[:10]}"
    compose_path = tmp_path / "compose.json"
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(tmp_path),
        "-f",
        str(compose_path),
    ]
    socket_source = os.environ.get(
        "MOONMIND_TEST_DOCKER_SOCKET", "/var/run/docker.sock"
    )
    state_dir = tmp_path / "deployment-state"
    state_dir.mkdir()
    config = {
        "services": {
            "sentinel": {
                "image": app_image,
                "entrypoint": ["/bin/sh", "-c", "exec sleep 600"],
            },
            "temporal-worker-deployment-control": {
                "image": app_image,
                "pull_policy": "never",
                "environment": {
                    "DOCKER_HOST": "tcp://docker-proxy:2375",
                    "MOONMIND_DEPLOYMENT_LOCK_DIR": "/workspace/state/locks",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(state_dir),
                        "target": "/workspace/state",
                    }
                ],
                "entrypoint": ["docker"],
            },
            "docker-proxy": {
                "image": proxy_image,
                "environment": {"INFO": "1"},
                "volumes": [
                    {
                        "type": "bind",
                        "source": socket_source,
                        "target": "/var/run/docker.sock",
                    }
                ],
            },
        },
        "networks": {"default": {"name": f"{project}-private"}},
    }

    def write():
        compose_path.write_text(json.dumps(config), encoding="utf-8")

    def compose(*args, check=True):
        return _run([*command, *args], cwd=tmp_path, env=env, check=check)

    def container_id(service):
        return compose("ps", "-a", "-q", service).stdout.strip()

    def probe():
        return compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "docker",
            "temporal-worker-deployment-control",
            "info",
            "--format",
            "{{.ServerVersion}}",
            check=False,
        )

    write()
    try:
        compose("up", "-d", "--no-deps", "sentinel")
        yield {
            "config": config,
            "write": write,
            "compose": compose,
            "id": container_id,
            "probe": probe,
            "command": command,
            "repo": tmp_path,
            "env": env,
            "socket_source": socket_source,
        }
    finally:
        compose("down", "--remove-orphans")


@pytest.mark.parametrize("initial_proxy", ["missing", "stopped", "broken", "healthy"])
def test_host_handoff_repairs_transport_and_preserves_other_services(
    transport_stack, initial_proxy
):
    stack = transport_stack
    sentinel = stack["id"]("sentinel")
    assert sentinel
    previous_proxy = ""
    if initial_proxy == "broken":
        # A directory at the socket mount reproduces an unusable Docker
        # transport without altering a real host socket or deployment.
        bad_socket = stack["repo"] / "invalid-socket"
        bad_socket.mkdir()
        stack["config"]["services"]["docker-proxy"]["volumes"][0]["source"] = str(
            bad_socket
        )
        stack["write"]()
        stack["compose"]("up", "-d", "--no-deps", "docker-proxy", check=False)
        previous_proxy = stack["id"]("docker-proxy")
        assert previous_proxy
        failed_probe = stack["probe"]()
        assert failed_probe.returncode != 0 or not failed_probe.stdout.strip()
        stack["config"]["services"]["docker-proxy"]["volumes"][0]["source"] = stack[
            "socket_source"
        ]
        stack["write"]()
    elif initial_proxy != "missing":
        stack["compose"]("up", "-d", "--no-deps", "docker-proxy")
        previous_proxy = stack["id"]("docker-proxy")
        if initial_proxy == "stopped":
            stack["compose"]("stop", "docker-proxy")

    update._ensure_legacy_docker_transport(
        stack["command"], stack["repo"], stack["env"]
    )
    if initial_proxy == "healthy":
        for _ in range(2):
            update._ensure_legacy_docker_transport(
                stack["command"], stack["repo"], stack["env"]
            )

    working_probe = stack["probe"]()
    assert working_probe.returncode == 0 and working_probe.stdout.strip()
    assert stack["id"]("sentinel") == sentinel
    proxy = stack["id"]("docker-proxy")
    assert proxy
    if initial_proxy in {"healthy", "stopped"}:
        assert proxy == previous_proxy
    elif initial_proxy == "broken":
        assert proxy != previous_proxy


def test_external_transport_failure_does_not_create_a_local_proxy(transport_stack):
    stack = transport_stack
    sentinel = stack["id"]("sentinel")
    stack["config"]["services"]["temporal-worker-deployment-control"]["environment"][
        "DOCKER_HOST"
    ] = "tcp://unreachable-transport.invalid:2375"
    stack["write"]()

    with pytest.raises(RuntimeError):
        update._ensure_legacy_docker_transport(
            stack["command"], stack["repo"], stack["env"]
        )

    assert stack["id"]("docker-proxy") == ""
    assert stack["id"]("sentinel") == sentinel


def test_worker_startup_failure_keeps_the_working_proxy(transport_stack):
    stack = transport_stack
    stack["compose"]("up", "-d", "--no-deps", "docker-proxy")
    working_probe = stack["probe"]()
    assert working_probe.returncode == 0 and working_probe.stdout.strip()
    proxy = stack["id"]("docker-proxy")
    sentinel = stack["id"]("sentinel")
    # This real image has no Docker CLI: the one-off fails to execute its
    # entrypoint while the existing proxy's Docker transport stays healthy.
    stack["config"]["services"]["temporal-worker-deployment-control"]["image"] = stack[
        "config"
    ]["services"]["docker-proxy"]["image"]
    stack["write"]()

    with pytest.raises(RuntimeError):
        update._ensure_legacy_docker_transport(
            stack["command"], stack["repo"], stack["env"]
        )

    assert stack["id"]("docker-proxy") == proxy
    assert stack["id"]("sentinel") == sentinel


def test_proxy_recovery_respects_the_active_deployment_lock(transport_stack):
    stack = transport_stack
    sentinel = stack["id"]("sentinel")
    script = """
import asyncio, os
from moonmind.workflows.skills.deployment_execution import FileDeploymentUpdateLockManager
async def hold():
    lease = await FileDeploymentUpdateLockManager(os.environ['MOONMIND_DEPLOYMENT_LOCK_DIR']).acquire('moonmind')
    async with lease:
        print('LOCK_READY', flush=True)
        await asyncio.sleep(90)
asyncio.run(hold())
"""
    owner = stack["compose"](
        "run",
        "-d",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "python",
        "temporal-worker-deployment-control",
        "-c",
        script,
    ).stdout.strip()
    assert owner
    try:
        deadline = time.monotonic() + 30
        while True:
            logs = _run(
                ["docker", "logs", owner], cwd=stack["repo"], env=stack["env"]
            ).stdout
            if "LOCK_READY" in logs:
                break
            assert time.monotonic() < deadline, logs
            time.sleep(0.2)
        with pytest.raises(RuntimeError, match="[Ll]ock|[Oo]wned"):
            update._ensure_legacy_docker_transport(
                stack["command"], stack["repo"], stack["env"]
            )
        assert stack["id"]("docker-proxy") == ""
        assert stack["id"]("sentinel") == sentinel
    finally:
        _run(
            ["docker", "rm", "-f", owner],
            cwd=stack["repo"],
            env=stack["env"],
            check=False,
        )

    update._ensure_legacy_docker_transport(
        stack["command"], stack["repo"], stack["env"]
    )
    assert stack["id"]("docker-proxy")
    assert stack["id"]("sentinel") == sentinel
