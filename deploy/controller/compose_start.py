"""Initialize a Compose-owned controller, then serve with the existing owner.

Docker's observed mounts and project labels, not the shell's PWD, establish
identity. A prior standalone owner is retired only under the shared stack
lock and only after its state bind is verified. No deployment work is run here.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import bootstrap
import lock as lock_mod
import record as record_mod

STATE_TARGET = "/var/lib/moonmind-controller"
REPO_TARGET = "/deployment"


def docker_command(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=45
    )
    if result.returncode:
        # Inspection output can contain deployment credentials; never echo it.
        raise RuntimeError(
            f"Controller startup Docker {args[0]} failed (exit {result.returncode})"
        )
    return result.stdout


def bind_source(container: dict, target: str) -> str:
    matches = [
        m["Source"]
        for m in container.get("Mounts", [])
        if m.get("Type") == "bind" and m.get("Destination") == target
    ]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError(
            f"Controller startup requires one observed state bind or repository bind at {target}"
        )
    return str(matches[0])


def _retire_legacy(
    state_dir: Path, self_row: dict, old: dict, state_source: str, docker
) -> None:
    project = old.get("project")
    own_project = self_row["Config"]["Labels"]["com.docker.compose.project"]
    if not project:
        # Lost metadata must not create a second owner of the same state.
        candidates = docker(
            "ps", "-aq", "--filter", "label=moonmind.controller.managed=true"
        ).split()
        matches = []
        for identifier in candidates:
            if identifier == self_row.get("Id"):
                continue
            rows = json.loads(docker("inspect", identifier))
            for row in rows:
                bindings = [
                    m.get("Source")
                    for m in row.get("Mounts", [])
                    if m.get("Type") == "bind" and m.get("Destination") == STATE_TARGET
                ]
                labels = (row.get("Config") or {}).get("Labels") or {}
                if (
                    bindings == [state_source]
                    and labels.get("com.docker.compose.project") != own_project
                ):
                    matches.append(labels.get("com.docker.compose.project"))
        if len(matches) > 1:
            raise RuntimeError(
                "Controller migration refuses ambiguous prior state owners"
            )
        project = matches[0] if matches else None
    if not project or project == own_project:
        return
    ids = docker(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        "label=com.docker.compose.service=controller",
    ).split()
    if not ids:
        return
    if len(ids) != 1:
        raise RuntimeError("Controller migration refuses ambiguous prior owners")
    rows = json.loads(docker("inspect", ids[0]))
    if len(rows) != 1:
        raise RuntimeError("Controller migration cannot inspect the prior owner")
    prior = rows[0]
    labels = (prior.get("Config") or {}).get("Labels") or {}
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != "controller"
        or labels.get("moonmind.controller.managed") != "true"
        or bind_source(prior, STATE_TARGET) != state_source
    ):
        raise RuntimeError(
            "Controller migration refuses an unverified prior state bind"
        )
    if (prior.get("State") or {}).get("Running") and record_mod.OperationStore(
        state_dir
    ).list_open():
        raise RuntimeError(
            "Prior controller still has open deployment work; startup will retry"
        )
    # The caller holds the stack lock. An idle old API cannot admit work
    # between our check and stop. No volume, image, or operation is deleted.
    docker("stop", "--time", "30", ids[0])
    docker("rm", ids[0])


def initialize(state_dir: Path, container: dict, *, docker=docker_command) -> dict:
    labels = (container.get("Config") or {}).get("Labels") or {}
    project = labels.get("com.docker.compose.project")
    if (
        not project
        or labels.get("com.docker.compose.service") != "controller"
        or labels.get("moonmind.controller.managed") != "true"
    ):
        raise RuntimeError("Controller startup requires its Compose service identity")
    repo_source = bind_source(container, REPO_TARGET)
    state_source = bind_source(container, STATE_TARGET)
    networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
    links = [
        name
        for name, value in networks.items()
        if bootstrap.CONTROLLER_ALIAS in (value.get("Aliases") or [])
    ]
    if len(links) != 1:
        raise RuntimeError(
            "Controller startup requires one private controller network alias"
        )
    ports = ((container.get("NetworkSettings") or {}).get("Ports") or {}).get(
        "8472/tcp"
    ) or []
    if len(ports) != 1 or ports[0].get("HostIp") != "127.0.0.1":
        raise RuntimeError(
            "Controller startup requires one loopback-only published endpoint"
        )
    host_port = int(ports[0]["HostPort"])
    image = str(container.get("Image") or "")
    if not image.startswith("sha256:"):
        raise RuntimeError("Controller startup could not observe its immutable image")
    with lock_mod.StackLock(state_dir, "moonmind").acquire():
        old = bootstrap.load_identity(state_dir) or {}
        _retire_legacy(state_dir, container, old, state_source, docker)
        bootstrap.ensure_secret(state_dir)
        identity = {
            "project": project,
            "targetProject": project,
            "repo": repo_source,
            "port": 8472,
            "hostPort": host_port,
            "targetNetwork": links[0],
        }
        # The generated manifest retains the same Compose project/service for
        # out-of-process handoff. App apply never replaces this service.
        bootstrap.render_compose_file(
            state_dir=state_dir,
            repo=Path(repo_source),
            image=image,
            port=8472,
            project=project,
            target_project=project,
            target_network=links[0],
            state_source=state_source,
            repo_source=repo_source,
            host_port=host_port,
        )
        record_mod._atomic_write_json(state_dir / "controller-identity.json", identity)
        os.chmod(state_dir / "controller-identity.json", 0o644)
        bootstrap.record_controller_image(
            state_dir,
            requested=str(container["Config"].get("Image") or image),
            pinned=image,
        )
    return identity


def expose_repository(host_path: str) -> None:
    """Expose the observed host path to existing Compose/helper code.

    The daemon uses the original bind source while this process reads the
    same files via /deployment. No host filesystem path is created here.
    """
    target = Path(host_path)
    if target.exists():
        if not target.samefile(REPO_TARGET):
            raise RuntimeError("Observed repository path collides with container files")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(REPO_TARGET, target_is_directory=True)


def prepare_host_access(state_dir: Path, repository: Path) -> None:
    """Share only startup metadata with the observed checkout owner.

    The checked-in deployment-state directories belong to the checkout owner.
    New bootstrap files must remain readable/writable by the normal host
    updater, without exposing the bearer or changing retained operation/data files.
    """
    owner = repository.stat()
    if os.geteuid() != 0 or owner.st_uid == 0:
        return
    for path in (
        state_dir,
        state_dir / "secrets",
        state_dir / "secrets/controller-bearer",
        state_dir / "controller-image.json",
        state_dir / "controller-compose.yaml",
        state_dir / "controller-identity.json",
    ):
        if path.stat().st_uid == 0:
            os.chown(path, owner.st_uid, owner.st_gid)
    os.chmod(state_dir / "secrets/controller-bearer", 0o600)


def main() -> int:
    rows = json.loads(docker_command("inspect", os.environ["HOSTNAME"]))
    if len(rows) != 1:
        raise RuntimeError("Controller startup cannot identify its container")
    expose_repository(bind_source(rows[0], REPO_TARGET))
    state_dir = Path(os.environ.get("MOONMIND_CONTROLLER_STATE_DIR", STATE_TARGET))
    identity = initialize(state_dir, rows[0])
    prepare_host_access(state_dir, Path(REPO_TARGET))
    os.environ["MOONMIND_CONTROLLER_TARGET_REPO"] = identity["repo"]
    os.environ["MOONMIND_CONTROLLER_TARGET_PROJECT"] = identity["targetProject"]
    import server

    return server.main([])


if __name__ == "__main__":
    raise SystemExit(main())
