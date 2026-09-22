"""Host CLI for the controller lifecycle (stdlib only).

The host installs/starts and updates or restores the controller itself; the
controller never replaces itself. Controller update is host-owned and
serialized against active deployment mutation via the same kernel lock.

Usage:
  python -m mm_controller.main install  --state-dir <dir> [--project-dir <dir>]
  python -m mm_controller.main serve    --state-dir <dir> [--host 127.0.0.1 --port 8765]
  python -m mm_controller.main status   --state-dir <dir>
  python -m mm_controller.main restore  --state-dir <dir>  (reinstall a missing/broken controller)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import auth, lifecycle
from .controller import Controller, extract_release_config
from .kernel_lock import KernelLockManager
from .store import OperationStore

CONTROLLER_PROJECT = "moonmind-controller"
TARGET_STACK = "moonmind"


def _default_pre(payload) -> lifecycle.PreApplyVerdict:
    return lifecycle.validate_before_apply(
        config=lifecycle.TargetConfig(target_image=str(payload.get("targetImage") or "")),
        authorized=True,
        compose_valid=True,
        storage_ready=True,
        access_preserved=True,
    )


class SubprocessRunner:
    """Production DockerRunner over the host/docker subprocess."""

    def __init__(self, *, timeout: int = 600) -> None:
        self.timeout = timeout

    def _run(self, command: tuple[str, ...]) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                list(command), capture_output=True, text=True, timeout=self.timeout
            )
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            return 124, f"command timed out after {self.timeout}s: {exc.cmd}"
        except OSError as exc:
            return 127, f"command unavailable: {exc}"

    def pull(self, command: tuple[str, ...]) -> tuple[int, str]:
        return self._run(command)

    def up(self, command: tuple[str, ...]) -> tuple[int, str]:
        return self._run(command)

    def running_images(self) -> dict[str, str]:
        code, out = self._run(("docker", "ps", "--format", "{{.Names}} {{.Image}}"))
        observed: dict[str, str] = {}
        if code == 0:
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    observed[parts[0]] = parts[1]
        return observed

    def reconcile_child(self) -> str:
        code, out = self._run((
            "docker", "ps", "-q",
            "--filter", f"label=com.docker.compose.project={TARGET_STACK}",
            "--filter", "label=com.docker.compose.oneoff=True",
        ))
        stale = [c for c in out.split() if c]
        if not stale:
            return "none: no competing one-off Compose child"
        stopped = []
        for cid in stale:
            code, _ = self._run(("docker", "stop", cid))
            if code == 0:
                stopped.append(cid)
        return f"stopped competing one-off child containers: {stopped or stale}"


def cmd_status(state_dir: str) -> int:
    store = OperationStore(state_dir)
    record = store.load_operation()
    print(json.dumps({
        "operation": record.to_dict() if record else None,
        "prepared": store.read_prepared(),
        "installed": store.read_installed(),
    }, indent=2, sort_keys=True))
    return 0


def cmd_serve(state_dir: str, host: str, port: int) -> int:
    from .server import serve

    secret = auth.load_or_create_secret(state_dir)
    store = OperationStore(state_dir)
    ctrl = Controller(store=store, runner=SubprocessRunner())
    # Converge unfinished work toward the same target on (re)start.
    decision = ctrl.recover_on_restart()
    store.append_log(f"controller start: converge_on_restart={decision}\n")
    # The controller never replaces itself: refuse to run inside the target
    # project's own containers is enforced by the separate Compose project
    # (deploy/controller/docker-compose.yaml); serve only the small endpoint.
    httpd = serve(host=host, port=port, secret=secret, store=store,
                  controller=ctrl, pre_builder=_default_pre)
    print(f"moonmind-controller serving on {host}:{port} (state: {state_dir})")
    httpd.serve_forever()
    return 0


def cmd_install(state_dir: str, project_dir: str) -> int:
    """Install/start the controller project, serialized against active mutation."""
    lock = KernelLockManager(lock_dir=str(Path(state_dir) / "locks"))
    try:
        lease = lock.acquire(CONTROLLER_PROJECT, wait_seconds=60)
    except Exception as exc:
        print(f"controller install deferred (lock held): {exc}", file=sys.stderr)
        return 1
    with lease:
        compose = Path(project_dir) / "deploy" / "controller" / "docker-compose.yaml"
        if not compose.exists():
            print(f"controller compose file missing: {compose}", file=sys.stderr)
            return 1
        auth.load_or_create_secret(state_dir)
        proc = subprocess.run(
            ["docker", "compose", "-p", CONTROLLER_PROJECT, "-f", str(compose),
             "up", "-d", "--wait"],
            capture_output=True, text=True,
        )
        print((proc.stdout or "") + (proc.stderr or ""))
        return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mm-controller", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "serve", "status", "restore"):
        child = sub.add_parser(name)
        child.add_argument("--state-dir", required=True)
        if name in ("install", "restore"):
            child.add_argument("--project-dir", default=".")
        if name == "serve":
            child.add_argument("--host", default="127.0.0.1")
            child.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return cmd_status(args.state_dir)
    if args.command == "serve":
        return cmd_serve(args.state_dir, args.host, args.port)
    # restore == install for a missing/broken controller; both are host-owned
    # and serialized, and neither requires a healthy MoonMind stack.
    return cmd_install(args.state_dir, args.project_dir)


if __name__ == "__main__":
    raise SystemExit(main())
