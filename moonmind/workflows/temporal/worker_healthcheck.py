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

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8080

_start_time: float = time.monotonic()


@dataclass(slots=True)
class WorkerHealthState:
    """Process liveness and executable-worker readiness are separate signals."""

    temporal_connected: bool = False
    workers_constructed: bool = False
    pollers_started: bool = False
    readiness_metadata: dict[str, Any] = field(default_factory=dict)
    startup_error: str | None = None

    @property
    def ready(self) -> bool:
        return (
            self.temporal_connected
            and self.workers_constructed
            and self.pollers_started
            and self.startup_error is None
        )


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
        body: dict[str, Any] = {
            "status": "ok",
            "live": True,
            "fleet": fleet,
            "uptime_seconds": uptime,
        }
    else:
        current = state or WorkerHealthState()
        body = {
            **current.readiness_metadata,
            "status": "ready" if current.ready else "not_ready",
            "ready": current.ready,
            "fleet": fleet,
            "uptime_seconds": uptime,
            "startup": {
                "temporalConnected": current.temporal_connected,
                "workersConstructed": current.workers_constructed,
                "pollersStarted": current.pollers_started,
            },
        }
        if current.startup_error:
            body["reasonCode"] = "worker_startup_failed"
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
        status = (
            b"200 OK" if not readiness or state.ready else b"503 Service Unavailable"
        )

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
) -> asyncio.Server | None:
    """Start the health-check HTTP server as a background asyncio task.

    Returns the ``asyncio.Server`` so callers can close it on shutdown,
    or ``None`` if the server is disabled via environment variable.
    """
    if not _is_enabled():
        logger.info("Worker healthcheck server disabled via WORKER_HEALTHCHECK_ENABLED")
        return None

    global _start_time
    _start_time = time.monotonic()

    port = _port()
    health_state = state or WorkerHealthState()
    server = await asyncio.start_server(
        lambda reader, writer: _handle_connection(reader, writer, health_state),
        "0.0.0.0",
        port,
    )
    logger.info("Worker healthcheck server listening on port %d", port)
    return server
