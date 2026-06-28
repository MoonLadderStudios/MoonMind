"""Runtime gate for Omnigent external agent integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_OMNIGENT_SERVER_URL = "http://127.0.0.1:8000"

OMNIGENT_DISABLED_MESSAGE = (
    "targetRuntime=omnigent requires OMNIGENT_ENABLED=true with "
    "OMNIGENT_SERVER_URL configured"
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class OmnigentRuntimeGate:
    """Whether Omnigent is enabled and required env vars are present."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _enabled_from_env(*, env: Mapping[str, Any]) -> bool:
    raw = _clean(env.get("OMNIGENT_ENABLED"))
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False


def build_omnigent_gate(
    *,
    env: Mapping[str, Any] | None = None,
    error_message: str = OMNIGENT_DISABLED_MESSAGE,
) -> OmnigentRuntimeGate:
    """Return gate state for Omnigent (env-driven)."""

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
    return _clean(source.get("OMNIGENT_SERVER_URL")) or DEFAULT_OMNIGENT_SERVER_URL


def resolved_request_timeout_seconds(*, env: Mapping[str, Any] | None = None) -> int:
    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_REQUEST_TIMEOUT_SECONDS"))
    if not raw:
        return 30
    try:
        return max(5, int(raw))
    except ValueError:
        return 30


__all__ = [
    "DEFAULT_OMNIGENT_SERVER_URL",
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_request_timeout_seconds",
    "resolved_server_url",
]
