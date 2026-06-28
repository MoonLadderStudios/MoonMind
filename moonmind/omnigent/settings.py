"""Runtime gate for the Omnigent external provider integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_OMNIGENT_ENDPOINT_REF = "default"
DEFAULT_OMNIGENT_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS = 120

OMNIGENT_DISABLED_MESSAGE = (
    "targetRuntime=omnigent requires OMNIGENT_ENABLED=true with "
    "OMNIGENT_SERVER_URL configured"
)

_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class OmnigentRuntimeGate:
    """Whether Omnigent is enabled and minimally configured."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _enabled_from_env(*, env: Mapping[str, Any]) -> bool:
    return _clean(env.get("OMNIGENT_ENABLED")).lower() in _TRUE_VALUES


def build_omnigent_gate(
    *,
    env: Mapping[str, Any] | None = None,
    error_message: str = OMNIGENT_DISABLED_MESSAGE,
) -> OmnigentRuntimeGate:
    """Return gate state for Omnigent."""

    source = env if env is not None else os.environ
    missing: list[str] = []
    if not _enabled_from_env(env=source):
        missing.append("OMNIGENT_ENABLED")
    if not _clean(source.get("OMNIGENT_SERVER_URL")):
        missing.append("OMNIGENT_SERVER_URL")
    return OmnigentRuntimeGate(
        enabled=len(missing) == 0,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_omnigent_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    return build_omnigent_gate(env=env).enabled


def resolved_server_url(*, env: Mapping[str, Any] | None = None) -> str:
    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_SERVER_URL"))


def resolved_api_token(*, env: Mapping[str, Any] | None = None) -> str | None:
    source = env if env is not None else os.environ
    token = _clean(source.get("OMNIGENT_API_TOKEN"))
    return token or None


def resolved_default_agent_name(*, env: Mapping[str, Any] | None = None) -> str | None:
    source = env if env is not None else os.environ
    value = _clean(source.get("OMNIGENT_DEFAULT_AGENT_NAME"))
    return value or None


def resolved_request_timeout_seconds(*, env: Mapping[str, Any] | None = None) -> int:
    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_REQUEST_TIMEOUT_SECONDS"))
    if not raw:
        return DEFAULT_OMNIGENT_REQUEST_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_OMNIGENT_REQUEST_TIMEOUT_SECONDS


def resolved_stream_heartbeat_timeout_seconds(
    *, env: Mapping[str, Any] | None = None
) -> int:
    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS"))
    if not raw:
        return DEFAULT_OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS


__all__ = [
    "DEFAULT_OMNIGENT_ENDPOINT_REF",
    "DEFAULT_OMNIGENT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS",
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_api_token",
    "resolved_default_agent_name",
    "resolved_request_timeout_seconds",
    "resolved_server_url",
    "resolved_stream_heartbeat_timeout_seconds",
]
