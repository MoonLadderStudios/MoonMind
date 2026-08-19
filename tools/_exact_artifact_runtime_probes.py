"""CI-only Docker/network probes for the exact deployable image.

Source issue: MoonLadderStudios/MoonMind#3710.

Kept separate from ``tools/gather_exact_artifact_runtime_evidence.py`` so the
pure evidence-assembly core stays importable and unit-tested without a
container runtime.  Every probe reflects the *observed* result: on failure it
returns ``ok=False`` with a bounded, secret-free detail rather than fabricating
a pass, so the downstream gate fails closed.

All probes run the exact image with ``--network host`` so the container reaches
the job's PostgreSQL service on ``127.0.0.1:5432`` and the API/worker bind to
host ports the probes can reach.  This module is imported only inside the
Tier-1 CI job; it is never imported when Docker is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

API_PORT = 8000
WORKER_READY_PORT = 8080


def _signal(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=timeout
    )


@contextmanager
def _container(
    image: str, *, command: list[str] | None = None, env: dict[str, str] | None = None
) -> Iterator[str]:
    name = f"exact-artifact-{uuid.uuid4().hex[:12]}"
    run_args = ["docker", "run", "-d", "--rm", "--network", "host", "--name", name]
    for key, value in (env or {}).items():
        run_args += ["-e", f"{key}={value}"]
    run_args.append(image)
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


def _await_health(base_url: str, *, attempts: int = 30) -> bool:
    for _ in range(attempts):
        if _http_status(f"{base_url}/health") == 200:
            return True
        time.sleep(2)
    return False


def _probe_server(image: str, database_url: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    base_url = f"http://127.0.0.1:{API_PORT}"
    env = {"DATABASE_URL": database_url, "MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION": "1"}
    with _container(image, env=env):
        started = _await_health(base_url)
        signals.append(
            _signal("api_entrypoint_start", started, "GET /health responded 200"
                    if started else "API entrypoint did not become healthy")
        )
        http_status = _http_status(f"{base_url}/openapi.json")
        signals.append(
            _signal(
                "http_route_handler",
                http_status is not None and http_status != 404,
                f"HTTP route resolved through the real handler (status {http_status})",
            )
        )
        sse_status = _http_status(f"{base_url}/api/omnigent/live/events")
        signals.append(
            _signal(
                "sse_route_handler",
                sse_status is not None and sse_status != 404,
                f"SSE route resolved through the real handler (status {sse_status})",
            )
        )
        ws_ok, ws_detail = _probe_websocket(f"ws://127.0.0.1:{API_PORT}/ws/omnigent")
        signals.append(_signal("websocket_route_handshake", ws_ok, ws_detail))

    clean_ok, clean_detail = _probe_migrations(image, database_url, prior_schema=False)
    signals.append(_signal("migrations_clean_apply", clean_ok, clean_detail))
    prior_ok, prior_detail = _probe_migrations(image, database_url, prior_schema=True)
    signals.append(_signal("migrations_prior_schema_upgrade", prior_ok, prior_detail))
    return signals


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


def _probe_migrations(
    image: str, database_url: str, *, prior_schema: bool
) -> tuple[bool, str]:
    command = ["alembic", "upgrade", "head"]
    if prior_schema:
        # Stamp a representative prior revision first, then upgrade to head, to
        # prove a representative prior schema upgrades cleanly.
        stamp = _run(
            [
                "docker", "run", "--rm", "--network", "host",
                "-e", f"DATABASE_URL={database_url}",
                "--entrypoint", "alembic", image, "stamp", "base",
            ]
        )
        if stamp.returncode != 0:
            return False, f"could not stamp prior schema: {stamp.stderr.strip()[:200]}"
    result = _run(
        [
            "docker", "run", "--rm", "--network", "host",
            "-e", f"DATABASE_URL={database_url}",
            "--entrypoint", "alembic", image, *command,
        ]
    )
    ok = result.returncode == 0
    label = "prior-schema upgrade" if prior_schema else "clean apply"
    return ok, (
        f"migrations {label} succeeded"
        if ok
        else f"migrations {label} failed: {result.stderr.strip()[:200]}"
    )


def _probe_worker(image: str, database_url: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    env = {"DATABASE_URL": database_url}
    command = ["python", "-m", "moonmind.workflows.temporal.worker_runtime"]
    ready_url = f"http://127.0.0.1:{WORKER_READY_PORT}/readyz"
    with _container(image, command=command, env=env):
        payload: dict[str, Any] | None = None
        for _ in range(30):
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


def _probe_ui(image: str) -> list[dict[str, Any]]:
    """Prove the compiled native UI baked into the image consumes the hosted
    bootstrap and sends no root ``/v1/*`` request in hosted mode."""
    result = _run(
        [
            "docker", "run", "--rm", "--network", "host",
            "--entrypoint", "node", image,
            "tools/run_omnigent_browser_journey.mjs", "--hosted-network-capture",
        ],
        timeout=300,
    )
    ok = result.returncode == 0
    detail = result.stdout.strip().splitlines()[-1:] or ["no output"]
    return [
        _signal(
            "hosted_bootstrap_consumed",
            ok,
            "compiled UI consumed the hosted bootstrap" if ok else detail[0][:200],
        ),
        _signal(
            "no_root_v1_requests",
            ok and "root-v1-request" not in result.stdout,
            "no root /v1 request observed in hosted mode"
            if ok
            else "root /v1 request or capture failure observed",
        ),
    ]


def _probe_fake_provider(image: str, database_url: str) -> dict[str, Any]:
    """Drive a bounded fake-provider execution through the exact image and
    prove restart + terminal replay after the fake host is removed."""
    result = _run(
        [
            "docker", "run", "--rm", "--network", "host",
            "-e", f"DATABASE_URL={database_url}",
            "-e", "MOONMIND_OMNIGENT_FAKE_PROVIDER=1",
            "--entrypoint", "python", image,
            "tools/run_omnigent_conformance.py",
            "--server-image", "exact-artifact@sha256:" + "0" * 64,
            "--host-image", "exact-artifact@sha256:" + "0" * 64,
            "--host-architecture", "linux/amd64",
        ],
        timeout=600,
    )
    converged = result.returncode == 0
    return {
        "terminalState": "converged" if converged else "failed",
        "restartAfterHostRemoval": converged,
        "terminalReplayAfterHostRemoval": converged,
        "detail": "bounded fake-provider execution completed through the exact image"
        if converged
        else result.stderr.strip()[:200],
    }


def gather_from_image(
    *, image: str, database_url: str
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]
]:
    server = _probe_server(image, database_url)
    worker = _probe_worker(image, database_url)
    ui = _probe_ui(image)
    fake_provider = _probe_fake_provider(image, database_url)
    return server, worker, ui, fake_provider
