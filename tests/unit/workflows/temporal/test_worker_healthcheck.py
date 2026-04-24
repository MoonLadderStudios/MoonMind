"""Unit tests for the Temporal worker healthcheck HTTP server."""

from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest

from moonmind.workflows.temporal.worker_healthcheck import (
    _build_response_body,
    _is_enabled,
    _port,
    start_healthcheck_server,
)

def test_build_response_body_returns_valid_json(monkeypatch):
    """Response body should be valid JSON with required keys."""
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "sandbox")
    body = json.loads(_build_response_body())
    assert body["status"] == "ok"
    assert body["fleet"] == "sandbox"
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], int)

def test_build_response_body_defaults_fleet(monkeypatch):
    """Fleet should default to 'unknown' when env var is missing."""
    monkeypatch.delenv("TEMPORAL_WORKER_FLEET", raising=False)
    body = json.loads(_build_response_body())
    assert body["fleet"] == "unknown"

@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
    ],
)
def test_is_enabled_respects_env(monkeypatch, env_value, expected):
    """WORKER_HEALTHCHECK_ENABLED should control server startup."""
    monkeypatch.setenv("WORKER_HEALTHCHECK_ENABLED", env_value)
    assert _is_enabled() is expected

def test_is_enabled_defaults_to_true(monkeypatch):
    """When no env var is set, healthcheck should be enabled."""
    monkeypatch.delenv("WORKER_HEALTHCHECK_ENABLED", raising=False)
    assert _is_enabled() is True

def test_port_defaults_to_8080(monkeypatch):
    """Default port should be 8080."""
    monkeypatch.delenv("WORKER_HEALTHCHECK_PORT", raising=False)
    assert _port() == 8080

def test_port_reads_env(monkeypatch):
    """Port should be configurable via WORKER_HEALTHCHECK_PORT."""
    monkeypatch.setenv("WORKER_HEALTHCHECK_PORT", "9090")
    assert _port() == 9090

def test_port_ignores_non_numeric(monkeypatch):
    """Non-numeric port values should fall back to default."""
    monkeypatch.setenv("WORKER_HEALTHCHECK_PORT", "abc")
    assert _port() == 8080

@pytest.mark.asyncio
async def test_start_healthcheck_server_disabled(monkeypatch):
    """When disabled, start_healthcheck_server should return None."""
    monkeypatch.setenv("WORKER_HEALTHCHECK_ENABLED", "false")
    server = await start_healthcheck_server()
    assert server is None

@pytest.mark.asyncio
async def test_start_healthcheck_server_responds(monkeypatch):
    """Server should start and respond to HTTP requests on /healthz."""
    monkeypatch.setenv("WORKER_HEALTHCHECK_ENABLED", "true")
    monkeypatch.setenv("WORKER_HEALTHCHECK_PORT", "0")  # Auto-assign port
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "test_fleet")

    server = await start_healthcheck_server()
    assert server is not None

    try:
        # Get the auto-assigned port
        sockets = server.sockets
        assert sockets, "Server should have at least one socket"
        port = sockets[0].getsockname()[1]

        # Make an HTTP request
        url = f"http://127.0.0.1:{port}/healthz"
        loop = asyncio.get_event_loop()
        response_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(url, timeout=5).read()
        )
        body = json.loads(response_bytes)

        assert body["status"] == "ok"
        assert body["fleet"] == "test_fleet"
        assert isinstance(body["uptime_seconds"], int)
    finally:
        server.close()
        await server.wait_closed()
