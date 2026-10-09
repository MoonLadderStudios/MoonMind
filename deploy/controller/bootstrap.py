"""Host-owned lifecycle for the standalone controller (issue #4500, REQ-02).

The host CLI installs, starts, updates, and restores the controller. The
controller never replaces itself: this CLI refuses to run inside the
controller container, and controller updates are serialized against active
deployment mutation. Stdlib-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lock as lock_mod
import mounts as mounts_mod
import record as record_mod

CONTROLLER_PROJECT = "moonmind-controller"
CONTROLLER_SERVICE = "controller"
DEFAULT_PORT = 8472
DEFAULT_IMAGE = os.environ.get(
    "MOONMIND_CONTROLLER_IMAGE",
    "ghcr.io/moonladderstudios/moonmind-controller:latest",
)
# Derived identity spreads independent deployments across stable project
# names and loopback ports instead of colliding on one shared name/port.
DERIVED_PORT_RANGE = 100
# The API reaches the controller over one private network owned by this
# deployment: the MoonMind Compose file joins the API to it under the same
# setting, and the controller joins it under a stable alias. No agent
# service is attached, and the host keeps its loopback endpoint.
TARGET_NETWORK_SETTING = "MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK"
TARGET_NETWORK_KEY = "deployment-controller-network"
CONTROLLER_ALIAS = "moonmind-controller"
JOURNAL_TRANSITION_CAPABILITY = "active-journal-transition"


# The standalone controller image repository. Any other image is the MoonMind
# application image, which ships this controller under /app/deploy/controller
# (the standalone image is not published yet, MoonLadderStudios/MoonMind#4500).
STANDALONE_CONTROLLER_REPOSITORY = "ghcr.io/moonladderstudios/moonmind-controller"
APPLICATION_CONTROLLER_ENTRYPOINT = '["python", "/app/deploy/controller/server.py"]'
# The standalone image runs as root; an application-image controller keeps
# the same Docker-socket authority instead of the application's own user.
APPLICATION_CONTROLLER_USER = "0:0"


def controller_capabilities(url: str, secret: str) -> set[str]:
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/healthz",
        headers={"Authorization": f"Bearer {secret}"},
    )
    # The deployment-owned bearer goes only to the controller endpoint, never
    # through an ambient HTTP(S) proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        body = json.load(response)
    return set(body.get("capabilities") or ())


def _checked_capture(command: list[str]) -> str:
    result = _run_capture(command)
    if result.returncode:
        raise RuntimeError(
            f"Controller prerequisite {command[1]} failed (exit {result.returncode})."
        )
    return result.stdout or ""


def _application_controller_image(target_project: str, requested: str) -> str:
    """Use an observed installed source on rollback, else the staged target.

    The application already publishes these immutable image contents. No
    separate controller publication or release-version equality is needed.
    The probe runs with no network, credentials, host mounts, or Docker socket.
    """
    ids = _checked_capture(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={target_project}",
        ]
    ).split()
    candidates = []
    if ids:
        containers = json.loads(_checked_capture(["docker", "inspect", *ids]))
        for container in containers:
            service = ((container.get("Config") or {}).get("Labels") or {}).get(
                "com.docker.compose.service"
            )
            if service in {
                "api",
                "temporal-worker-agent-runtime",
                "temporal-worker-deployment-control",
            }:
                image = container.get("Image")
                if image and image not in candidates:
                    candidates.append(image)
    probe = (
        "import pathlib,sys; p=pathlib.Path('/app/deploy/controller'); "
        "sys.exit(10) if not (p/'server.py').is_file() else None; "
        "sys.path.insert(0,str(p)); import server; "
        f"sys.exit(0 if {JOURNAL_TRANSITION_CAPABILITY!r} in "
        "getattr(server,'CONTROLLER_CAPABILITIES',()) else 10)"
    )
    for image in [*candidates, None]:
        if image is None:
            _checked_capture(["docker", "pull", requested])
            image = _checked_capture(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    requested,
                ]
            ).strip()
        result = _run_capture(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--entrypoint",
                "python",
                image,
                "-c",
                probe,
            ]
        )
        if result.returncode == 0:
            return image
        if result.returncode != 10:
            raise RuntimeError(
                f"Controller prerequisite image probe failed (exit {result.returncode})."
            )
    raise RuntimeError(
        "Neither the installed source nor requested image supplies journal transition support."
    )


def cmd_ensure(args, env) -> int:
    """Refresh an old controller before submission, outside its operation.

    Host and the already-privileged deployment-control submitter use the
    same lifecycle owner. Reuse its generated Compose file so daemon host
    paths, networks, identity and authority never change in an app container.
    """
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    identity = load_identity(state_dir)
    compose_file = state_dir / "controller-compose.yaml"
    if not identity or not compose_file.is_file():
        raise RuntimeError(
            "The controller prerequisite requires its existing installation."
        )
    secret = _secret_path(state_dir).read_text(encoding="utf-8").strip()
    url = args.controller_url or f"http://127.0.0.1:{identity['port']}"

    def supported():
        try:
            return JOURNAL_TRANSITION_CAPABILITY in controller_capabilities(url, secret)
        except urllib.error.HTTPError:
            raise
        except OSError:
            # An interrupted recreation may have stopped the old process.
            # Durable operation state and the kernel lock still decide
            # whether host-owned repair may proceed; never create a writer.
            return False

    if supported():
        return 0
    if _open_operations(state_dir, args.stack):
        raise ActiveOperationError(
            "Controller prerequisite waits for the current deployment operation to finish."
        )
    with lock_mod.StackLock(state_dir, args.stack).acquire():
        # Recheck after exclusion: another submission can record intent while
        # bootstrap waits for the same lock. Never replace its live owner.
        if _open_operations(state_dir, args.stack):
            raise ActiveOperationError(
                "Controller prerequisite waits for the current deployment operation to finish."
            )
        if supported():
            return 0
        image = _application_controller_image(
            str(identity.get("targetProject") or args.target_project),
            args.image,
        )
        lines = compose_file.read_text(encoding="utf-8").splitlines()
        image_lines = [
            index for index, line in enumerate(lines) if line.startswith("    image:")
        ]
        if len(image_lines) != 1:
            raise RuntimeError(
                "Controller Compose project has no unique controller image."
            )
        lines = [line for line in lines if not line.startswith("    entrypoint:")]
        index = next(
            index for index, line in enumerate(lines) if line.startswith("    image:")
        )
        replacement = [
            f"    image: {image}",
            '    entrypoint: ["python", "/app/deploy/controller/server.py"]',
        ]
        if not any(line.startswith("    user:") for line in lines):
            # The standalone image defaults to root; the application image
            # defaults to app. Preserve the installed process identity rather
            # than losing secret/socket access or expanding a custom identity.
            previous_image = lines[index].partition(":")[2].strip().strip("\"'")
            previous_user = json.loads(_checked_capture([
                "docker", "image", "inspect", "--format", "{{json .Config.User}}",
                previous_image,
            ]))
            if not isinstance(previous_user, str):
                raise RuntimeError("Installed controller process identity is unavailable")
            replacement.append(f"    user: {json.dumps(previous_user or '0:0')}")
        lines[index : index + 1] = replacement
        temporary = compose_file.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, compose_file)
        record_controller_image(state_dir, requested=args.image, pinned=image)
        code = _compose(
            state_dir,
            identity["project"],
            "up",
            "-d",
            "--pull",
            "never",
            "--wait",
            CONTROLLER_SERVICE,
        )
        if code:
            raise RuntimeError(
                f"Controller prerequisite recreation failed (exit {code})."
            )
        deadline = time.monotonic() + 30
        while True:
            try:
                if JOURNAL_TRANSITION_CAPABILITY in controller_capabilities(
                    url, secret
                ):
                    return 0
            except OSError:
                # The recreated controller may not accept connections yet;
                # keep polling within the existing readiness deadline.
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Controller prerequisite did not expose journal transition support."
                )
            time.sleep(1)


class InsideControllerError(RuntimeError):
    """Refusing to manage the controller from inside itself."""


class ActiveOperationError(RuntimeError):
    """Refusing controller mutation while a deployment operation is open."""


def _ensure_outside_controller(env) -> None:
    if (env or {}).get("MOONMIND_CONTROLLER_MANAGED") == "1":
        raise InsideControllerError(
            "Controller update is host-owned; refusing to run inside the "
            "controller container (it never replaces itself)."
        )


def _repo_fingerprint(repo: Path) -> str:
    return hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()


def project_for_repo(repo: Path) -> str:
    """Derive a stable per-deployment Compose project name.

    Independent checkouts must not reconcile each other's controller
    container: each deployment owns a project derived from its own path.
    """
    return f"{CONTROLLER_PROJECT}-{_repo_fingerprint(repo)[:12]}"


def port_for_repo(repo: Path, base: int = DEFAULT_PORT) -> int:
    """Derive a stable per-deployment loopback port with explicit override.

    Pass ``--port`` (or ``MOONMIND_CONTROLLER_PORT``) to pin an endpoint;
    otherwise each deployment spreads across ``base..base+range`` by path.
    """
    offset = int(_repo_fingerprint(repo)[12:16], 16) % DERIVED_PORT_RANGE
    return base + offset


def _identity_path(state_dir: Path) -> Path:
    return state_dir / "controller-identity.json"


def load_identity(state_dir: Path) -> dict | None:
    """Return the persisted controller identity, if install recorded one."""
    path = _identity_path(state_dir)
    if not path.is_file():
        return None
    try:
        identity = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(identity, dict) or not identity.get("project"):
        return None
    return identity


def _repo_env_value(repo: Path, key: str) -> str:
    """Read one setting from the deployment-owned ``.env`` (no interpolation)."""
    try:
        content = (repo / ".env").read_text(encoding="utf-8")
    except OSError:
        return ""
    value = ""
    for line in content.splitlines():
        name, separator, raw = line.strip().partition("=")
        if separator and name.strip() == key:
            value = raw.strip().strip("'\"").strip()
    return value


def target_network_for_repo(repo: Path, env=None, project: str | None = None) -> str:
    """Resolve the API link network the way Compose interpolates it.

    An explicit setting wins; otherwise the name follows the MoonMind Compose
    project, so independent deployments never share the controller alias.
    """
    return (
        str((env or {}).get(TARGET_NETWORK_SETTING) or "").strip()
        or _repo_env_value(repo, TARGET_NETWORK_SETTING)
        or f"{project or target_project_for_repo(repo, env)}_{TARGET_NETWORK_KEY}"
    )


def target_project_for_repo(repo: Path, env=None) -> str:
    """Resolve the MoonMind Compose project name the way Compose derives it."""
    explicit = str((env or {}).get("COMPOSE_PROJECT_NAME") or "").strip() or _repo_env_value(
        repo, "COMPOSE_PROJECT_NAME"
    )
    name = explicit or repo.name
    normalized = "".join(
        char for char in name.lower() if char.isalnum() or char in "-_"
    )
    return normalized or "moonmind"


def ensure_target_network(network: str, project: str) -> None:
    """Create the API link network when the MoonMind stack has not yet.

    The network carries the Compose labels of the MoonMind project so a later
    ``docker compose up`` adopts it instead of warning. Because the
    controller stays attached, a target-project shutdown leaves the network
    (and the controller endpoint) in place.
    """
    try:
        inspected = _run_capture(["docker", "network", "inspect", network])
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Cannot inspect Docker network {network!r}: {exc}") from exc
    if inspected.returncode == 0:
        return
    created = _run_capture(
        [
            "docker",
            "network",
            "create",
            "--internal",
            "--label",
            f"com.docker.compose.project={project}",
            "--label",
            f"com.docker.compose.network={TARGET_NETWORK_KEY}",
            network,
        ]
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"Cannot create Docker network {network!r}: "
            f"{(created.stderr or created.stdout or '').strip()[-500:]}"
        )


def ensure_identity(
    state_dir: Path,
    repo: Path,
    port: int | None,
    *,
    target_network: str | None = None,
    target_project: str | None = None,
) -> dict:
    """Persist (or reuse) this deployment's controller project and endpoint.

    The first install derives and records the identity; later commands reuse
    the recorded project and port so a deployment keeps its endpoint across
    restarts instead of drifting when the checkout path changes case or
    symlink shape. An explicit ``--port`` always wins for the endpoint. The
    API link network and MoonMind project are recorded too, so the API can
    derive the endpoint and later commands keep the same link.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    existing = load_identity(state_dir)
    project = (existing or {}).get("project") or project_for_repo(repo)
    resolved_port = port if port is not None else (existing or {}).get("port") or port_for_repo(repo)
    identity = {
        "project": project,
        "port": int(resolved_port),
        "repo": str(repo),
        "recordedAt": int(time.time()),
    }
    network = target_network or (existing or {}).get("targetNetwork")
    if network:
        identity["targetNetwork"] = str(network)
        identity["targetProject"] = str(
            target_project
            or (existing or {}).get("targetProject")
            or target_project_for_repo(repo)
        )
    _identity_path(state_dir).write_text(
        json.dumps(identity, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return identity


def _port_in_use(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(2)
        return probe.connect_ex(("127.0.0.1", int(port))) == 0
    except OSError:
        return False
    finally:
        probe.close()


class ImageResolutionError(RuntimeError):
    """The privileged controller image could not be pinned to a digest."""


def split_image_reference(image: str) -> tuple[str, str | None, str | None]:
    """Split an image reference into (repository, tag, digest)."""
    text = (image or "").strip()
    digest = None
    if "@" in text:
        text, _, digest = text.partition("@")
    tag = "latest"
    if ":" in text:
        maybe_repo, _, maybe_tag = text.rpartition(":")
        if "/" not in maybe_tag:
            text, tag = maybe_repo, maybe_tag or "latest"
    return text, tag, digest


def _run_capture(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)


def resolve_image_digest(image: str) -> str:
    """Resolve an image reference to its immutable ``sha256:...`` digest.

    An already-pinned ``repo@sha256:<hex>`` reference is returned as-is.
    Otherwise the registry manifest is inspected (no pull, no container) and
    the digest is computed over the raw manifest bytes. Resolution failure
    raises :class:`ImageResolutionError`: the privileged controller is never
    started from an unverified mutable tag.
    """
    _, _, pinned = split_image_reference(image)
    if pinned:
        digest = pinned if pinned.startswith("sha256:") else f"sha256:{pinned}"
        hexpart = digest.partition(":")[2]
        if len(hexpart) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
            raise ImageResolutionError(f"Refusing malformed pinned digest: {image!r}")
        return digest.lower()
    try:
        completed = _run_capture(
            ["docker", "buildx", "imagetools", "inspect", "--raw", image]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ImageResolutionError(
            f"Cannot resolve controller image {image!r} "
            f"(container tooling unavailable: {exc})"
        ) from exc
    if completed.returncode != 0:
        raise ImageResolutionError(
            f"Cannot resolve controller image {image!r}: "
            f"{(completed.stderr or completed.stdout or '').strip()[-500:]}"
        )
    raw = (completed.stdout or "").encode("utf-8")
    if not raw.strip():
        raise ImageResolutionError(
            f"Cannot resolve controller image {image!r}: empty manifest"
        )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _image_record_path(state_dir: Path) -> Path:
    return state_dir / "controller-image.json"


def record_controller_image(state_dir: Path, *, requested: str, pinned: str | None) -> dict:
    """Persist the resolved privileged image before the controller starts."""
    record = {
        "requested": requested,
        "pinned": pinned,
        "verified": bool(pinned),
        "resolvedAt": int(time.time()),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    _image_record_path(state_dir).write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return record


def load_controller_image(state_dir: Path) -> dict | None:
    path = _image_record_path(state_dir)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def pinned_controller_image(state_dir: Path, requested: str) -> tuple[str, dict]:
    """Resolve and persist the immutable image; fall back recorded-unverified.

    Install/update records the resolution attempt either way. Starting the
    privileged container requires a verified digest (see ``cmd_start``),
    so an offline install renders but refuses to launch until the image is
    pinned through a later online ``update``.
    """
    try:
        digest = resolve_image_digest(requested)
    except ImageResolutionError as exc:
        print(f"WARNING: {exc}", flush=True)
        record = record_controller_image(state_dir, requested=requested, pinned=None)
        return requested, record
    repository, _, _ = split_image_reference(requested)
    pinned = f"{repository}@{digest}"
    record = record_controller_image(state_dir, requested=requested, pinned=pinned)
    return pinned, record


def require_verified_image(state_dir: Path, requested: str) -> str:
    """Return the pinned image or refuse to start an unverified controller."""
    record = load_controller_image(state_dir)
    pinned = (record or {}).get("pinned") if record else None
    if pinned and (record or {}).get("verified"):
        return str(pinned)
    raise ImageResolutionError(
        "Refusing to start the privileged controller from an unverified "
        f"mutable tag ({requested!r}); re-run install/update with registry "
        "access so the image resolves to a digest first."
    )


def _secret_path(state_dir: Path) -> Path:
    return state_dir / "secrets" / "controller-bearer"


def ensure_secret(state_dir: Path) -> Path:
    """Create the deployment-owned bearer secret once; never overwrite."""
    path = _secret_path(state_dir)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(secret + "\n")
    except BaseException:
        with _suppress():
            path.unlink()
        raise
    os.chmod(path, 0o600)
    return path


class _Suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def _suppress():
    return _Suppress()


def render_compose_file(
    *,
    state_dir: Path,
    repo: Path,
    image: str = DEFAULT_IMAGE,
    port: int = DEFAULT_PORT,
    project: str | None = None,
    target_network: str | None = None,
    target_project: str | None = None,
) -> Path:
    """Render the separate controller Compose project (REQ-02).

    Bind sources are resolved through the daemon-visible mount adapter so
    the Windows/WSL Docker Desktop boundary receives daemon-namespace
    paths (``/run/desktop/mnt/host/<drive>/...``) instead of missing local
    ``/mnt/<drive>`` mounts. A missing required host source fails fast
    instead of becoming an auto-created empty directory. The Compose
    project name is this deployment's stable identity (see
    :func:`project_for_repo`), never a shared global.
    """
    state_src = mounts_mod.resolve_bind_source(str(state_dir))
    repo_src = mounts_mod.resolve_bind_source(str(repo))
    project_name = project or project_for_repo(repo)
    process = ""
    if is_application_image(image):
        process = (
            f"    entrypoint: {APPLICATION_CONTROLLER_ENTRYPOINT}\n"
            f"    user: {json.dumps(APPLICATION_CONTROLLER_USER)}\n"
        )
    path = state_dir / "controller-compose.yaml"
    # No-target submissions update the project bootstrap recorded, so a
    # `-p` deployment is never addressed as a parallel default project.
    target_project_env = (
        f'      MOONMIND_CONTROLLER_TARGET_PROJECT: "{target_project}"\n'
        if target_project
        else ""
    )
    service_networks = ""
    project_networks = ""
    if target_network:
        service_networks = f"""    networks:
      default: {{}}
      moonmind:
        aliases:
          - {CONTROLLER_ALIAS}
"""
        project_networks = f"""networks:
  moonmind:
    name: {target_network}
    external: true
"""
    content = f"""# MoonMind standalone deployment controller (issue #4500).
# Separate Compose project: own durable state, restart policy, and direct
# Docker socket mount. Its transport and endpoint survive target-project
# shutdown. Managed by deploy/controller/bootstrap.py on the host; the
# controller never replaces itself.
name: {project_name}
services:
  {CONTROLLER_SERVICE}:
    image: {image}
{process}    restart: unless-stopped
    environment:
      MOONMIND_CONTROLLER_MANAGED: "1"
      MOONMIND_CONTROLLER_STATE_DIR: /var/lib/moonmind-controller
      MOONMIND_CONTROLLER_PORT: "{port}"
      MOONMIND_CONTROLLER_SECRET_FILE: /var/lib/moonmind-controller/secrets/controller-bearer
      MOONMIND_CONTROLLER_TARGET_REPO: "{repo}"
{target_project_env}    ports:
      - "127.0.0.1:{port}:{port}"
    volumes:
      - {state_src}:/var/lib/moonmind-controller
      - {repo_src}:{repo}:ro
      - /var/run/docker.sock:/var/run/docker.sock
    labels:
      moonmind.controller.managed: "true"
{service_networks}{project_networks}"""
    path.write_text(content, encoding="utf-8")
    return path


def is_application_image(image: str) -> bool:
    """Whether ``image`` is the MoonMind application image, not the standalone one.

    The configured default controller image (``MOONMIND_CONTROLLER_IMAGE``)
    is treated as standalone too.
    """
    repository, _, _ = split_image_reference(image)
    standalone = {
        STANDALONE_CONTROLLER_REPOSITORY,
        split_image_reference(DEFAULT_IMAGE)[0],
    }
    return repository not in standalone


def _compose(state_dir: Path, project: str, *args: str) -> int:
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(state_dir / "controller-compose.yaml"),
        *args,
    ]
    return subprocess.run(command, check=False).returncode


def _project_for_state(state_dir: Path, repo: Path) -> str:
    identity = load_identity(state_dir)
    if identity:
        return str(identity["project"])
    return project_for_repo(repo)


def _ensure_identity_network(identity: dict | None) -> None:
    network = (identity or {}).get("targetNetwork")
    if network:
        ensure_target_network(
            str(network), str((identity or {}).get("targetProject") or "moonmind")
        )


def cmd_install(args, env) -> int:
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    identity = ensure_identity(
        state_dir,
        repo,
        getattr(args, "port", None),
        target_network=args.target_network,
        target_project=args.target_project,
    )
    if _port_in_use(identity["port"]) and not load_controller_image(state_dir):
        raise RuntimeError(
            f"Port {identity['port']} is already in use by another endpoint; "
            "pass --port to pin this deployment's controller endpoint."
        )
    pinned, _ = pinned_controller_image(state_dir, args.image)
    compose_file = render_compose_file(
        state_dir=state_dir,
        repo=repo,
        image=pinned,
        port=identity["port"],
        project=identity["project"],
        target_network=identity.get("targetNetwork"),
        target_project=identity.get("targetProject"),
    )
    print(f"Controller project rendered: {compose_file}", flush=True)
    print(f"Deployment-owned secret: {_secret_path(state_dir)}", flush=True)
    print(
        "Start with: "
        f"python3 {Path(__file__).name} start --state-dir {state_dir}",
        flush=True,
    )
    return 0


def cmd_start(args, env) -> int:
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    compose_file = state_dir / "controller-compose.yaml"
    if not compose_file.exists():
        raise RuntimeError(
            f"Controller project is not installed; run install first: {compose_file}"
        )
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    project = _project_for_state(state_dir, repo)
    # The privileged container only starts from a verified digest.
    require_verified_image(state_dir, args.image)
    _ensure_identity_network(load_identity(state_dir))
    code = _compose(state_dir, project, "up", "-d", "--wait")
    if code != 0:
        raise RuntimeError(f"Controller start failed (exit {code}).")
    print("Controller is running.", flush=True)
    return 0


def _open_operations(state_dir: Path, stack: str) -> list:
    store = record_mod.OperationStore(state_dir)
    return store.list_open(stack=stack)


def cmd_update(args, env) -> int:
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    stack = args.stack
    open_operations = _open_operations(state_dir, stack)
    if open_operations:
        raise ActiveOperationError(
            f"Refusing controller update while {len(open_operations)} deployment "
            f"operation(s) on stack {stack!r} are open; controller update is "
            "serialized against active deployment mutation."
        )
    # Controller replacement and stack mutation share one atomic exclusion
    # boundary: the target stack's lock, the same lock submissions hold
    # across pull/apply. A submission that begins after the open-operation
    # check above still blocks here instead of racing the recreation.
    lock_candidate = lock_mod.StackLock(state_dir, stack)
    with lock_candidate.acquire():
        pinned, _ = pinned_controller_image(state_dir, args.image)
        repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
        identity = ensure_identity(
            state_dir,
            repo,
            getattr(args, "port", None),
            target_network=args.target_network,
            target_project=args.target_project,
        )
        render_compose_file(
            state_dir=state_dir,
            repo=repo,
            image=pinned,
            port=identity["port"],
            project=identity["project"],
            target_network=identity.get("targetNetwork"),
            target_project=identity.get("targetProject"),
        )
        _ensure_identity_network(identity)
        code = _compose(state_dir, identity["project"], "pull", CONTROLLER_SERVICE)
        if code != 0:
            raise RuntimeError(f"Controller image pull failed (exit {code}).")
        code = _compose(
            state_dir, identity["project"], "up", "-d", "--wait", CONTROLLER_SERVICE
        )
        if code != 0:
            raise RuntimeError(f"Controller recreation failed (exit {code}).")
    print("Controller updated.", flush=True)
    return 0


def cmd_restore(args, env) -> int:
    """Restore a missing/broken controller without touching deployment state."""
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    pinned, _ = pinned_controller_image(state_dir, args.image)
    identity = ensure_identity(
        state_dir,
        repo,
        getattr(args, "port", None),
        target_network=args.target_network,
        target_project=args.target_project,
    )
    render_compose_file(
        state_dir=state_dir,
        repo=repo,
        image=pinned,
        port=identity["port"],
        project=identity["project"],
        target_network=identity.get("targetNetwork"),
        target_project=identity.get("targetProject"),
    )
    _ensure_identity_network(identity)
    code = _compose(state_dir, identity["project"], "up", "-d", "--wait", CONTROLLER_SERVICE)
    if code != 0:
        raise RuntimeError(f"Controller restore failed (exit {code}).")
    print("Controller restored independently of MoonMind health.", flush=True)
    return 0


def cmd_status(args, env) -> int:
    state_dir = Path(args.state_dir).resolve()
    compose_file = state_dir / "controller-compose.yaml"
    secret_file = _secret_path(state_dir)
    print(f"compose file: {compose_file} {'present' if compose_file.exists() else 'missing'}", flush=True)
    print(f"secret: {secret_file} {'present' if secret_file.exists() else 'missing'}", flush=True)
    store = record_mod.OperationStore(state_dir)
    open_operations = store.list_open()
    print(f"open operations: {len(open_operations)}", flush=True)
    for operation in open_operations:
        print(
            f"  {operation['operationId']} stack={operation.get('stack')} "
            f"status={operation.get('status')}",
            flush=True,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=False, help="Controller state directory.")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", required=False, help="Controller state directory.")
    for name in ("install", "start", "update", "restore", "status", "ensure"):
        child = sub.add_parser(name, parents=[common])
        child.add_argument("--repo", default=None, help="Target MoonMind checkout.")
        child.add_argument("--stack", default="moonmind", help="Target stack.")
        child.add_argument(
            "--image",
            default=None,
            help=(
                "Controller image (default: the image this installation "
                f"recorded, else {DEFAULT_IMAGE})."
            ),
        )
        child.add_argument("--port", type=int, default=DEFAULT_PORT)
        child.add_argument("--controller-url", default=None)
        child.add_argument(
            "--target-network",
            default=None,
            help=(
                f"API link network (default ${TARGET_NETWORK_SETTING} or "
                f"<target project>_{TARGET_NETWORK_KEY})."
            ),
        )
        child.add_argument(
            "--target-project",
            default=None,
            help="MoonMind Compose project that owns the API link network.",
        )
    return parser


def default_state_dir(repo: Path) -> Path:
    return repo / "deploy" / "state" / "controller"


def main(argv=None, env=None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    if not args.state_dir:
        args.state_dir = str(default_state_dir(repo))
    if not args.repo:
        args.repo = str(repo)
    if not args.image:
        # A repair keeps the controller this installation runs (for example
        # the application image the host entrypoint installed) instead of
        # swapping in a different default image.
        recorded = load_controller_image(Path(args.state_dir).resolve()) or {}
        args.image = str(recorded.get("requested") or DEFAULT_IMAGE)
    environment = dict(os.environ) if env is None else dict(env)
    if not args.target_project:
        args.target_project = target_project_for_repo(repo, environment)
    if not args.target_network:
        args.target_network = target_network_for_repo(
            repo, environment, project=args.target_project
        )
    # Only the managed marker is consulted; the rest of the host env is used.
    marker = {"MOONMIND_CONTROLLER_MANAGED": environment.get("MOONMIND_CONTROLLER_MANAGED", "")}
    commands = {
        "install": cmd_install,
        "start": cmd_start,
        "update": cmd_update,
        "restore": cmd_restore,
        "status": cmd_status,
        "ensure": cmd_ensure,
    }
    return commands[args.command](args, marker)


if __name__ == "__main__":
    raise SystemExit(main())
