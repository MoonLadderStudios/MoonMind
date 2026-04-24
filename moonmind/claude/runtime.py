"""Shared helpers for gating Claude runtime support on Anthropic API keys."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

CLAUDE_API_KEY_ENV_ALIASES: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
)
CLAUDE_RUNTIME_DISABLED_MESSAGE = (
    "targetRuntime=claude requires ANTHROPIC_API_KEY to be configured"
)
"""Canonical error text for disabled Claude runtime (ANTHROPIC/CLAUDE API key missing)."""

@dataclass(frozen=True, slots=True)
class RuntimeGateState:
    """Represents whether Claude runtime is enabled plus context for diagnostics."""

    enabled: bool
    source_env: str | None
    error_message: str

def _clean_value(value: object | None) -> str:
    return str(value or "").strip()

def resolve_anthropic_api_key(
    *, api_key: str | None = None, env: Mapping[str, Any] | None = None
) -> str:
    """Return the configured Anthropic API key from args or environment aliases."""

    candidate = _clean_value(api_key)
    if candidate:
        return candidate

    source = env if env is not None else os.environ
    for key in CLAUDE_API_KEY_ENV_ALIASES:
        candidate = _clean_value(source.get(key))  # type: ignore[arg-type]
        if candidate:
            return candidate
    return ""

def build_runtime_gate_state(
    *,
    api_key: str | None = None,
    env: Mapping[str, Any] | None = None,
    error_message: str = CLAUDE_RUNTIME_DISABLED_MESSAGE,
) -> RuntimeGateState:
    """Return normalized gate state, including whether a key is present and its source."""

    candidate = _clean_value(api_key)
    if candidate:
        return RuntimeGateState(
            enabled=True, source_env="argument", error_message=error_message
        )

    source = env if env is not None else os.environ
    for key in CLAUDE_API_KEY_ENV_ALIASES:
        candidate = _clean_value(source.get(key))  # type: ignore[arg-type]
        if candidate:
            return RuntimeGateState(
                enabled=True, source_env=key, error_message=error_message
            )

    return RuntimeGateState(
        enabled=True, source_env="unconditional", error_message=error_message
    )

def is_claude_runtime_enabled(
    *, api_key: str | None = None, env: Mapping[str, Any] | None = None
) -> bool:
    """Return whether Claude runtime should be enabled based on API key presence."""

    return True

__all__ = [
    "CLAUDE_API_KEY_ENV_ALIASES",
    "CLAUDE_RUNTIME_DISABLED_MESSAGE",
    "RuntimeGateState",
    "build_runtime_gate_state",
    "resolve_anthropic_api_key",
    "is_claude_runtime_enabled",
]
