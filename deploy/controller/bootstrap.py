"""Lifecycle for the standalone controller (issue #4500, REQ-02).

The host CLI installs, starts, updates, and restores the controller. The
controller never replaces itself: this CLI refuses to run inside the
controller container, and controller updates are serialized against active
deployment mutation. ``handoff`` runs in a one-off of the release an open
operation requested, so that release's controller applies it. Stdlib-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
# Behavior submitters require of the running controller (server.py
# CONTROLLER_CAPABILITIES). A controller lacking any of it is refreshed.
REQUIRED_CAPABILITIES = (
    "active-journal-transition",
    "request-identity",
    "unversioned-routing-cutover",
)
# `ensure --prefer-requested` reports whether the controller now runs a
# different release, so a submitter retries only when the code changed.
REFRESH_RECEIPT_PREFIX = "MOONMIND_CONTROLLER_REFRESH="
# `handoff` reports whether the requested release's controller took over.
HANDOFF_RECEIPT_PREFIX = "MOONMIND_CONTROLLER_HANDOFF="
# A replacement that exits or restarts this soon after starting is reverted.
HANDOFF_SETTLE_SECONDS = 5
# Bound readiness separately from the CLI so failed starts can reach rollback.
CONTROLLER_START_TIMEOUT_SECONDS = 30
CONTROLLER_START_COMMAND_TIMEOUT_SECONDS = 120
# The caller allows 300 seconds; leave time to deliver the final receipt.
HANDOFF_TIMEOUT_SECONDS = 270
HANDOFF_RECOVERY_SECONDS = 90
COMMAND_CLEANUP_SECONDS = 5
CONTROLLER_RECONCILE_TIMEOUT_SECONDS = 15
UNSAFE_RETRY_PREFIX = "MOONMIND_CONTROLLER_RETRY_UNSAFE="


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


def _checked_capture(command: list[str], *, environment=None, deadline=None) -> str:
    options = {}
    if environment is not None:
        options["environment"] = environment
    if deadline is not None:
        options["deadline"] = deadline
    result = _run_capture(command, **options)
    if result.returncode:
        raise RuntimeError(
            f"Controller prerequisite {command[1]} failed (exit {result.returncode})."
        )
    return result.stdout or ""


def _application_controller_image(target_project: str, requested: str) -> str:
    """Use the requested release, falling back to installed source on rollback.

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
    return _first_capable_controller_image(candidates, requested)


def _requested_controller_image(requested: str) -> str:
    """The requested release alone, after the installed controller failed it."""
    return _first_capable_controller_image([], requested)


def _first_capable_controller_image(candidates: list[str], requested: str) -> str:
    probe = (
        "import pathlib,sys; p=pathlib.Path('/app/deploy/controller'); "
        "sys.exit(10) if not (p/'server.py').is_file() else None; "
        "sys.path.insert(0,str(p)); import server; "
        f"sys.exit(0 if set({REQUIRED_CAPABILITIES!r}).issubset("
        "getattr(server,'CONTROLLER_CAPABILITIES',())) else 10)"
    )
    for image in [None, *candidates]:
        if image is None:
            inspection = [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                requested,
            ]
            immutable = requested.startswith("sha256:") or "@sha256:" in requested
            staged = _run_capture(inspection) if immutable else None
            if staged is not None and staged.returncode == 0:
                image = staged.stdout.strip()
            else:
                _checked_capture(["docker", "pull", requested])
                image = _checked_capture(inspection).strip()
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
        "Neither the installed source nor requested image supplies the required controller capabilities."
    )


def _running_controller_image(project: str, *, deadline=None) -> str | None:
    """The image the live controller container runs; ``None`` when none runs.

    The Compose file is not proof: an interrupted refresh can name the
    requested image there while the old or a stopped controller remains.
    """
    listed = _run_capture(
        [
            "docker",
            "ps",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={CONTROLLER_SERVICE}",
        ],
        **({"deadline": deadline} if deadline is not None else {}),
    )
    identifiers = (listed.stdout or "").split() if listed.returncode == 0 else []
    if len(identifiers) != 1:
        return None
    inspected = _run_capture(
        ["docker", "inspect", "--format", "{{.Image}}", identifiers[0]],
        **({"deadline": deadline} if deadline is not None else {}),
    )
    if inspected.returncode:
        return None
    return (inspected.stdout or "").strip() or None


def cmd_ensure(args, env) -> int:
    """Refresh an old controller before submission, outside its operation.

    Host and the already-privileged deployment-control submitter use the
    same lifecycle owner.

    ``--prefer-requested`` runs after the installed controller failed the
    requested release. A capability cannot show that the installed code can
    apply a release whose own controller carries the fix, so this refreshes
    a capable controller to the requested release unless it already runs it,
    and prints a receipt saying whether the controller changed.
    """
    import lock as lock_mod

    prefer_requested = bool(getattr(args, "prefer_requested", False))
    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    identity = recover_identity(state_dir)
    compose_file = state_dir / "controller-compose.yaml"
    if not identity or not compose_file.is_file():
        raise RuntimeError(
            "The controller prerequisite requires its existing installation."
        )
    secret = _secret_path(state_dir).read_text(encoding="utf-8").strip()
    url = args.controller_url or f"http://127.0.0.1:{identity['port']}"

    def supported():
        try:
            return controller_capabilities(url, secret).issuperset(REQUIRED_CAPABILITIES)
        except urllib.error.HTTPError:
            raise
        except OSError:
            # An interrupted recreation may have stopped the old process.
            # Durable operation state and the kernel lock still decide
            # whether host-owned repair may proceed; never create a writer.
            return False

    ready = supported()
    if _open_operations(state_dir, args.stack):
        if ready and not prefer_requested:
            # A capable owner keeps its active work. The submitter will
            # observe it or receive the existing busy refusal.
            return 0
        raise ActiveOperationError(
            "Controller prerequisite waits for the current deployment operation to finish."
        )
    with lock_mod.StackLock(state_dir, args.stack).acquire():
        # Recheck after exclusion: another submission can record intent while
        # bootstrap waits for the same lock. Never replace its live owner.
        if _open_operations(state_dir, args.stack):
            if supported() and not prefer_requested:
                return 0
            raise ActiveOperationError(
                "Controller prerequisite waits for the current deployment operation to finish."
            )
        image = (
            _requested_controller_image(args.image)
            if prefer_requested
            else _application_controller_image(
                str(identity.get("targetProject") or args.target_project),
                args.image,
            )
        )
        if prefer_requested and _running_controller_image(identity["project"]) == image:
            _print_refresh_receipt(refreshed=False, image=image)
            return 0
        _write_controller_compose(
            compose_file, _retargeted_controller_lines(compose_file, image)
        )
        record_controller_image(state_dir, requested=args.image, pinned=image)
        _ensure_identity_network(identity)
        code = _compose_up(state_dir, identity["project"], "--pull", "never")
        if code:
            raise RuntimeError(
                f"Controller prerequisite recreation failed (exit {code})."
            )
        deadline = time.monotonic() + 30
        while True:
            try:
                if controller_capabilities(url, secret).issuperset(
                    REQUIRED_CAPABILITIES
                ):
                    if prefer_requested:
                        _print_refresh_receipt(refreshed=True, image=image)
                    return 0
            except OSError:
                # The recreated controller may not accept connections yet;
                # keep polling within the existing readiness deadline.
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Controller prerequisite did not expose the required controller capabilities."
                )
            time.sleep(1)


def _retargeted_controller_lines(
    compose_file: Path, image: str, *, deadline=None
) -> list[str]:
    """The installed controller Compose file, running ``image``'s controller.

    Reuse the generated file so daemon host paths, networks, identity and
    authority never change in an app container; only the spelling of a bind
    source changes, for a client that resolves the same host directory under
    its WSL drive mount.
    """
    lines = _client_bind_lines(compose_file.read_text(encoding="utf-8").splitlines())
    image_lines = [
        index for index, line in enumerate(lines) if line.startswith("    image:")
    ]
    if len(image_lines) != 1:
        raise RuntimeError("Controller Compose project has no unique controller image.")
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
        previous_user = json.loads(
            _checked_capture(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.User}}",
                    previous_image,
                ],
                **({"deadline": deadline} if deadline is not None else {}),
            )
        )
        if not isinstance(previous_user, str):
            raise RuntimeError("Installed controller process identity is unavailable")
        replacement.append(f"    user: {json.dumps(previous_user or '0:0')}")
    lines[index : index + 1] = replacement
    return lines


def _write_controller_compose(compose_file: Path, lines: list[str]) -> None:
    temporary = compose_file.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, compose_file)


def _print_refresh_receipt(*, refreshed: bool, image: str) -> None:
    receipt = json.dumps({"refreshed": refreshed, "image": image}, sort_keys=True)
    print(f"{REFRESH_RECEIPT_PREFIX}{receipt}", flush=True)


def cmd_handoff(args, env) -> int:
    """Let the controller an open operation's requested release ships apply it.

    The running controller starts this in a one-off of the requested image
    while it holds the stack lock for ``--operation``, before the operation
    changes anything. Recreating the controller ends that process, and its
    kernel lock with it; the new controller resumes the operation from its
    record. Only that operation may be open, and each attempt group hands
    off once, so a controller that cannot take over never loops: the running
    controller keeps applying. Failed replacement recovery is bounded by
    the same deadline and requires confirmed command cleanup and state.
    """
    _ensure_outside_controller(env)
    deadline = time.monotonic() + HANDOFF_TIMEOUT_SECONDS
    # Docker stop/cancellation must unwind the owned CLI before the helper
    # exits, without treating cancellation as a reason to launch recovery.
    previous_handler = signal.signal(signal.SIGTERM, _cancel_handoff)
    try:
        return _handoff(args, deadline=deadline)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return _print_handoff_receipt("unavailable", reason=str(exc))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _cancel_handoff(signum, frame):
    raise SystemExit(128 + signum)


def _handoff(args, *, deadline: float) -> int:
    forward_deadline = deadline - HANDOFF_RECOVERY_SECONDS
    state_dir = Path(args.state_dir).resolve()
    compose_file = state_dir / "controller-compose.yaml"
    if not compose_file.is_file():
        return _print_handoff_receipt("unavailable", reason="no installed controller")
    identity = recover_identity(state_dir, deadline=forward_deadline)
    project = str(identity["project"])
    image = _checked_capture(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
        deadline=forward_deadline,
    ).strip()
    previous = _running_controller_image(project, deadline=forward_deadline)
    if previous == image:
        return _print_handoff_receipt("current", image=image)
    store = record_mod.OperationStore(state_dir)
    operation = store.load(args.operation)
    group = operation.get("attemptGroup", 1)
    marker = operation.get("controllerHandoff") or {}
    if previous is None:
        reason = "the installed controller is not running"
    elif operation.get("status") not in record_mod.OPEN_STATUSES:
        reason = "the operation is not open"
    elif [op["operationId"] for op in store.list_open()] != [args.operation]:
        reason = "other deployment work is open"
    elif marker.get("image") == image and marker.get("attemptGroup") == group:
        reason = "this attempt already handed off to the requested controller"
    else:
        reason = None
    if reason:
        return _print_handoff_receipt("unavailable", reason=reason)
    store.record_controller_handoff(
        args.operation, {"image": image, "from": previous, "attemptGroup": group}
    )
    original = compose_file.read_text(encoding="utf-8")
    recorded = load_controller_image(state_dir) or {}
    try:
        _write_controller_compose(
            compose_file,
            _retargeted_controller_lines(
                compose_file, image, deadline=forward_deadline
            ),
        )
        record_controller_image(state_dir, requested=args.image, pinned=image)
        _ensure_identity_network(identity, deadline=forward_deadline)
        code = _compose_up(
            state_dir, project, "--pull", "never", deadline=forward_deadline
        )
        if code:
            raise RuntimeError(f"controller recreation failed (exit {code})")
        if not _controller_stays_running(project, image, deadline=forward_deadline):
            raise RuntimeError("the requested controller did not stay running")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        try:
            compose_file.write_text(original, encoding="utf-8")
            if recorded:
                record_controller_image(
                    state_dir,
                    requested=str(recorded.get("requested") or ""),
                    pinned=recorded.get("pinned"),
                )
            if isinstance(exc, CommandCleanupError):
                raise RuntimeError("recovery skipped: command cleanup is unverified")  # noqa: TRY004 - outcome, not argument validation
            _reconcile_controller(project, images={image, previous}, deadline=deadline)
            code = _compose_up(state_dir, project, "--pull", "never", deadline=deadline)
            if code:
                raise RuntimeError(f"exit {code}")
            if not _controller_stays_running(
                project, previous, deadline=deadline, allow_prior_restarts=True
            ):
                raise RuntimeError("previous controller readiness is unverified")
            restored = "restored"
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
        ) as restore_error:
            restored = f"restore failed ({restore_error})"
        return _print_handoff_receipt(
            "unavailable", reason=f"{exc}; previous controller {restored}"
        )
    return _print_handoff_receipt("handed-off", image=image)


def _controller_stays_running(
    project: str, image: str, *, deadline=None, allow_prior_restarts=False
) -> bool:
    """Verify the same healthy container stays running through the settle window.

    RestartCount is lifetime history, not a recovery failure. Compare it and
    StartedAt across this window so a retained prior container may have history,
    but a new restart, replacement, or unhealthy state cannot pass readiness.
    Forward replacements retain the stricter zero-restart requirement.
    """
    budget = {"deadline": deadline} if deadline is not None else {}

    def snapshot():
        listed = _run_capture(
            [
                "docker",
                "ps",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={CONTROLLER_SERVICE}",
            ],
            **budget,
        )
        identifiers = (listed.stdout or "").split() if listed.returncode == 0 else []
        if len(identifiers) != 1:
            return None
        inspected = _run_capture(["docker", "inspect", identifiers[0]], **budget)
        if inspected.returncode:
            return None
        try:
            containers = json.loads(inspected.stdout or "[]")
            if not isinstance(containers, list) or len(containers) != 1:
                return None
            container = containers[0]
            state = container.get("State") or {}
            labels = (container.get("Config") or {}).get("Labels") or {}
            restarts = container.get("RestartCount")
            if (
                container.get("Id") != identifiers[0]
                or container.get("Image") != image
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != CONTROLLER_SERVICE
                or state.get("Status") != "running"
                or state.get("Restarting")
                or state.get("Paused")
                or state.get("Dead")
                or (
                    "Health" in state
                    and (state["Health"] or {}).get("Status") != "healthy"
                )
                or not isinstance(state.get("StartedAt"), str)
                or not state["StartedAt"]
                or type(restarts) is not int
                or restarts < 0
            ):
                return None
            return container["Id"], restarts, state["StartedAt"]
        except (ValueError, TypeError, AttributeError):
            return None

    before = snapshot()
    if before is None or (not allow_prior_restarts and before[1] != 0):
        return False
    if deadline is not None and _remaining(deadline) <= HANDOFF_SETTLE_SECONDS:
        raise RuntimeError("handoff readiness budget exhausted")
    time.sleep(HANDOFF_SETTLE_SECONDS)
    return snapshot() == before


def _print_handoff_receipt(status: str, **details: str) -> int:
    receipt = json.dumps({"status": status, **details}, sort_keys=True)
    print(f"{HANDOFF_RECEIPT_PREFIX}{receipt}", flush=True)
    return 0


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


def describe_installation(state_dir: Path, *, deadline=None) -> dict:
    """Read installed identity and bind authority through one Compose inspection."""
    compose_file = state_dir / "controller-compose.yaml"
    if not compose_file.is_file():
        raise RuntimeError(
            "Controller identity recovery requires its existing Compose installation."
        )
    # The rendered controller project is independent of the application's
    # ambient Compose project and .env; its file records resolved settings.
    compose_environment = dict(os.environ)
    compose_environment.pop("COMPOSE_PROJECT_NAME", None)
    config = json.loads(
        _checked_capture(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "-f",
                str(compose_file),
                "config",
                "--format",
                "json",
            ],
            environment=compose_environment,
            **({"deadline": deadline} if deadline is not None else {}),
        )
    )
    controller = (config.get("services") or {}).get(CONTROLLER_SERVICE) or {}
    environment = controller.get("environment") or {}
    project = config.get("name")
    repo = environment.get("MOONMIND_CONTROLLER_TARGET_REPO")
    try:
        port = int(environment.get("MOONMIND_CONTROLLER_PORT"))
    except (TypeError, ValueError):
        port = 0
    if not project or not repo or not 0 < port < 65536:
        raise RuntimeError(
            "The installed Compose project does not identify its controller endpoint."
        )
    identity = {
        "project": str(project),
        "port": port,
        "repo": str(repo),
        "recordedAt": int(time.time()),
    }
    links = []
    for name, membership in (controller.get("networks") or {}).items():
        if CONTROLLER_ALIAS in ((membership or {}).get("aliases") or ()):
            network = (config.get("networks") or {}).get(name) or {}
            if network.get("name"):
                links.append(str(network["name"]))
    if len(links) > 1:
        raise RuntimeError(
            "The installed controller has more than one API link network."
        )
    if links:
        identity["targetNetwork"] = links[0]
    target = environment.get("MOONMIND_CONTROLLER_TARGET_PROJECT")
    if target:
        identity["targetProject"] = str(target)

    sources = {}
    for kind, target in (("repo", repo), ("state", "/var/lib/moonmind-controller")):
        bindings = [
            volume.get("source")
            for volume in controller.get("volumes") or ()
            if volume.get("type") == "bind" and volume.get("target") == target
        ]
        if len(bindings) != 1 or not isinstance(bindings[0], str) or not bindings[0]:
            raise RuntimeError(
                f"The installed controller has no unique {kind} bind source."
            )
        sources[kind] = bindings[0]
    return {"identity": identity, "bindSources": sources}


def recover_identity(state_dir: Path, *, deadline=None) -> dict:
    """Restore lost metadata without recreating the controller or touching work."""
    existing = load_identity(state_dir)
    if existing:
        return existing
    identity = describe_installation(
        state_dir, **({"deadline": deadline} if deadline is not None else {})
    )["identity"]
    # This metadata contains no credential. Host callers must be able to read
    # it after recovery runs with the controller's root-owned state authority.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=state_dir, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(identity, stream, sort_keys=True, indent=2)
            stream.write("\n")
            os.fchmod(stream.fileno(), 0o644)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _identity_path(state_dir))
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return identity


def cmd_recover_identity(args, env) -> int:
    _ensure_outside_controller(env)
    recover_identity(Path(args.state_dir).resolve())
    print(
        "Controller identity recovered from its existing Compose installation.",
        flush=True,
    )
    return 0


def cmd_describe_installation(args, env) -> int:
    print(json.dumps(describe_installation(Path(args.state_dir).resolve())), flush=True)
    return 0


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
    explicit = str(
        (env or {}).get("COMPOSE_PROJECT_NAME") or ""
    ).strip() or _repo_env_value(repo, "COMPOSE_PROJECT_NAME")
    name = explicit or repo.name
    normalized = "".join(
        char for char in name.lower() if char.isalnum() or char in "-_"
    )
    return normalized or "moonmind"


def ensure_target_network(network: str, project: str, *, deadline=None) -> None:
    """Create the API link network when the MoonMind stack has not yet.

    The network carries the Compose labels of the MoonMind project so a later
    ``docker compose up`` adopts it instead of warning. Because the
    controller stays attached, a target-project shutdown leaves the network
    (and the controller endpoint) in place.
    """
    options = {"deadline": deadline} if deadline is not None else {}
    try:
        inspected = _run_capture(["docker", "network", "inspect", network], **options)
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
        ],
        **options,
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
    resolved_port = (
        port
        if port is not None
        else (existing or {}).get("port") or port_for_repo(repo)
    )
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


class CommandTimeoutError(RuntimeError):
    """The command budget expired with no owned process group left running."""


class ControllerStateUnverifiedError(RuntimeError):
    """Tell host fallback not to launch another source against uncertain effects."""

    def __str__(self):
        return UNSAFE_RETRY_PREFIX + super().__str__()


class CommandCleanupError(ControllerStateUnverifiedError):
    """The prior CLI may still mutate Docker; no recovery mutation is safe."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CommandTimeoutError("controller command budget exhausted")
    return remaining


def _group_running(group: int) -> bool:
    """Observe the owned POSIX group; Linux zombies cannot issue Docker requests."""
    if not sys.platform.startswith("linux"):
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return False
        return True
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            # Linux may report ESRCH after opening stat when that process exits.
            # Continue observing the group: its launcher can exit before a plugin.
            continue
        if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
            return True
    return False


def _stop_command(process, *, deadline: float) -> None:
    """Finish bounded cleanup before honoring cancellation, including during probes."""
    pending = []
    handlers = {}

    def defer(signum, frame):
        pending.append((signum, frame))

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(signum)
        if previous != signal.SIG_IGN:
            handlers[signum] = signal.signal(signum, defer)
    try:
        _stop_command_group(process, deadline=deadline)
    except CommandCleanupError as exc:
        if pending:
            # Cancellation must not hide the unsafe-retry diagnostic from the host.
            print(str(exc), file=sys.stderr, flush=True)
        raise
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if pending:
            signum, frame = pending[0]
            handler = handlers[signum]
            if callable(handler):
                handler(signum, frame)
            else:
                raise SystemExit(128 + signum)


def _stop_command_group(process, *, deadline: float) -> None:
    """Stop the owned CLI and Compose plugin before inspecting Docker again."""
    try:
        for signum, grace in ((signal.SIGTERM, 1), (signal.SIGKILL, 3)):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                # The group already exited; this shutdown race needs no signal.
                pass
            process.poll()  # Reap the launcher before portable group-existence probes.
            until = min(deadline, time.monotonic() + grace)
            while _group_running(process.pid) and time.monotonic() < until:
                time.sleep(min(0.05, max(0, until - time.monotonic())))
                process.poll()
            if not _group_running(process.pid):
                process.wait(timeout=_remaining(deadline))
                return
        raise RuntimeError("process group still running")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise CommandCleanupError(
            f"controller command cleanup unverified: {exc}"
        ) from exc


def _run_owned(command: list[str], *, deadline: float, capture=False, environment=None):
    """Use the shared phase budget, including bounded descendant cleanup.

    Refresh/handoff use a POSIX process group. Native Windows discovery and
    cold-image startup retain their existing unbounded subprocess path.
    """
    timeout = min(
        CONTROLLER_START_COMMAND_TIMEOUT_SECONDS,
        _remaining(deadline) - COMMAND_CLEANUP_SECONDS,
    )
    if timeout <= 0:
        raise CommandTimeoutError("controller command budget exhausted")
    if os.name != "posix":
        raise RuntimeError("bounded controller startup requires its Linux helper")
    previous_handler = signal.signal(signal.SIGTERM, _cancel_handoff)
    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            text=True,
            env=environment,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            # Enter protected cleanup even after launcher success: the plugin
            # can outlive it, and cancellation may arrive during observation.
            _stop_command(process, deadline=deadline)
        except CommandCleanupError:
            raise
        except BaseException as exc:
            try:
                _stop_command(process, deadline=deadline)
            except CommandCleanupError as cleanup:
                if not isinstance(exc, Exception):
                    print(str(cleanup), file=sys.stderr, flush=True)
                    raise exc
                raise CommandCleanupError(f"{exc}; {cleanup}") from exc
            if isinstance(exc, subprocess.TimeoutExpired):
                raise CommandTimeoutError(
                    f"controller command timed out after {timeout:g} seconds"
                ) from exc
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _reconcile_controller(
    project: str, *, images: set[str] | None = None, deadline: float
) -> str:
    """Fail closed on ambiguous or transitional exact-project container state."""
    identifiers = _checked_capture(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={CONTROLLER_SERVICE}",
        ],
        deadline=deadline,
    ).split()
    if not identifiers:
        return "no controller container"
    if len(identifiers) != 1:
        raise RuntimeError("recovery skipped: ambiguous controller containers")
    containers = json.loads(
        _checked_capture(["docker", "inspect", *identifiers], deadline=deadline)
    )
    if not isinstance(containers, list) or len(containers) != 1:
        raise RuntimeError("recovery skipped: controller inspection unavailable")
    container = containers[0]
    if not isinstance(container, dict):
        raise ValueError("recovery skipped: invalid controller inspection")  # noqa: TRY004 - invalid Docker response
    labels = (container.get("Config") or {}).get("Labels") or {}
    state = container.get("State") or {}
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != CONTROLLER_SERVICE
        or not isinstance(container.get("Image"), str)
        or not container["Image"]
        or (images is not None and container["Image"] not in images)
        or state.get("Status") not in {"created", "running", "exited", "restarting"}
        or state.get("Paused")
        or state.get("Dead")
        or (
            state.get("Status") == "created"
            and (state.get("Running") or state.get("Restarting"))
        )
    ):
        # After command cleanup, a known created or restarting candidate is
        # precisely what rollback repairs. Removing/unknown or contradictory
        # created state is not safe to repeat.
        raise RuntimeError("recovery skipped: controller state is uncertain")
    return f"{container['Image']} ({state['Status']})"


def _run_capture(
    args: list, *, environment=None, deadline=None
) -> subprocess.CompletedProcess:
    if deadline is not None:
        return _run_owned(
            args, deadline=deadline, capture=True, environment=environment
        )
    return subprocess.run(
        args, capture_output=True, text=True, timeout=120, check=False, env=environment
    )


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
        if len(hexpart) != 64 or any(
            c not in "0123456789abcdefABCDEF" for c in hexpart
        ):
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


def record_controller_image(
    state_dir: Path, *, requested: str, pinned: str | None
) -> dict:
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

    Bind sources are resolved through the mount adapter: a Windows drive
    path becomes its Desktop daemon-namespace path
    (``/run/desktop/mnt/host/<drive>/...``) and a WSL distro's
    ``/mnt/<drive>`` path stays the path its Docker client resolves. A
    missing required host source fails fast instead of becoming an
    auto-created empty directory. The Compose
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


def _compose(state_dir: Path, project: str, *args: str, deadline=None) -> int:
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(state_dir / "controller-compose.yaml"),
        *args,
    ]
    if deadline is not None:
        return _run_owned(command, deadline=deadline).returncode
    # Cold-image startup remains host-portable and must allow download time.
    return subprocess.run(command, check=False, timeout=None).returncode


def _compose_up(state_dir: Path, project: str, *options: str, deadline=None) -> int:
    """Start the controller, replacing a container that is not running.

    A stopped container keeps the network IDs it last joined, and Compose
    restarts it rather than recreating it. Once the target stack recreates
    the link network, that restart fails with "network ... not found".
    ``compose rm`` removes only a container that is not running, so a live
    controller is untouched; its state lives in the bind-mounted state dir.
    Bound startup so a restarting replacement returns control for rollback.
    """
    bounded_refresh = (
        deadline is None
        and "--pull" in options
        and options[options.index("--pull") + 1] == "never"
    )
    if bounded_refresh:
        deadline = (
            time.monotonic()
            + CONTROLLER_START_COMMAND_TIMEOUT_SECONDS
            + COMMAND_CLEANUP_SECONDS
        )
    budget = {"deadline": deadline} if deadline is not None else {}
    try:
        code = _compose(
            state_dir, project, "rm", "--force", CONTROLLER_SERVICE, **budget
        )
        if code:
            return code
        return _compose(
            state_dir,
            project,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            str(CONTROLLER_START_TIMEOUT_SECONDS),
            *options,
            CONTROLLER_SERVICE,
            **budget,
        )
    except CommandTimeoutError as exc:
        if not bounded_refresh:
            raise  # Handoff owns its reserved recovery/reconciliation budget.
        try:
            observed = _reconcile_controller(
                project,
                deadline=time.monotonic() + CONTROLLER_RECONCILE_TIMEOUT_SECONDS,
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
        ) as inspection:
            raise ControllerStateUnverifiedError(
                f"{exc}; controller state unverified: {inspection}"
            ) from exc
        raise RuntimeError(f"{exc}; controller state reconciled: {observed}") from exc


def _client_bind_lines(lines: list[str]) -> list[str]:
    """Spell Desktop daemon-namespace bind sources for this Docker client."""
    prefix = f"      - {mounts_mod.DESKTOP_HOST_MOUNT_ROOT}/"
    spelled = []
    for line in lines:
        if line.startswith(prefix):
            source, separator, target = line[len("      - ") :].partition(":")
            line = f"      - {mounts_mod.wsl_distro_host_path(source)}{separator}{target}"
        spelled.append(line)
    return spelled


def _project_for_state(state_dir: Path, repo: Path) -> str:
    identity = load_identity(state_dir)
    if identity:
        return str(identity["project"])
    return project_for_repo(repo)


def _ensure_identity_network(identity: dict | None, *, deadline=None) -> None:
    network = (identity or {}).get("targetNetwork")
    if network:
        ensure_target_network(
            str(network),
            str((identity or {}).get("targetProject") or "moonmind"),
            **({"deadline": deadline} if deadline is not None else {}),
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
        "Start with: " f"python3 {Path(__file__).name} start --state-dir {state_dir}",
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
    code = _compose_up(state_dir, project)
    if code != 0:
        raise RuntimeError(f"Controller start failed (exit {code}).")
    print("Controller is running.", flush=True)
    return 0


def _open_operations(state_dir: Path, stack: str) -> list:
    store = record_mod.OperationStore(state_dir)
    return store.list_open(stack=stack)


def cmd_update(args, env) -> int:
    import lock as lock_mod

    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    stack = args.stack
    # Controller replacement and stack mutation share one atomic exclusion
    # boundary: the target stack's lock, the same lock submissions hold
    # while they record intent and apply. Open work is read only under it.
    with lock_mod.StackLock(state_dir, stack).acquire():
        open_operations = _open_operations(state_dir, stack)
        if open_operations:
            raise ActiveOperationError(
                f"Refusing controller update while {len(open_operations)} deployment "
                f"operation(s) on stack {stack!r} are open; controller update is "
                "serialized against active deployment mutation."
            )
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
        code = _compose_up(state_dir, identity["project"])
        if code != 0:
            raise RuntimeError(f"Controller recreation failed (exit {code}).")
    print("Controller updated.", flush=True)
    return 0


def cmd_restore(args, env) -> int:
    """Restore a missing/broken controller without touching deployment state.

    A controller that still owns the stack is live, not broken: rerendering
    could recreate it mid-apply, so restore takes the same stack lock. A
    crashed controller's kernel lock ends with its process.
    """
    import lock as lock_mod

    _ensure_outside_controller(env)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    ensure_secret(state_dir)
    with lock_mod.StackLock(state_dir, args.stack).acquire():
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
        code = _compose_up(state_dir, identity["project"])
    if code != 0:
        raise RuntimeError(f"Controller restore failed (exit {code}).")
    print("Controller restored independently of MoonMind health.", flush=True)
    return 0


def cmd_status(args, env) -> int:
    state_dir = Path(args.state_dir).resolve()
    compose_file = state_dir / "controller-compose.yaml"
    secret_file = _secret_path(state_dir)
    print(
        f"compose file: {compose_file} {'present' if compose_file.exists() else 'missing'}",
        flush=True,
    )
    print(
        f"secret: {secret_file} {'present' if secret_file.exists() else 'missing'}",
        flush=True,
    )
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
    parser.add_argument(
        "--state-dir", required=False, help="Controller state directory."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state-dir", required=False, help="Controller state directory."
    )
    for name in (
        "install",
        "start",
        "update",
        "restore",
        "status",
        "ensure",
        "recover-identity",
        "describe-installation",
        "handoff",
    ):
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
        if name == "ensure":
            child.add_argument(
                "--prefer-requested",
                action="store_true",
                help=(
                    "After the installed controller failed the requested "
                    "release, refresh to that release's controller unless it "
                    "already runs it."
                ),
            )
        if name == "handoff":
            child.add_argument(
                "--operation",
                required=True,
                help="The open operation the requested release's controller resumes.",
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
    marker = {
        "MOONMIND_CONTROLLER_MANAGED": environment.get(
            "MOONMIND_CONTROLLER_MANAGED", ""
        )
    }
    commands = {
        "install": cmd_install,
        "start": cmd_start,
        "update": cmd_update,
        "restore": cmd_restore,
        "status": cmd_status,
        "ensure": cmd_ensure,
        "recover-identity": cmd_recover_identity,
        "describe-installation": cmd_describe_installation,
        "handoff": cmd_handoff,
    }
    return commands[args.command](args, marker)


if __name__ == "__main__":
    raise SystemExit(main())
