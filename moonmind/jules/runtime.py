"""Shared helpers for gating Jules runtime support on API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

JULES_RUNTIME_DISABLED_MESSAGE = "targetRuntime=jules requires JULES_ENABLED=true with JULES_API_URL and JULES_API_KEY configured"
"""Canonical error text for disabled Jules runtime configuration."""

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class RuntimeGateState:
    """Represents whether Jules runtime is enabled plus diagnostics context."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


@dataclass(frozen=True, slots=True)
class JulesProviderProfile:
    """Resolved runtime profile used by provider-neutral integrations monitoring."""

    integration_name: str
    activity_queue: str
    callback_mode: str
    supports_cancel: bool
    initial_poll_seconds: int
    max_poll_seconds: int
    callback_rate_limit_per_window: int
    callback_rate_limit_window_seconds: int


def _clean_value(value: object | None) -> str:
    return str(value or "").strip()


def _resolve_enabled_flag(
    *, enabled: bool | None = None, env: Mapping[str, Any] | None = None
) -> bool:
    if isinstance(enabled, bool):
        return enabled
    source = env if env is not None else os.environ
    raw = _clean_value(source.get("JULES_ENABLED"))  # type: ignore[arg-type]
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False


def build_runtime_gate_state(
    *,
    enabled: bool | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    env: Mapping[str, Any] | None = None,
    error_message: str = JULES_RUNTIME_DISABLED_MESSAGE,
) -> RuntimeGateState:
    """Return normalized gate state, including missing configuration fields."""

    source = env if env is not None else os.environ
    runtime_enabled = _resolve_enabled_flag(enabled=enabled, env=source)
    resolved_url = _clean_value(api_url) or _clean_value(
        source.get("JULES_API_URL")  # type: ignore[arg-type]
    )
    resolved_key = _clean_value(api_key) or _clean_value(
        source.get("JULES_API_KEY")  # type: ignore[arg-type]
    )

    missing: list[str] = []
    if not runtime_enabled:
        missing.append("JULES_ENABLED")
    if not resolved_url:
        missing.append("JULES_API_URL")
    if not resolved_key:
        missing.append("JULES_API_KEY")

    return RuntimeGateState(
        enabled=not missing,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_jules_runtime_enabled(
    *,
    enabled: bool | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    env: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether Jules runtime should be enabled for queue execution."""

    return build_runtime_gate_state(
        enabled=enabled,
        api_url=api_url,
        api_key=api_key,
        env=env,
    ).enabled


def build_jules_provider_profile(
    *,
    activity_queue: str = "mm.activity.integrations",
    callback_mode: str = "preferred",
    supports_cancel: bool = True,
    initial_poll_seconds: int = 5,
    max_poll_seconds: int = 300,
    callback_rate_limit_per_window: int = 30,
    callback_rate_limit_window_seconds: int = 60,
) -> JulesProviderProfile:
    """Return the default Jules monitoring profile without altering runtime semantics."""

    return JulesProviderProfile(
        integration_name="jules",
        activity_queue=activity_queue,
        callback_mode=callback_mode,
        supports_cancel=supports_cancel,
        initial_poll_seconds=max(1, int(initial_poll_seconds)),
        max_poll_seconds=max(1, int(max_poll_seconds)),
        callback_rate_limit_per_window=max(1, int(callback_rate_limit_per_window)),
        callback_rate_limit_window_seconds=max(
            1, int(callback_rate_limit_window_seconds)
        ),
    )


__all__ = [
    "JULES_RUNTIME_DISABLED_MESSAGE",
    "JulesProviderProfile",
    "RuntimeGateState",
    "build_jules_provider_profile",
    "build_runtime_gate_state",
    "is_jules_runtime_enabled",
]
