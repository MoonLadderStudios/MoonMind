"""Omnigent external-agent runtime gate for MM-991."""

from __future__ import annotations

from moonmind.omnigent.settings import (
    DEFAULT_OMNIGENT_SERVER_URL,
    OMNIGENT_DISABLED_MESSAGE,
    OmnigentRuntimeGate,
    build_omnigent_gate,
    is_omnigent_enabled,
    resolved_request_timeout_seconds,
    resolved_server_url,
)

__all__ = [
    "DEFAULT_OMNIGENT_SERVER_URL",
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_request_timeout_seconds",
    "resolved_server_url",
]
