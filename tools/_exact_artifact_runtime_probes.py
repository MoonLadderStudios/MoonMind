"""CI-only Docker/network probes for the exact deployable image.

Source issue: MoonLadderStudios/MoonMind#3710.

Kept separate from ``tools/gather_exact_artifact_runtime_evidence.py`` so the
pure evidence-assembly core stays importable and unit-tested without a
container runtime.  Every probe reflects the *observed* result: on failure it
returns ``ok=False`` with a bounded, secret-free detail rather than fabricating
a pass, so the downstream gate fails closed.

Two boundaries make these probes real rather than nominal:

* **The image under test is referenced by its immutable local content id.**  A
  locally built image has no registry repo digest, so ``repo@sha256:<image id>``
  is unpullable; the probes therefore run ``sha256:<image id>``, which Docker
  resolves locally and which *is* the immutable artifact.  The digest-pinned
  reference recorded in the report is a separate identity string.
* **Probe scripts are supplied through an explicit read-only mount, not baked
  into the deployable image.**  ``/probe`` carries the repository's probe
  assets; ``PYTHONPATH=/app`` keeps every import resolving from the *image's*
  installed code, so the mount supplies the harness and never shadows the
  artifact under test.

All probes run the exact image with ``--network host`` so the container reaches
the job's PostgreSQL service on ``127.0.0.1:5432``, the job's Temporal test
server on ``127.0.0.1:7233``, and the API/worker bind to host ports the probes
can reach.  This module is imported only inside the Tier-1 CI job; it is never
imported when Docker is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from moonmind.omnigent.bridge_config import resolve_bridge_config

REPO_ROOT = Path(__file__).resolve().parents[1]

API_PORT = 8000
WORKER_READY_PORT = 8080

# The read-only mount that supplies probe assets to the deployable image, and
# the image's own application root, which stays first on the import path.
PROBE_MOUNT = "/probe"
APP_ROOT = "/app"

# The repository's canonical Alembic invocation.  ``alembic`` has no root
# configuration, so both migration probes must name this file explicitly.
ALEMBIC_CONFIG = "api_service/migrations/alembic.ini"

# Placeholder identifiers only resolve the route *pattern*; each probe asserts
# the request reaches the route's real handler (a non-404), never that the id
# exists.
_PROBE_SESSION_ID = "exact-artifact-probe-session"
_PROBE_HOST_ID = "exact-artifact-probe-host"


def probe_route_templates() -> dict[str, str]:
    """Return the mounted application route templates the runtime probes hit.

    Derived from the operator-declared bridge configuration so this probe and
    the hermetic route-contract test share one source of truth with the FastAPI
    routes actually mounted in ``api_service.main`` (liveness ``/healthz``, the
    Omnigent SSE stream, and the Omnigent WebSocket tunnel) plus the worker
    readiness endpoint.  If a route is renamed, the contract test
    (tests/unit/tools/test_exact_artifact_runtime_probe_routes.py) fails instead
    of the Tier-1 job silently mis-gating a healthy image on a fall-through 404.
    """

    public_api = resolve_bridge_config().public_api
    mount = public_api.mount_path.rstrip("/")
    return {
        "liveness": "/healthz",
        "http": "/openapi.json",
        "sse": f"{mount}{public_api.routes.stream_events}",
        "websocket": f"{mount}/v1/hosts/{{host_id}}/tunnel",
        "worker_ready": "/readyz",
    }


def _resolve_probe_path(template: str) -> str:
    """Fill placeholder route params so a request reaches the real handler."""

    return template.format(session_id=_PROBE_SESSION_ID, host_id=_PROBE_HOST_ID)


def _signal(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def postgres_env(database_url: str) -> dict[str, str]:
    """Translate a PostgreSQL URL into the settings the image actually reads.

    ``moonmind.config.settings.DatabaseSettings`` (used by both the API and the
    Alembic environment) resolves its connection from ``POSTGRES_*``, so passing
    only ``DATABASE_URL`` would leave every container pointed at the Compose
    default host and fail for a reason unrelated to the artifact under test.
    """

    parsed = urllib.parse.urlsplit(database_url)
    if not parsed.hostname or not parsed.path.lstrip("/"):
        raise ValueError("database URL must include a host and database name")
    env = {
        "POSTGRES_HOST": parsed.hostname,
        "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_DB": parsed.path.lstrip("/"),
    }
    if parsed.username:
        env["POSTGRES_USER"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        env["POSTGRES_PASSWORD"] = urllib.parse.unquote(parsed.password)
    return env


def database_url_for(database_url: str, database_name: str) -> str:
    """Return ``database_url`` pointing at ``database_name``."""

    parsed = urllib.parse.urlsplit(database_url)
    return urllib.parse.urlunsplit(parsed._replace(path=f"/{database_name}"))


def reset_database(admin_database_url: str, database_name: str) -> str:
    """Drop and recreate ``database_name``, returning its URL.

    Each migration scenario needs its *own* empty database.  Reusing one
    database across scenarios makes the second scenario's result meaningless:
    an upgrade replayed over tables that already exist fails even for a valid
    migration chain.
    """

    import psycopg2  # imported here so the pure module stays importable
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    connection = psycopg2.connect(admin_database_url)
    try:
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with connection.cursor() as cursor:
            identifier = sql.Identifier(database_name)
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(identifier)
            )
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(identifier))
    finally:
        connection.close()
    return database_url_for(admin_database_url, database_name)


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=timeout
    )


def _docker_run_args(
    image: str,
    *,
    detach: bool = False,
    name: str | None = None,
    entrypoint: str | None = None,
    env: dict[str, str] | None = None,
    repo_dir: Path | None = None,
) -> list[str]:
    """Build a ``docker run`` invocation for the exact image under test."""

    args = ["docker", "run", "--network", "host", "-w", APP_ROOT]
    if detach:
        # Deliberately NOT --rm: the restart probe stops and starts this
        # container, and auto-removal races with that stop. The caller's
        # ``finally`` block removes it.
        args.append("-d")
    else:
        args.append("--rm")
    if name:
        args += ["--name", name]
    if entrypoint:
        args += ["--entrypoint", entrypoint]
    # The image's own application root stays first on the import path so a
    # mounted probe asset can never shadow the artifact under test.
    merged_env = {"PYTHONPATH": APP_ROOT}
    merged_env.update(env or {})
    for key, value in merged_env.items():
        args += ["-e", f"{key}={value}"]
    if repo_dir is not None:
        args += ["-v", f"{repo_dir}:{PROBE_MOUNT}:ro"]
    args.append(image)
    return args


@contextmanager
def _container(
    image: str,
    *,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    repo_dir: Path | None = None,
) -> Iterator[str]:
    name = f"exact-artifact-{uuid.uuid4().hex[:12]}"
    run_args = _docker_run_args(
        image, detach=True, name=name, env=env, repo_dir=repo_dir
    )
    if command:
        run_args += command
    started = _run(run_args)
    if started.returncode != 0:
        raise RuntimeError(f"failed to start container: {started.stderr.strip()[:300]}")
    try:
        yield name
    finally:
        _run(["docker", "rm", "-f", name], timeout=60)


def _http_status(url: str, *, timeout: float = 3.0) -> int | None:
    """Return the HTTP status for ``url`` (a real 4xx counts), or None if the
    request never reached a handler (connection error)."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


def _await_health(base_url: str, path: str, *, attempts: int = 60) -> bool:
    for _ in range(attempts):
        if _http_status(f"{base_url}{path}") == 200:
            return True
        time.sleep(2)
    return False


def _probe_migrations(
    image: str, database_url: str, *, prior_schema: bool
) -> tuple[bool, str]:
    """Apply migrations in the exact image against a dedicated empty database.

    ``prior_schema`` materializes a *real* prior revision (the parent of the
    repository head) and then upgrades to head, which is what a deployment onto
    an existing database actually does.  Stamping ``base`` would only move
    Alembic's version marker and leave the schema untouched.
    """

    env = postgres_env(database_url)
    if not prior_schema:
        result = _run(_alembic(image, env, "upgrade", "head"), timeout=300)
        return (
            result.returncode == 0,
            "migrations clean apply succeeded"
            if result.returncode == 0
            else f"migrations clean apply failed: {_stderr(result)}",
        )

    parent = _resolve_head_parent(image, env)
    if parent is None:
        return False, "could not resolve the revision preceding the repository head"
    if parent == "base":
        return (
            False,
            "repository head has no parent revision, so no prior schema exists to "
            "upgrade from",
        )
    materialize = _run(_alembic(image, env, "upgrade", parent), timeout=300)
    if materialize.returncode != 0:
        return (
            False,
            f"could not materialize prior revision {parent[:12]}: "
            f"{_stderr(materialize)}",
        )
    result = _run(_alembic(image, env, "upgrade", "head"), timeout=300)
    return (
        result.returncode == 0,
        f"prior-schema upgrade from {parent[:12]} succeeded"
        if result.returncode == 0
        else f"prior-schema upgrade from {parent[:12]} failed: {_stderr(result)}",
    )


def _alembic(image: str, env: dict[str, str], *command: str) -> list[str]:
    return _docker_run_args(image, entrypoint="alembic", env=env) + [
        "-c",
        ALEMBIC_CONFIG,
        *command,
    ]


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()[:200]


def _resolve_head_parent(image: str, env: dict[str, str]) -> str | None:
    """Return the revision immediately preceding the repository head."""

    shown = _run(_alembic(image, env, "show", "head"), timeout=120)
    if shown.returncode != 0:
        return None
    for line in shown.stdout.splitlines():
        label, _, value = line.partition(":")
        if label.strip().lower() == "parent":
            parent = value.strip()
            return parent or None
    return None


def _probe_websocket(url: str) -> tuple[bool, str]:
    """A real WebSocket route completes a handshake (101) or fails through the
    real handler (a non-404 status). A 404 means the route fell through (the
    #3697 failure mode)."""
    try:
        from websockets.sync.client import connect
    except Exception as exc:  # pragma: no cover - CI dependency
        return False, f"websocket client unavailable: {exc}"
    try:
        with connect(url, open_timeout=5):
            return True, "WebSocket handshake completed (101)"
    except Exception as exc:  # noqa: BLE001 - inspect handshake rejection
        text = str(exc)
        if "404" in text:
            return False, "WebSocket route fell through to HTTP 404 (#3697)"
        # A rejection that is not a 404 still proves the route reached the real
        # handler (for example an auth 401/403), which is a resolved handshake.
        return True, f"WebSocket reached the real handler: {text[:120]}"


def _probe_server_routes(base_url: str) -> list[dict[str, Any]]:
    """Probe the running API's routes; the caller owns the container lifetime."""

    templates = probe_route_templates()
    signals: list[dict[str, Any]] = []
    http_status = _http_status(f"{base_url}{templates['http']}")
    signals.append(
        _signal(
            "http_route_handler",
            http_status is not None and http_status != 404,
            f"HTTP route resolved through the real handler (status {http_status})",
        )
    )
    sse_status = _http_status(f"{base_url}{_resolve_probe_path(templates['sse'])}")
    signals.append(
        _signal(
            "sse_route_handler",
            sse_status is not None and sse_status != 404,
            f"SSE route resolved through the real handler (status {sse_status})",
        )
    )
    ws_path = _resolve_probe_path(templates["websocket"])
    ws_ok, ws_detail = _probe_websocket(f"ws://127.0.0.1:{API_PORT}{ws_path}")
    signals.append(_signal("websocket_route_handshake", ws_ok, ws_detail))
    return signals


def _restart_container(name: str, base_url: str, liveness_path: str) -> tuple[bool, str]:
    """Restart the running API container and require it to serve again.

    This is a real authority handoff: the deployable process is stopped and
    started again against the *same* already-migrated database, and must
    re-advertise liveness through its own entrypoint.
    """

    restarted = _run(["docker", "restart", name], timeout=120)
    if restarted.returncode != 0:
        return False, f"could not restart the API container: {_stderr(restarted)}"
    if not _await_health(base_url, liveness_path):
        return False, "API entrypoint did not become healthy again after restart"
    return True, "API entrypoint restarted and served liveness against the existing schema"


def _probe_server_and_ui(
    image: str,
    *,
    database_url: str,
    repo_dir: Path,
    ui_probe: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Probe the API image and, in the *same* container lifetime, its hosted UI.

    The compiled native UI is served by this container, so the browser capture
    must run while it is still listening; completing and tearing down the API
    container before starting the UI probe would leave nothing to navigate to.
    """

    base_url = f"http://127.0.0.1:{API_PORT}"
    liveness_path = probe_route_templates()["liveness"]
    env = dict(postgres_env(database_url))
    env["MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION"] = "1"

    signals: list[dict[str, Any]] = []
    ui_signals: list[dict[str, Any]] = []
    with _container(image, env=env, repo_dir=repo_dir) as name:
        started = _await_health(base_url, liveness_path)
        signals.append(
            _signal(
                "api_entrypoint_start",
                started,
                f"GET {liveness_path} responded 200"
                if started
                else "API entrypoint did not become healthy",
            )
        )
        if started:
            signals.extend(_probe_server_routes(base_url))
            # The hosted UI is served by this same live container.
            ui_signals = ui_probe(base_url)
            restart_ok, restart_detail = _restart_container(
                name, base_url, liveness_path
            )
        else:
            signals.extend(
                _signal(name_, False, "API entrypoint never became healthy")
                for name_ in (
                    "http_route_handler",
                    "sse_route_handler",
                    "websocket_route_handshake",
                )
            )
            ui_signals = [
                _signal(name_, False, "no hosted API was available to capture")
                for name_ in ("hosted_bootstrap_consumed", "no_root_v1_requests")
            ]
            restart_ok, restart_detail = (
                False,
                "API entrypoint never became healthy, so restart was not observed",
            )
        signals.append(
            _signal("api_restart_against_existing_schema", restart_ok, restart_detail)
        )
    return signals, ui_signals


def _probe_worker(
    image: str, *, database_url: str, temporal_address: str
) -> list[dict[str, Any]]:
    """Probe the worker runtime in the exact image against a real Temporal.

    The worker connects to Temporal before it can advertise readiness, so the
    caller must provide a reachable server; without one the process exits and
    both signals would report a missing dependency rather than an image defect.
    """

    signals: list[dict[str, Any]] = []
    env = dict(postgres_env(database_url))
    env.update(
        {
            "TEMPORAL_ADDRESS": temporal_address,
            "TEMPORAL_WORKER_FLEET": "workflow",
            "MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION": "1",
        }
    )
    command = ["python", "-m", "moonmind.workflows.temporal.worker_runtime"]
    ready_path = probe_route_templates()["worker_ready"]
    ready_url = f"http://127.0.0.1:{WORKER_READY_PORT}{ready_path}"
    with _container(image, command=command, env=env):
        payload: dict[str, Any] | None = None
        for _ in range(45):
            try:
                with urllib.request.urlopen(ready_url, timeout=3) as response:
                    payload = json.loads(response.read())
                break
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                time.sleep(2)
        task_queues = (payload or {}).get("taskQueues") or []
        signals.append(
            _signal(
                "worker_task_queues_advertised",
                bool(task_queues),
                f"worker advertised task queues: {sorted(map(str, task_queues))[:6]}"
                if task_queues
                else "worker did not advertise task queues",
            )
        )
        ready = bool(payload) and payload.get("ready") is True
        signals.append(
            _signal(
                "worker_readiness_capabilities",
                ready,
                "worker readiness endpoint advertised ready capabilities"
                if ready
                else "worker readiness endpoint did not advertise ready",
            )
        )
    return signals


def make_hosted_ui_probe(
    *, node_executable: str = "node", repo_dir: Path = REPO_ROOT, timeout: int = 300
) -> Any:
    """Return a callable that captures the hosted UI served by the exact image.

    The browser controller runs on the CI host (which owns the Playwright
    installation) against the container's hosted origin.  The artifact under
    test is still the image: the compiled bundle, the injected boot payload, and
    the served document all come from the running container.
    """

    def _probe(base_url: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            [
                node_executable,
                "tools/run_omnigent_browser_journey.mjs",
                "--hosted-network-capture",
                "--hosted-url",
                base_url,
            ],
            cwd=repo_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return interpret_hosted_ui_capture(result.returncode, result.stdout)

    return _probe


def interpret_hosted_ui_capture(
    returncode: int, stdout: str
) -> list[dict[str, Any]]:
    """Turn the browser controller's exit status and output into UI signals."""

    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    last = lines[-1] if lines else "no output"
    ok = returncode == 0
    root_v1_observed = "root-v1-request" in (stdout or "")
    return [
        _signal(
            "hosted_bootstrap_consumed",
            ok,
            "compiled UI consumed the hosted boot payload from the deployable image"
            if ok
            else last[:200],
        ),
        _signal(
            "no_root_v1_requests",
            ok and not root_v1_observed,
            "no root /v1 request observed in hosted mode"
            if ok and not root_v1_observed
            else "root /v1 request or capture failure observed",
        ),
    ]


def gather_from_image(
    *,
    image: str,
    database_url: str,
    temporal_address: str,
    repo_dir: Path | str = REPO_ROOT,
    ui_probe: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Gather server, worker, and UI runtime signals from the exact image.

    ``image`` must be a locally resolvable reference to the immutable artifact
    (its ``sha256:<image id>`` content id for a locally built image).
    """

    repo_path = Path(repo_dir)
    probe_ui = ui_probe or make_hosted_ui_probe(repo_dir=repo_path)

    # Each migration scenario gets its own freshly created database, and the
    # server probe runs against the clean database that reached head.
    clean_url = reset_database(database_url, "moonmind_exact_artifact_clean")
    prior_url = reset_database(database_url, "moonmind_exact_artifact_prior")

    clean_ok, clean_detail = _probe_migrations(image, clean_url, prior_schema=False)
    prior_ok, prior_detail = _probe_migrations(image, prior_url, prior_schema=True)

    server, ui = _probe_server_and_ui(
        image, database_url=clean_url, repo_dir=repo_path, ui_probe=probe_ui
    )
    server.append(_signal("migrations_clean_apply", clean_ok, clean_detail))
    server.append(_signal("migrations_prior_schema_upgrade", prior_ok, prior_detail))

    worker = _probe_worker(
        image, database_url=clean_url, temporal_address=temporal_address
    )
    return server, worker, ui
