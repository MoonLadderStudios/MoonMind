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
KNOWN_SERVICES = (AGENT_SERVICE, PROXY_SERVICE, RUNNER_SERVICE, "init-db")


def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def selected_services(parts):
    return [service for service in KNOWN_SERVICES if service in parts]


args = sys.argv[1:]
state = load_state()

if args[:3] == ["ps", "-a", "-q"]:
    # This infrastructure fixture has no API container. The production gate
    # still queries the selected project's installed API before each up.
    assert "label=com.docker.compose.service=api" in args
    state["accessPreflightCalls"] = state.get("accessPreflightCalls", 0) + 1
    save_state(state)
    raise SystemExit(0)

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

if args[:2] == ["info", "--format"]:
    print(state.get("fakeOperatingSystem", "Docker Desktop"))
    raise SystemExit(0)

if args[0] == "ps" and args[1:2] == ["-aq"]:
    wanted = ""
    if "--filter" in args:
        wanted = args[args.index("--filter") + 1].removeprefix("name=^/").removesuffix("$")
    for updater_name, updater_id in state.get("updaters", {}).items():
        if updater_name == wanted:
            print(updater_id)
            break
    raise SystemExit(0)

if args[:2] == ["ps", "-q"]:
    # Installed (non one-off) containers of this Compose project.
    print("\n".join(state.get("installedContainers", {})))
    raise SystemExit(0)

if args[:3] == ["inspect", "--format", "{{json .Mounts}}"]:
    # The deployment's own containers, then this worker's own mount table,
    # answer which host path the daemon resolves for the checkout. An engine
    # that records neither leaves the caller with no evidence.
    installed = state.get("installedContainers", {})
    targets = [target for target in args[3:] if target in installed]
    if targets:
        for target in targets:
            print(json.dumps(installed[target]))
    else:
        print(json.dumps(state.get("selfMounts", [])))
    raise SystemExit(0)

if args[0] == "inspect":
    target = args[1] if len(args) > 1 else ""
    for updater_name, updater_id in state.get("updaters", {}).items():
        if target in (updater_id, updater_name):
            info = state.get("updaterInfo", {}).get(updater_name, {})
            print(
                json.dumps(
                    [
                        {
                            "Image": info.get("imageId"),
                            "State": {"Running": True},
                            "Config": {
                                "Labels": {
                                    "moonmind.release.owner": info.get("owner")
                                }
                            },
                        }
                    ]
                )
            )
            raise SystemExit(0)
    raise SystemExit(0)

if args[0] == "update":
    raise SystemExit(0)

if not args or args[0] != "compose":
    print("unsupported fake Docker command", file=sys.stderr)
    raise SystemExit(2)

command_index = next(
    (index for index, part in enumerate(args) if part in {"config", "ps", "images", "pull", "up", "run"}),
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
    policy = tail[tail.index("--policy") + 1] if "--policy" in tail else None
    state.setdefault("pullCalls", []).append(
        {"policy": policy, "services": selected_services(tail)}
    )
    save_state(state)
    raise SystemExit(0)

if command == "run":
    # Detached updater launch. Record the deployment-state bind the daemon
    # observes without enforcing it here: the test asserts daemon visibility,
    # and an unrewritten WSL source must fail that assertion exactly as the
    # production daemon mounted an empty directory for it.
    name = tail[tail.index("--name") + 1]
    project_directory = (
        args[args.index("--project-directory") + 1]
        if "--project-directory" in args
        else ""
    )
    config = json.loads(Path(args[args.index("-f") + 1]).read_text())

    def _resolve_bind(volume):
        if isinstance(volume, dict):
            return volume
        parts = str(volume).split(":")
        source = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        if source.startswith("."):
            source = project_directory.rstrip("/") + "/" + source.lstrip("./")
        return {"source": source, "target": target}

    service = config["services"]["temporal-worker-deployment-control"]
    state_bind = next(
        _resolve_bind(volume)
        for volume in service["volumes"]
        if _resolve_bind(volume).get("target") == "/workspace/deployment_state"
    )
    state["updaterStateBind"] = state_bind
    request = json.loads(Path(args[-1]).read_text())
    state.setdefault("updaters", {})[name] = f"updater-{name}"
    state.setdefault("updaterInfo", {})[name] = {
        "owner": request["authored"]["owner"],
        "imageId": request["imageId"],
    }
    save_state(state)
    raise SystemExit(0)

state["orphanRemovalRequested"] = state.get("orphanRemovalRequested", False) or "--remove-orphans" in tail
up_services = selected_services(tail)
if PROXY_SERVICE in up_services and not any(
    PROXY_SERVICE in call["services"] for call in state.get("pullCalls", [])
):
    print("No such image: newly pinned infrastructure image", file=sys.stderr)
    raise SystemExit(1)
if "init-db" in up_services:
    config = json.loads(Path(args[args.index("-f") + 1]).read_text())
    volume = config["services"]["init-db"]["volumes"][0]
    state["initDbBind"] = volume
    # Model the daemon's mounted host checkout, not the worker's filesystem.
    if volume["source"] != state["daemonProjectDir"] + "/init_db":
        save_state(state)
        print("cannot open /app/init_db/init_db_entrypoint.sh: No such file", file=sys.stderr)
        raise SystemExit(2)
    completed = subprocess.run(
        ["sh", str(Path(state["localProjectDir"]) / "init_db/init_db_entrypoint.sh")],
        capture_output=True, text=True, check=False,
    )
    state["initDbExitCode"] = completed.returncode
    state["initDbOutput"] = completed.stdout
    save_state(state)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    raise SystemExit(completed.returncode)

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
        "ContainerName": "replay-temporal-worker-agent-runtime-1",
        "ID": TARGET_ID,
        "Repository": "ghcr.io/moonladderstudios/moonmind",
        "Service": AGENT_SERVICE,
        "Tag": "latest",
    },
    {
        "ID": "sha256:" + "c" * 64,
        "ContainerName": "replay-sandbox-egress-proxy-1",
        "Repository": "ubuntu/squid",
        "Service": PROXY_SERVICE,
        "Tag": "latest",
    },
    {
        "ID": "sha256:" + "d" * 64,
        "ContainerName": "replay-temporal-worker-deployment-control-1",
        "Repository": "ghcr.io/moonladderstudios/moonmind",
        "Service": RUNNER_SERVICE,
        "Tag": "latest",
    },
]
save_state(state)
raise SystemExit(0)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("windows_host", [False, True])
@pytest.mark.parametrize("explicit_defaults", [False, True])
async def test_deployment_update_reconciles_non_image_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_host: bool,
    explicit_defaults: bool,
) -> None:
    replay_id = "deployment-update-infrastructure-reconciliation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    requested_repository = manifest["requestedRepository"]
    requested_image = f"{requested_repository}:latest"
    windows_replay = (
        load_replay("deployment-update-windows-bind-source", "manifest.json")
        if windows_host
        else None
    )
    real_docker = shutil.which("docker")
    assert (
        real_docker is not None
    ), "reliability journey image must provide Docker Compose"

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
    if windows_replay:
        # Keep the real Compose parser in the journey; only the daemon is modeled.
        compose_text = compose_path.read_text(encoding="utf-8")
        compose_path.write_text(
            compose_text.replace(
                "networks:\n  restricted-egress-network:",
                "  init-db:\n"
                f"    image: {requested_image}\n"
                "    volumes:\n"
                "      - ./init_db:/app/init_db:ro\n"
                "    command: sh /app/init_db/init_db_entrypoint.sh\n"
                "networks:\n  restricted-egress-network:",
            ),
            encoding="utf-8",
        )
        (tmp_path / "init_db").mkdir()
        (tmp_path / "init_db/init_db_entrypoint.sh").write_text(
            "printf 'init-db completed\\n'\n", encoding="utf-8"
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
                "localProjectDir": str(tmp_path),
                "daemonProjectDir": (
                    windows_replay["daemonProjectDir"] if windows_replay else None
                ),
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
        project_dir=(
            windows_replay["hostProjectDir"] if windows_replay else str(tmp_path)
        ),
        local_project_dir=str(tmp_path) if windows_replay else None,
        project_name="moonmind-test-deployment-replay",
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
            **(
                {"mode": "changed_services", "removeOrphans": True, "wait": True}
                if explicit_defaults
                else {}
            ),
            "reason": "Replay production infrastructure reconciliation failure",
        },
        context={"deployment_runner_mode": "privileged_worker"},
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert bool(state.get("accessPreflightCalls", 0)) == state["orphanRemovalRequested"]
    assert state["composeConfigCalls"] >= 4
    assert state["pullCalls"][0]["services"] == expected["pullServices"] + (
        ["init-db"] if windows_replay else []
    )
    assert state["pullCalls"][1] == {
        "policy": "missing",
        "services": ["sandbox-egress-proxy"],
    }
    assert len(state["pullCalls"]) == 2
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
        all(service not in call["services"] for call in state["pullCalls"])
        and service not in state["upServices"]
        for service in expected["excludedServices"]
    )
    if windows_replay:
        assert state["initDbExitCode"] == 0
        assert state["initDbOutput"] == "init-db completed\n"
        assert state["initDbBind"]["source"] == windows_replay["expectedBindSource"]
        assert state["initDbBind"]["bind"]["create_host_path"] is False
        assert state["initDbBind"]["read_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "daemon_platform,evidence",
    [
        ("Docker Desktop", "installed"),
        ("Docker Desktop", "self"),
        ("Docker Desktop", "none"),
        ("Ubuntu 24.04", "none"),
    ],
)
async def test_wsl_updater_launch_uses_daemon_visible_state_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    daemon_platform: str,
    evidence: str,
) -> None:
    """Detached updater launches from a WSL checkout keep their state bind.

    Replay of the escaped `update-moonmind.sh` failure: the host submission
    wrote its request to the real deployment state, but the updater launched
    from inside the worker mounted a bind the daemon resolved somewhere else.
    The daemon mounted an empty directory, the updater exited with
    ``FileNotFoundError`` three times, and the caller exhausted three
    deliveries without terminal evidence. Which namespace serves a WSL
    checkout differs between Docker Desktop backends, so a host path the
    deployment demonstrably resolves decides when one is readable: its own
    installed containers first, then this worker's mount table, and only
    without either does the path's shape select a namespace. Following the
    worker's ephemeral mount ahead of the installed containers rewrote every
    bind source and recreated the whole project mid-release, including the
    socket proxy the updater itself talks to. The journey enters through the
    production ``launch_updater`` path with real Compose rendering; only the
    daemon is modeled.
    """
    import time

    from moonmind.workflows.skills.deployment_release import launch_updater

    replay_id = "deployment-update-wsl-updater-bind-source"
    manifest = load_replay(replay_id, "manifest.json")
    real_docker = shutil.which("docker")
    assert (
        real_docker is not None
    ), "reliability journey image must provide Docker Compose"

    leaf = "".join(
        part for part in tmp_path.name.replace("-", "_") if part.isalnum() or part == "_"
    )
    host_project_dir = f"{manifest['hostProjectDir']}-{leaf}"
    daemon_project_dir = f"{manifest['daemonProjectDir']}-{leaf}"
    requested_image = "ghcr.io/moonladderstudios/moonmind@sha256:" + "b" * 64

    (tmp_path / "deploy" / "state").mkdir(parents=True)
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text(
        json.dumps(
            {
                "services": {
                    "temporal-worker-deployment-control": {
                        "image": requested_image,
                        "volumes": [
                            "./deploy/state:/workspace/deployment_state:rw",
                            ".:/workspace/host_project:ro",
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    owner = "host-update:wsl-updater-bind-replay"
    record = {
        "authored": {
            "owner": owner,
            "inputs": {"stack": "moonmind"},
            "context": {"idempotency_key": owner},
        },
        "image": requested_image,
        "imageId": "sha256:" + "c" * 64,
        "deadline": time.time() + 300,
    }
    directory = tmp_path / "release-job"
    directory.mkdir()
    (directory / "request.json").write_text(
        json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
    )

    state_path = tmp_path / "engine-state.json"
    state_path.write_text(
        json.dumps(
            {
                "fakeOperatingSystem": daemon_platform,
                "updaters": {},
                "updaterInfo": {},
                # Docker Desktop serves this checkout at the WSL path itself.
                # The share spelling below is the second path it records for
                # the same directory, which the installed project must win over.
                "selfMounts": (
                    [{"Source": host_project_dir, "Destination": str(tmp_path)}]
                    if evidence == "self"
                    else [
                        {
                            "Source": f"/run/desktop/mnt/host/wsl/share{leaf}",
                            "Destination": str(tmp_path),
                        }
                    ]
                    if evidence == "installed"
                    else []
                ),
                "installedContainers": (
                    {
                        "installed-worker": [
                            {"Source": host_project_dir, "Destination": str(tmp_path)}
                        ]
                    }
                    if evidence == "installed"
                    else {}
                ),
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
    monkeypatch.setenv("HOSTNAME", "replay-deployment-worker")
    monkeypatch.setenv("MM_DEPLOYMENT_REPLAY_STATE", str(state_path))
    monkeypatch.setenv("MM_DEPLOYMENT_REPLAY_REAL_DOCKER", real_docker)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    # Isolate the daemon-platform probe cache per test through a distinct
    # DOCKER_HOST key: the fake `docker info` above answers per platform.
    monkeypatch.setenv("DOCKER_HOST", f"replay-wsl-updater-{leaf}")
    # The read alias (host project dir symlinked into the worker) cannot be
    # created on CI runners, where /mnt is root-owned, and it is orthogonal
    # to the daemon-observed bind asserted here: the rewrite path renders
    # from the local checkout, and the direct path never reaches a real
    # daemon in this journey. Alias creation stays covered by its own unit.
    monkeypatch.setattr(
        HostDockerComposeRunner,
        "_ensure_host_project_read_alias",
        lambda self: None,
    )

    runner = HostDockerComposeRunner(
        project_dir=host_project_dir,
        local_project_dir=str(tmp_path),
        project_name="moonmind-test-wsl-updater",
    )
    observed = await launch_updater(runner, directory, record)
    assert observed["Image"] == record["imageId"]

    state = json.loads(state_path.read_text(encoding="utf-8"))
    recorded = state["updaterStateBind"]
    if evidence in {"installed", "self"}:
        # The daemon resolved this checkout at the WSL path, so the updater
        # inherits it instead of a guessed namespace — and in the "installed"
        # case it keeps the spelling the running project already uses, which
        # is what stops a whole-project recreate mid-release.
        assert recorded["source"] == host_project_dir + manifest[
            "expectedStateBindSourceSuffix"
        ]
    elif daemon_platform == "Docker Desktop":
        # No evidence: an unconfirmed Desktop namespace is the only signal.
        assert recorded["source"] == daemon_project_dir + manifest[
            "expectedStateBindSourceSuffix"
        ]
        assert recorded["bind"]["create_host_path"] is False
    else:
        # A confirmed native Linux daemon serves the POSIX mount itself.
        assert recorded["source"] == host_project_dir + manifest[
            "expectedStateBindSourceSuffix"
        ]
