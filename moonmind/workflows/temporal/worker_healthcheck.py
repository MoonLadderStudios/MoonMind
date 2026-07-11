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
from dataclasses import dataclass, field
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8080

_start_time: float = time.monotonic()


@dataclass(slots=True)
class WorkerReadiness:
    """Mutable process-local readiness evidence set only after worker construction."""

    ready: bool = False
    task_queues: tuple[str, ...] = ()
    workflow_types: tuple[str, ...] = ()
    registry_fingerprint: str | None = None
    build_id: str = field(
        default_factory=lambda: os.environ.get("MOONMIND_BUILD_ID", "unknown")
    )


_readiness = WorkerReadiness()


def mark_worker_ready(
    *,
    task_queues: tuple[str, ...],
    workflow_types: tuple[str, ...],
    registry_fingerprint: str | None,
) -> None:
    _readiness.task_queues = task_queues
    _readiness.workflow_types = workflow_types
    _readiness.registry_fingerprint = registry_fingerprint
    _readiness.ready = True


def mark_worker_not_ready() -> None:
    _readiness.ready = False


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

def _build_response_body(*, readiness: bool = False) -> bytes:
    """Build the JSON health response payload."""
    fleet = os.environ.get("TEMPORAL_WORKER_FLEET", "unknown")
    uptime = int(time.monotonic() - _start_time)

    body: dict[str, Any] = {
        "status": (
            "ready" if _readiness.ready else ("not_ready" if readiness else "ok")
        ),
        "fleet": fleet,
        "uptime_seconds": uptime,
        "ready": _readiness.ready,
        "build_id": _readiness.build_id,
    }
    if readiness:
        body.update(
            task_queues=list(_readiness.task_queues),
            workflow_types=list(_readiness.workflow_types),
            registry_fingerprint=_readiness.registry_fingerprint,
        )
    return json.dumps(body).encode("utf-8")

async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
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

        is_readiness = request_line.split(b" ", 2)[1:2] == [b"/readyz"]
        payload = _build_response_body(readiness=is_readiness)
        status_line = b"HTTP/1.1 200 OK\r\n"
        if is_readiness and not _readiness.ready:
            status_line = b"HTTP/1.1 503 Service Unavailable\r\n"

        response = (
            status_line
            +
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

async def start_healthcheck_server() -> asyncio.Server | None:
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
    server = await asyncio.start_server(_handle_connection, "0.0.0.0", port)
    logger.info("Worker healthcheck server listening on port %d", port)
    return server
