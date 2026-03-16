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
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8080

_start_time: float = time.monotonic()


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


def _build_response_body() -> bytes:
    """Build the JSON health response payload."""
    fleet = os.environ.get("TEMPORAL_WORKER_FLEET", "unknown")
    uptime = int(time.monotonic() - _start_time)

    body: dict[str, Any] = {
        "status": "ok",
        "fleet": fleet,
        "uptime_seconds": uptime,
    }
    return json.dumps(body).encode("utf-8")


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a single HTTP connection — respond to any request with health JSON."""
    try:
        # Read request line (we don't need the full request)
        await asyncio.wait_for(reader.readline(), timeout=5.0)

        # Consume remaining headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        payload = _build_response_body()

        response = (
            b"HTTP/1.1 200 OK\r\n"
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
