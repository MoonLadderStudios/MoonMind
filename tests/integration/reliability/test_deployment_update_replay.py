from __future__ import annotations

import json
import os
import shutil
import stat
import textwrap
from pathlib import Path

import pytest

from moonmind.workflows.skills.deployment_execution import (
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    HostDockerComposeRunner,
    InMemoryDesiredStateStore,
    InMemoryEvidenceWriter,
)
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


_FAKE_DOCKER_ENGINE = r"""
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

STATE_PATH = Path(os.environ["MM_DEPLOYMENT_REPLAY_STATE"])
REAL_DOCKER = os.environ["MM_DEPLOYMENT_REPLAY_REAL_DOCKER"]
TARGET_ID = "sha256:" + "a" * 64
AGENT_SERVICE = "temporal-worker-agent-runtime"
PROXY_SERVICE = "sandbox-egress-proxy"
RUNNER_SERVICE = "temporal-worker-deployment-control"
NETWORK = "restricted-egress-network"
KNOWN_SERVICES = (AGENT_SERVICE, PROXY_SERVICE, RUNNER_SERVICE)


def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def selected_services(parts):
    return [service for service in KNOWN_SERVICES if service in parts]


args = sys.argv[1:]
state = load_state()

if args[:2] == ["image", "inspect"]:
    requested_image = args[2]
    print(
        json.dumps(
            [
                {
                    "Id": TARGET_ID,
                    "RepoTags": [requested_image],
                    "RepoDigests": [
                        requested_image.split(":", 1)[0] + "@sha256:" + "b" * 64
                    ],
                }
            ]
        )
    )
    raise SystemExit(0)

if not args or args[0] != "compose":
    print("unsupported fake Docker command", file=sys.stderr)
    raise SystemExit(2)

command_index = next(
    (index for index, part in enumerate(args) if part in {"config", "ps", "images", "pull", "up"}),
    None,
)
if command_index is None:
    print("missing Compose command", file=sys.stderr)
    raise SystemExit(2)

command = args[command_index]
tail = args[command_index + 1 :]

if command == "config":
    completed = subprocess.run(
        [REAL_DOCKER, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    state["composeConfigCalls"] = state.get("composeConfigCalls", 0) + 1
    save_state(state)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    raise SystemExit(completed.returncode)

if command == "ps":
    print(json.dumps(state["containers"]))
    raise SystemExit(0)

if command == "images":
    print(json.dumps(state["images"]))
    raise SystemExit(0)

if command == "pull":
    state["pullServices"] = selected_services(tail)
    save_state(state)
    raise SystemExit(0)

up_services = selected_services(tail)
state["upServices"] = up_services
if AGENT_SERVICE in up_services and PROXY_SERVICE not in up_services:
    state["networkError"] = f"network {NETWORK} is unavailable"
    save_state(state)
    print(state["networkError"], file=sys.stderr)
    raise SystemExit(1)

state["networks"] = {NETWORK: {"services": up_services}}
state["containers"] = [
    {
        "ID": "agent-runtime-new",
        "Name": "replay-temporal-worker-agent-runtime-1",
        "Service": AGENT_SERVICE,
        "State": "running",
        "Health": "healthy",
    },
    {
        "ID": "sandbox-proxy-new",
        "Name": "replay-sandbox-egress-proxy-1",
        "Service": PROXY_SERVICE,
        "State": "running",
        "Health": "healthy",
    },
    {
        "ID": "deployment-runner-existing",
        "Name": "replay-temporal-worker-deployment-control-1",
        "Service": RUNNER_SERVICE,
        "State": "running",
        "Health": "healthy",
    },
]
state["images"] = [
    {
        "ID": TARGET_ID,
        "Repository": "ghcr.io/moonladderstudios/moonmind",
        "Service": AGENT_SERVICE,
        "Tag": "latest",
    },
    {
        "ID": "sha256:" + "c" * 64,
        "Repository": "ubuntu/squid",
        "Service": PROXY_SERVICE,
        "Tag": "latest",
    },
    {
        "ID": "sha256:" + "d" * 64,
        "Repository": "ghcr.io/moonladderstudios/moonmind",
        "Service": RUNNER_SERVICE,
        "Tag": "latest",
    },
]
save_state(state)
raise SystemExit(0)
"""


@pytest.mark.asyncio
async def test_deployment_update_reconciles_non_image_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_id = "deployment-update-infrastructure-reconciliation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    requested_repository = manifest["requestedRepository"]
    requested_image = f"{requested_repository}:latest"
    real_docker = shutil.which("docker")
    assert real_docker is not None, "integration_ci image must provide Docker Compose"

    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text(
        textwrap.dedent(
            f"""
            services:
              temporal-worker-agent-runtime:
                image: {requested_image}
                networks:
                  - restricted-egress-network
              sandbox-egress-proxy:
                image: ubuntu/squid:latest
                networks:
                  - restricted-egress-network
              temporal-worker-deployment-control:
                image: {requested_image}
            networks:
              restricted-egress-network:
                internal: true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    state_path = tmp_path / "engine-state.json"
    state_path.write_text(
        json.dumps(
            {
                "composeConfigCalls": 0,
                "containers": [
                    {
                        "ID": "agent-runtime-old",
                        "Service": "temporal-worker-agent-runtime",
                        "State": "running",
                    },
                    {
                        "ID": "deployment-runner-existing",
                        "Service": "temporal-worker-deployment-control",
                        "State": "running",
                    },
                ],
                "images": [],
                "networks": {},
            }
        ),
        encoding="utf-8",
    )
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        textwrap.dedent(_FAKE_DOCKER_ENGINE).lstrip(),
        encoding="utf-8",
    )
    fake_docker.chmod(
        fake_docker.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    monkeypatch.setenv("MM_DEPLOYMENT_REPLAY_STATE", str(state_path))
    monkeypatch.setenv("MM_DEPLOYMENT_REPLAY_REAL_DOCKER", real_docker)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        project_name="deployment-replay",
        excluded_services=tuple(manifest["excludedServices"]),
    )
    executor = DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=runner,
        excluded_services=tuple(manifest["excludedServices"]),
    )

    result = await executor.execute(
        {
            "stack": "moonmind",
            "image": {
                "repository": requested_repository,
                "reference": "latest",
            },
            "mode": "changed_services",
            "removeOrphans": True,
            "wait": True,
            "reason": "Replay production infrastructure reconciliation failure",
        },
        context={"deployment_runner_mode": "privileged_worker"},
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert state["composeConfigCalls"] >= 4
    assert state["pullServices"] == expected["pullServices"]
    assert state["upServices"] == expected["reconciliationServices"]
    assert set(state["networks"]["restricted-egress-network"]["services"]) == set(
        expected["reconciliationServices"]
    )
    assert {
        container["Service"]
        for container in state["containers"]
        if container["State"] == "running"
    } >= set(expected["reconciliationServices"])
    assert all(
        service not in state["pullServices"] and service not in state["upServices"]
        for service in expected["excludedServices"]
    )
