"""Runtime gate for the Omnigent external agent integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

OMNIGENT_DISABLED_MESSAGE = (
    "agentId=omnigent requires OMNIGENT_ENABLED=true with "
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
    if value is None:
        return ""
    return str(value).strip()


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
    enabled_flag = _enabled_from_env(env=source)
    raw_enabled = source.get("OMNIGENT_ENABLED")
    server_url = _clean(source.get("OMNIGENT_SERVER_URL"))

    missing: list[str] = []
    if raw_enabled is None or _clean(raw_enabled) == "":
        missing.append("OMNIGENT_ENABLED")
        if not server_url:
            missing.append("OMNIGENT_SERVER_URL")
    elif enabled_flag and not server_url:
        missing.append("OMNIGENT_SERVER_URL")

    return OmnigentRuntimeGate(
        enabled=enabled_flag and len(missing) == 0,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_omnigent_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    return build_omnigent_gate(env=env).enabled


def resolved_server_url(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured Omnigent server URL."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_SERVER_URL"))


__all__ = [
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "resolved_server_url",
]
