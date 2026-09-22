"""Host-owned lifecycle for the standalone controller (issue #4500, REQ-02).

The host CLI installs, starts, updates, and restores the controller. The
controller never replaces itself: this CLI refuses to run inside the
controller container, and controller updates are serialized against active
deployment mutation. Stdlib-only.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lock as lock_mod
import record as record_mod

CONTROLLER_PROJECT = "moonmind-controller"
CONTROLLER_SERVICE = "controller"
DEFAULT_PORT = 8472
DEFAULT_IMAGE = os.environ.get(
    "MOONMIND_CONTROLLER_IMAGE",
    "ghcr.io/moonladderstudios/moonmind-controller:latest",
)


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
) -> Path:
    """Render the separate controller Compose project (REQ-02)."""
    path = state_dir / "controller-compose.yaml"
    content = f"""# MoonMind standalone deployment controller (issue #4500).
# Separate Compose project: own durable state, restart policy, and direct
# Docker socket mount. Its transport and endpoint survive target-project
# shutdown. Managed by deploy/controller/bootstrap.py on the host; the
# controller never replaces itself.
name: {CONTROLLER_PROJECT}
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
      - {state_dir}:/var/lib/moonmind-controller
      - {repo}:{repo}:ro
      - /var/run/docker.sock:/var/run/docker.sock
    labels:
      moonmind.controller.managed: "true"
"""
    path.write_text(content, encoding="utf-8")
    return path


def _compose(state_dir: Path, *args: str) -> int:
    command = [
        "docker",
        "compose",
        "--project-name",
        CONTROLLER_PROJECT,
        "-f",
        str(state_dir / "controller-compose.yaml"),
        *args,
    ]
    return subprocess.run(command, check=False).returncode


def cmd_install(args, env) -> int:
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    compose_file = render_compose_file(
        state_dir=state_dir, repo=repo, image=args.image, port=args.port
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
    code = _compose(state_dir, "up", "-d", "--wait")
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
    lock_candidate = lock_mod.StackLock(state_dir, f"{CONTROLLER_PROJECT}-update")
    with lock_candidate.acquire():
        code = _compose(state_dir, "pull", CONTROLLER_SERVICE)
        if code != 0:
            raise RuntimeError(f"Controller image pull failed (exit {code}).")
        code = _compose(state_dir, "up", "-d", "--wait", CONTROLLER_SERVICE)
        if code != 0:
            raise RuntimeError(f"Controller recreation failed (exit {code}).")
    print("Controller updated.", flush=True)
    return 0


def cmd_restore(args, env) -> int:
    """Restore a missing/broken controller without touching deployment state."""
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    render_compose_file(
        state_dir=state_dir, repo=repo, image=args.image, port=args.port
    )
    code = _compose(state_dir, "up", "-d", "--wait", CONTROLLER_SERVICE)
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
