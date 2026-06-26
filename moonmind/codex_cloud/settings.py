"""Shared helpers for gating Codex Cloud runtime support on API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

CODEX_CLOUD_DISABLED_MESSAGE = (
    "targetRuntime=codex_cloud requires CODEX_CLOUD_ENABLED=true "
    "with CODEX_CLOUD_API_URL and CODEX_CLOUD_API_KEY configured"
)
"""Canonical error text for disabled Codex Cloud runtime configuration."""

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

@dataclass(frozen=True, slots=True)
class CodexCloudRuntimeGate:
    """Represents whether Codex Cloud runtime is enabled plus diagnostics context."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str

def _clean_value(value: object | None) -> str:
    return str(value or "").strip()

def _resolve_enabled_flag(
    *, enabled: bool | None = None, env: Mapping[str, Any] | None = None
) -> bool:
    if isinstance(enabled, bool):
        return enabled
    source = env if env is not None else os.environ
    raw = _clean_value(source.get("CODEX_CLOUD_ENABLED"))  # type: ignore[arg-type]
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False

def build_codex_cloud_gate(
    *,
    enabled: bool | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    env: Mapping[str, Any] | None = None,
    error_message: str = CODEX_CLOUD_DISABLED_MESSAGE,
) -> CodexCloudRuntimeGate:
    """Return normalized gate state for Codex Cloud runtime."""

    source = env if env is not None else os.environ
    runtime_enabled = _resolve_enabled_flag(enabled=enabled, env=source)
    resolved_url = _clean_value(api_url) or _clean_value(
        source.get("CODEX_CLOUD_API_URL")  # type: ignore[arg-type]
    )
    resolved_key = _clean_value(api_key) or _clean_value(
        source.get("CODEX_CLOUD_API_KEY")  # type: ignore[arg-type]
    )

    missing: list[str] = []
    if not runtime_enabled:
        missing.append("CODEX_CLOUD_ENABLED")
    if not resolved_url:
        missing.append("CODEX_CLOUD_API_URL")
    if not resolved_key:
        missing.append("CODEX_CLOUD_API_KEY")

    return CodexCloudRuntimeGate(
        enabled=not missing,
        missing=tuple(missing),
        error_message=error_message,
    )

def is_codex_cloud_enabled(
    *,
    enabled: bool | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    env: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether Codex Cloud runtime should be enabled."""

    return build_codex_cloud_gate(
        enabled=enabled,
        api_url=api_url,
        api_key=api_key,
        env=env,
    ).enabled

__all__ = [
    "CODEX_CLOUD_DISABLED_MESSAGE",
    "CodexCloudRuntimeGate",
    "build_codex_cloud_gate",
    "is_codex_cloud_enabled",
]
