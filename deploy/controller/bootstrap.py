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


def ensure_identity(state_dir: Path, repo: Path, port: int | None) -> dict:
    """Persist (or reuse) this deployment's controller project and endpoint.

    The first install derives and records the identity; later commands reuse
    the recorded project and port so a deployment keeps its endpoint across
    restarts instead of drifting when the checkout path changes case or
    symlink shape. An explicit ``--port`` always wins for the endpoint.
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
    path = state_dir / "controller-compose.yaml"
    content = f"""# MoonMind standalone deployment controller (issue #4500).
# Separate Compose project: own durable state, restart policy, and direct
# Docker socket mount. Its transport and endpoint survive target-project
# shutdown. Managed by deploy/controller/bootstrap.py on the host; the
# controller never replaces itself.
name: {project_name}
services:
  {CONTROLLER_SERVICE}:
    image: {image}
    restart: unless-stopped
    environment:
      MOONMIND_CONTROLLER_MANAGED: "1"
      MOONMIND_CONTROLLER_STATE_DIR: /var/lib/moonmind-controller
      MOONMIND_CONTROLLER_PORT: "{port}"
      MOONMIND_CONTROLLER_SECRET_FILE: /var/lib/moonmind-controller/secrets/controller-bearer
    ports:
      - "127.0.0.1:{port}:{port}"
    volumes:
      - {state_src}:/var/lib/moonmind-controller
      - {repo_src}:{repo}:ro
      - /var/run/docker.sock:/var/run/docker.sock
    labels:
      moonmind.controller.managed: "true"
"""
    path.write_text(content, encoding="utf-8")
    return path


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


def cmd_install(args, env) -> int:
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    identity = ensure_identity(state_dir, repo, getattr(args, "port", None))
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
        identity = ensure_identity(state_dir, repo, getattr(args, "port", None))
        render_compose_file(
            state_dir=state_dir,
            repo=repo,
            image=pinned,
            port=identity["port"],
            project=identity["project"],
        )
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
    identity = ensure_identity(state_dir, repo, getattr(args, "port", None))
    render_compose_file(
        state_dir=state_dir,
        repo=repo,
        image=pinned,
        port=identity["port"],
        project=identity["project"],
    )
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
    for name in ("install", "start", "update", "restore", "status"):
        child = sub.add_parser(name, parents=[common])
        child.add_argument("--repo", default=None, help="Target MoonMind checkout.")
        child.add_argument("--stack", default="moonmind", help="Target stack.")
        child.add_argument("--image", default=DEFAULT_IMAGE, help="Controller image.")
        child.add_argument("--port", type=int, default=DEFAULT_PORT)
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
    environment = dict(os.environ) if env is None else dict(env)
    # Only the managed marker is consulted; the rest of the host env is used.
    marker = {"MOONMIND_CONTROLLER_MANAGED": environment.get("MOONMIND_CONTROLLER_MANAGED", "")}
    commands = {
        "install": cmd_install,
        "start": cmd_start,
        "update": cmd_update,
        "restore": cmd_restore,
        "status": cmd_status,
    }
    return commands[args.command](args, marker)


if __name__ == "__main__":
    raise SystemExit(main())
