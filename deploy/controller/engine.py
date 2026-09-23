"""Changed-service Compose apply path owned by the controller (REQ-03, REQ-05).

Semantic default, under bounded timeouts::

    docker compose pull --policy always [<services>]
    docker compose up -d --pull never --no-build --remove-orphans --wait [<services>]

All images needed for the requested update are staged before apply, so a
registry loss after staging does not block the apply and a pull failure
leaves existing containers untouched. Installed infrastructure image/config
selections are preserved unless an infrastructure change is intentional: the
controller never injects application-release churn into unchanged services
and keeps required ``init-db`` gating inside Compose.

Full ``down`` and force-recreate are explicit repair operations only, never
automatic escalation. No volume/image pruning. On failure the controller
surfaces the exit status and a redacted log tail.

Pre-apply validation checks target configuration, authorization, persistent
storage, and preservation of access settings. Old API/worker health is
diagnostic input, never admission to repair. Post-apply verification covers
the applicable service, ordinary-dispatch, and operator-access checks;
failed or unavailable mandatory checks stay explicit while the controller
remains usable.
"""
from __future__ import annotations

import subprocess
from typing import Any, Mapping, Protocol, Sequence

from redact import redact_text, tail_text

PULL_TIMEOUT_SECONDS = 600
UP_TIMEOUT_SECONDS = 900
MAX_COMMAND_TIMEOUT_SECONDS = 900

PULL_FLAGS = ("pull", "--policy", "always")
UP_FLAGS = (
    "up",
    "-d",
    "--pull",
    "never",
    "--no-build",
    "--remove-orphans",
    "--wait",
)

class CommandError(RuntimeError):
    """A Compose/CLI command failed; carries exit status and output."""

    def __init__(self, command: str, exit_status: int, output: str = "") -> None:
        super().__init__(f"{command} failed (exit {exit_status})")
        self.command = command
        self.exit_status = exit_status
        self.output = output


class StageError(CommandError):
    """Image staging failed; no container was recreated."""


class ApplyError(CommandError):
    """Service apply failed; carries the redacted log tail."""


class SelfReplacementError(RuntimeError):
    """The controller was asked to replace itself; host-owned only."""


class Runner(Protocol):
    def run(self, args: Sequence[str], timeout_seconds: int) -> Mapping[str, Any]:
        """Run a command; return a mapping with at least ``exit``/``output``."""


def assert_safe_command(args: Sequence[str]) -> None:
    """Reject teardown/prune/force-recreate commands outright."""
    tokens = {str(part) for part in args}
    joined = " ".join(str(part) for part in args)
    if "down" in tokens and "compose" in joined:
        raise ValueError("Refusing `compose down`: explicit repair only, never automatic.")
    if "--force-recreate" in tokens or "--force-recreate" in joined:
        raise ValueError("Refusing `--force-recreate`: explicit repair only, never automatic.")
    for verb in ("prune", "volume rm", "image rm", "rmi"):
        if verb in tokens or verb in joined:
            raise ValueError(f"Refusing destructive command fragment: {verb!r}.")


def compose_base(
    *,
    project: str,
    project_dir: str,
    compose_files: Sequence[str],
    env_file: str | None = None,
    env_files: Sequence[str] | None = None,
) -> tuple[str, ...]:
    base: list[str] = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        project_dir,
    ]
    for compose_file in compose_files:
        base.extend(["-f", compose_file])
    # Layered env files in order: Compose applies them left to right, so the
    # deployment-owned `.env` stays first and the controller-owned image
    # overlay overrides only the release selection. Passing only the overlay
    # would replace Compose's implicit `.env` loading and render the stack
    # with defaulted credentials, bindings, and infrastructure versions.
    selected = list(env_files) if env_files else ([env_file] if env_file else [])
    for item in selected:
        base.extend(["--env-file", item])
    return tuple(base)


def pull_command(base: Sequence[str], services: Sequence[str]) -> tuple[str, ...]:
    command = (*base, *PULL_FLAGS, *services)
    assert_safe_command(command)
    return command


def up_command(base: Sequence[str], services: Sequence[str]) -> tuple[str, ...]:
    command = (*base, *UP_FLAGS, *services)
    assert_safe_command(command)
    return command


def run_command(
    runner: Runner, args: Sequence[str], *, timeout_seconds: int
) -> Mapping[str, Any]:
    assert_safe_command(args)
    if not 0 < timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS:
        raise ValueError(f"Refusing unbounded command timeout: {timeout_seconds!r}")
    return runner.run(tuple(args), timeout_seconds)


def stage_images(
    runner: Runner,
    base: Sequence[str],
    images: Sequence[str],
    *,
    services: Sequence[str] = (),
) -> dict:
    """Pull every image needed for the update before any apply.

    A staging failure raises :class:`StageError` and no ``up`` is attempted,
    so existing containers are left untouched.
    """
    targets = tuple(services) if services else tuple(images)
    try:
        result = run_command(
            runner, pull_command(base, targets), timeout_seconds=PULL_TIMEOUT_SECONDS
        )
    except CommandError as exc:
        raise StageError(
            "pull", exc.exit_status, redact_text(tail_text(exc.output))
        ) from exc
    if int(result.get("exit", 0)) != 0:
        raise StageError(
            "pull",
            int(result.get("exit", 1)),
            redact_text(tail_text(str(result.get("output", "")))),
        )
    return {"staged": list(images)}


def apply_services(
    runner: Runner, base: Sequence[str], services: Sequence[str]
) -> dict:
    """Recreate changed services; surface exit status + redacted log tail."""
    try:
        result = run_command(
            runner, up_command(base, services), timeout_seconds=UP_TIMEOUT_SECONDS
        )
    except CommandError as exc:
        raise ApplyError(
            "up", exc.exit_status, redact_text(tail_text(exc.output))
        ) from exc
    if int(result.get("exit", 0)) != 0:
        raise ApplyError(
            "up",
            int(result.get("exit", 1)),
            redact_text(tail_text(str(result.get("output", "")))),
        )
    return {"recreated": list(services)}


def apply(
    runner: Runner,
    *,
    project: str,
    project_dir: str,
    compose_files: Sequence[str],
    services: Sequence[str],
    images: Sequence[str],
    env_file: str | None = None,
    env_files: Sequence[str] | None = None,
    own_service: str | None = None,
) -> dict:
    """Stage all images, then apply. Never replaces the controller itself."""
    targets = [s for s in services if s]
    if own_service and own_service in targets:
        raise SelfReplacementError(
            f"Refusing to replace controller service {own_service!r} through "
            "the controller; controller update is host-owned."
        )
    if not targets:
        raise ValueError("Refusing an apply with no selected services.")
    selected_env = list(env_files) if env_files else ([env_file] if env_file else [])
    base = compose_base(
        project=project,
        project_dir=project_dir,
        compose_files=compose_files,
        env_files=selected_env or None,
    )
    staged = stage_images(runner, base, tuple(images), services=tuple(targets))
    applied = apply_services(runner, base, tuple(targets))
    return {"staged": staged["staged"], "recreated": applied["recreated"]}


def pre_apply_checks(
    *,
    config_valid: bool,
    storage_ok: bool,
    access_preserved: bool,
    authorized: bool = True,
    old_service_health: Mapping[str, str] | None = None,
) -> dict:
    """Validate before apply. Old health is diagnostic, never admission."""
    problems = []
    if not config_valid:
        problems.append("target configuration is invalid")
    if not authorized:
        problems.append("request is not authorized for this stack")
    if not storage_ok:
        problems.append("persistent storage is unavailable")
    if not access_preserved:
        problems.append("access settings would not be preserved")
    return {
        "admitted": not problems,
        "problems": problems,
        "oldHealth": {
            "status": "diagnostic",
            "services": dict(old_service_health or {}),
            "note": (
                "Old API/worker health is diagnostic input, not a "
                "prerequisite to repair."
            ),
        },
    }


def post_apply_checks(
    *,
    services_running: Mapping[str, bool],
    dispatch_ok: bool | None,
    operator_access: Mapping[str, str] | None = None,
) -> dict:
    """Verify after apply. Failed/unavailable mandatory checks stay explicit."""
    checks = []
    for service, running in services_running.items():
        checks.append(
            {
                "name": f"service:{service}",
                "status": "passed" if running else "failed",
                "detail": "running" if running else "not running after apply",
            }
        )
    if dispatch_ok is None:
        checks.append(
            {"name": "dispatch", "status": "unavailable", "detail": "not observable"}
        )
    else:
        checks.append(
            {
                "name": "dispatch",
                "status": "passed" if dispatch_ok else "failed",
                "detail": "ordinary dispatch works" if dispatch_ok else "dispatch failed",
            }
        )
    for url, outcome in (operator_access or {}).items():
        checks.append(
            {
                "name": f"operator-access:{url}",
                "status": "passed" if outcome == "ok" else "failed",
                "detail": outcome,
            }
        )
    failed = [c for c in checks if c["status"] != "passed"]
    return {
        "checks": checks,
        "complete": not failed,
        # The controller stays usable even when mandatory checks fail; the
        # gaps remain explicit instead of becoming silent success.
        "controllerUsable": True,
    }


def subprocess_runner() -> Runner:
    """Production runner backed by bounded subprocess execution."""

    class _SubprocessRunner:
        def run(self, args: Sequence[str], timeout_seconds: int) -> Mapping[str, Any]:
            assert_safe_command(args)
            try:
                completed = subprocess.run(
                    list(args),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise CommandError(
                    str(args[2]) if len(args) > 2 else str(args),
                    124,
                    redact_text(tail_text((exc.stdout or "") + (exc.stderr or ""))),
                ) from exc
            output = redact_text(
                tail_text(f"{completed.stdout or ''}\n{completed.stderr or ''}".strip())
            )
            if completed.returncode != 0:
                name = str(args[2]) if len(args) > 2 else str(args[0])
                raise CommandError(name, completed.returncode, output)
            return {"exit": 0, "output": output}

    return _SubprocessRunner()


__all__ = [
    "ApplyError",
    "CommandError",
    "MAX_COMMAND_TIMEOUT_SECONDS",
    "PULL_FLAGS",
    "PULL_TIMEOUT_SECONDS",
    "UP_FLAGS",
    "UP_TIMEOUT_SECONDS",
    "Runner",
    "SelfReplacementError",
    "StageError",
    "apply",
    "apply_services",
    "assert_safe_command",
    "compose_base",
    "post_apply_checks",
    "pre_apply_checks",
    "pull_command",
    "run_command",
    "stage_images",
    "subprocess_runner",
    "up_command",
]
