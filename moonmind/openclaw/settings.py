"""Runtime gate for OpenClaw external agent integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"

OPENCLAW_DISABLED_MESSAGE = (
    "targetRuntime=openclaw requires OPENCLAW_ENABLED=true with "
    "OPENCLAW_GATEWAY_TOKEN configured (OPENCLAW_GATEWAY_URL optional; "
    f"defaults to {DEFAULT_GATEWAY_URL})"
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class OpenClawRuntimeGate:
    """Whether OpenClaw is enabled and required env vars are present."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _enabled_from_env(*, env: Mapping[str, Any]) -> bool:
    raw = _clean(env.get("OPENCLAW_ENABLED"))
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False


def build_openclaw_gate(
    *,
    env: Mapping[str, Any] | None = None,
    error_message: str = OPENCLAW_DISABLED_MESSAGE,
) -> OpenClawRuntimeGate:
    """Return gate state for OpenClaw (env-driven)."""

    source = env if env is not None else os.environ
    enabled_flag = _enabled_from_env(env=source)
    token = _clean(source.get("OPENCLAW_GATEWAY_TOKEN"))

    missing: list[str] = []
    if not enabled_flag:
        missing.append("OPENCLAW_ENABLED")
    if not token:
        missing.append("OPENCLAW_GATEWAY_TOKEN")

    return OpenClawRuntimeGate(
        enabled=len(missing) == 0,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_openclaw_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    return build_openclaw_gate(env=env).enabled


def resolved_gateway_url(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured gateway base URL or local dev default."""

    source = env if env is not None else os.environ
    url = _clean(source.get("OPENCLAW_GATEWAY_URL"))
    return url or DEFAULT_GATEWAY_URL


def resolved_default_model(*, env: Mapping[str, Any] | None = None) -> str:
    source = env if env is not None else os.environ
    m = _clean(source.get("OPENCLAW_DEFAULT_MODEL"))
    return m or "openclaw-default"


def resolved_timeout_seconds(*, env: Mapping[str, Any] | None = None) -> int:
    source = env if env is not None else os.environ
    raw = _clean(source.get("OPENCLAW_TIMEOUT_SECONDS"))
    if not raw:
        return 3600
    try:
        return max(60, int(raw))
    except ValueError:
        return 3600


__all__ = [
    "DEFAULT_GATEWAY_URL",
    "OPENCLAW_DISABLED_MESSAGE",
    "OpenClawRuntimeGate",
    "build_openclaw_gate",
    "is_openclaw_enabled",
    "resolved_default_model",
    "resolved_gateway_url",
    "resolved_timeout_seconds",
]
