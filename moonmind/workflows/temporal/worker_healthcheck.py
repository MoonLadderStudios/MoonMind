"""Lightweight HTTP health-check server for Temporal workers.

Runs as a background asyncio task alongside the Temporal worker polling loop
so Docker Compose healthcheck probes can verify the process is responsive.

Uses only the Python standard library (``asyncio.start_server``) to avoid
adding external dependencies.

Environment variables:
    WORKER_HEALTHCHECK_PORT     Port to listen on (default 8080).
    WORKER_HEALTHCHECK_ENABLED  Set to "false" to disable (default "true").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from moonmind.workflows.temporal.worker_code_identity import (
    UNKNOWN,
    WorkerCodeIdentity,
    compare_code_identities,
    resolve_checkout_code_identity,
)

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8080
_IDENTITY_REFRESH_SECONDS = 5.0
_IDENTITY_MAX_AGE_SECONDS = 30.0

_start_time: float = time.monotonic()


@dataclass(slots=True)
class WorkerHealthState:
    """Process liveness and executable-worker readiness are separate signals."""

    temporal_connected: bool = False
    workers_constructed: bool = False
    pollers_started: bool = False
    readiness_metadata: dict[str, Any] = field(default_factory=dict)
    startup_error: str | None = None
    # Startup-recorded code identity (MoonLadderStudios/MoonMind#4224): the git
    # revision (or package digest) of the modules this process imported. It is
    # compared with an off-loop, bounded-age checkout snapshot so a
    # bind-mounted worker running stale modules reports ``stale_code`` instead
    # of ready. ``None`` means the identity was never recorded and the worker
    # reports ``unknown``, never healthy.
    code_revision: str | None = None
    code_digest: str | None = None
    code_identity_source: str = "unknown"
    checkout_identity: WorkerCodeIdentity = field(default_factory=WorkerCodeIdentity)
    identity_checked_at: float | None = None
    identity_generation: int = 0

    @property
    def ready(self) -> bool:
        return (
            self.temporal_connected
            and self.workers_constructed
            and self.pollers_started
            and self.startup_error is None
        )

    def code_identity(self) -> dict[str, Any]:
        """Return this worker's startup-recorded code identity payload."""
        startup = WorkerCodeIdentity(
            revision=(self.code_revision or "").strip() or None,
            digest=(self.code_digest or "").strip() or None,
            source=(self.code_identity_source or "").strip() or UNKNOWN,
        )
        current = self.checkout_identity
        age = (
            max(0.0, time.monotonic() - self.identity_checked_at)
            if self.identity_checked_at is not None else None
        )
        immutable = self.readiness_metadata.get("immutableReleaseIdentity") is True
        fresh = age is not None and (immutable or age <= _IDENTITY_MAX_AGE_SECONDS)
        status = compare_code_identities(startup, current) if fresh else UNKNOWN
        payload: dict[str, Any] = {
            "codeRevision": startup.revision or UNKNOWN,
            "codeDigest": startup.digest or UNKNOWN,
            "codeIdentitySource": startup.source,
            "codeIdentityStatus": status,
            "checkoutRevision": current.revision or UNKNOWN,
            "codeIdentityAgeSeconds": age,
            "codeIdentityMaxAgeSeconds": None if immutable else _IDENTITY_MAX_AGE_SECONDS,
            "codeIdentityGeneration": self.identity_generation,
        }
        if status == "stale":
            payload["reasonCode"] = "stale_code"
            payload["staleCode"] = {
                "worker": (self.readiness_metadata.get("fleet") if isinstance(self.readiness_metadata, dict) else None)
                or os.environ.get("TEMPORAL_WORKER_FLEET", "unknown"),
                "startupRevision": startup.revision or UNKNOWN,
                "currentRevision": current.revision or UNKNOWN,
            }
        elif status == UNKNOWN:
            payload["reasonCode"] = "code_identity_expired" if age is not None and not fresh else "code_identity_unknown"
        return payload

    def record_checkout_identity(self, identity: WorkerCodeIdentity, *, started_at: float) -> None:
        # Age starts before the scan: a stalled scan cannot renew stale evidence.
        self.checkout_identity = identity
        self.identity_checked_at = started_at
        self.identity_generation += 1


async def _refresh_code_identity(state: WorkerHealthState) -> None:
    """One scan at a time, independent of HTTP request volume and the poll loop."""
    while True:
        await asyncio.sleep(_IDENTITY_REFRESH_SECONDS)
        if state.readiness_metadata.get("immutableReleaseIdentity") is True:
            continue
        started_at = time.monotonic()
        try:
            identity = await asyncio.to_thread(resolve_checkout_code_identity)
        except Exception:
            logger.exception("Worker code identity refresh failed")
            identity = WorkerCodeIdentity()
        state.record_checkout_identity(identity, started_at=started_at)


@dataclass(slots=True)
class HealthcheckServer:
    """Own the HTTP listener and freshness task as one shutdown boundary."""

    server: asyncio.Server
    refresh_task: asyncio.Task[None]

    @property
    def sockets(self):
        return self.server.sockets

    def close(self) -> None:
        self.server.close()
        self.refresh_task.cancel()

    async def wait_closed(self) -> None:
        await self.server.wait_closed()
        await asyncio.gather(self.refresh_task, return_exceptions=True)


def _is_enabled() -> bool:
    return os.environ.get("WORKER_HEALTHCHECK_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _port() -> int:
    raw = os.environ.get("WORKER_HEALTHCHECK_PORT", "")
    if raw.strip().isdigit():
        return int(raw.strip())
    return _DEFAULT_PORT


def _build_response_body(
    state: WorkerHealthState | None = None,
    *,
    readiness: bool = False,
) -> bytes:
    """Build the JSON health response payload."""
    fleet = os.environ.get("TEMPORAL_WORKER_FLEET", "unknown")
    uptime = int(time.monotonic() - _start_time)

    if not readiness:
        from moonmind.workflows.temporal.worker_code_identity import UNKNOWN

        body: dict[str, Any] = {
            "status": "ok",
            "live": True,
            "fleet": fleet,
            "uptime_seconds": uptime,
            "codeRevision": (state.code_revision if state else None) or UNKNOWN,
            "codeIdentitySource": (state.code_identity_source if state else None)
            or UNKNOWN,
        }
    else:
        current = state or WorkerHealthState()
        code_identity = current.code_identity()
        ready = current.ready and code_identity["codeIdentityStatus"] == "healthy"
        body = {
            **current.readiness_metadata,
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "fleet": fleet,
            "uptime_seconds": uptime,
            "startup": {
                "temporalConnected": current.temporal_connected,
                "workersConstructed": current.workers_constructed,
                "pollersStarted": current.pollers_started,
            },
            **code_identity,
        }
        if current.startup_error:
            body["reasonCode"] = "worker_startup_failed"
        # A worker running stale modules must not look ready: mixed-version
        # deployments silently admit work under old rules and finalize it
        # under new ones (MoonLadderStudios/MoonMind#4224).
        if code_identity.get("codeIdentityStatus") == "stale" and "reasonCode" not in body:
            body["reasonCode"] = "stale_code"
    return json.dumps(body).encode("utf-8")


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: WorkerHealthState,
) -> None:
    """Handle a single HTTP connection — respond to any request with health JSON."""
    try:
        # Read request line (we don't need the full request)
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)

        # Consume remaining headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        parts = request_line.decode("ascii", errors="ignore").split()
        path = parts[1] if len(parts) >= 2 else "/healthz"
        readiness = path == "/readyz"
        payload = _build_response_body(state, readiness=readiness)
        if readiness:
            try:
                body_json = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body_json = {}
            status = (
                b"200 OK" if body_json.get("ready") is True else b"503 Service Unavailable"
            )
        else:
            status = b"200 OK"

        response = (
            b"HTTP/1.1 " + status + b"\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"\r\n" + payload
        )
        writer.write(response)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def start_healthcheck_server(
    state: WorkerHealthState | None = None,
) -> HealthcheckServer | None:
    """Start the health-check HTTP server as a background asyncio task.

    Returns the listener and refresh task owner so callers can close both,
    or ``None`` if the server is disabled via environment variable.
    """
    if not _is_enabled():
        logger.info("Worker healthcheck server disabled via WORKER_HEALTHCHECK_ENABLED")
        return None

    global _start_time
    _start_time = time.monotonic()

    port = _port()
    health_state = state or WorkerHealthState()
    health_state.record_checkout_identity(
        WorkerCodeIdentity(
            revision=health_state.code_revision,
            digest=health_state.code_digest,
            source=health_state.code_identity_source,
        ),
        started_at=time.monotonic(),
    )
    server = await asyncio.start_server(
        lambda reader, writer: _handle_connection(reader, writer, health_state),
        "0.0.0.0",
        port,
    )
    logger.info("Worker healthcheck server listening on port %d", port)
    return HealthcheckServer(server, asyncio.create_task(_refresh_code_identity(health_state)))
