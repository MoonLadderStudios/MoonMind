"""Helpers for resolving default task runtime settings.

Canonical managed runtime IDs are: ``codex_cli``, ``gemini_cli``,
``claude_code``.  Short aliases (``codex``, ``claude``) are normalized to
these canonical forms before any lookup.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

DEFAULT_TASK_RUNTIME = "codex_cli"
DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"

# Canonical runtime ids as primary keys.
_DEFAULT_RUNTIME_MODELS: dict[str, str] = {
    "codex_cli": "gpt-5.4",
    "gemini_cli": "gemini-3.1-pro-preview",
    "claude_code": "claude-opus-4-7",
}
_DEFAULT_RUNTIME_EFFORTS: dict[str, str] = {
    "codex_cli": "high",
}

# Short aliases → canonical ids.
_RUNTIME_ALIASES: dict[str, str] = {
    "codex": "codex_cli",
    "claude": "claude_code",
}

_RUNTIME_MODEL_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "codex_cli": ("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
    "gemini_cli": ("MOONMIND_GEMINI_MODEL", "GEMINI_MODEL"),
    "claude_code": ("MOONMIND_CLAUDE_MODEL", "CLAUDE_MODEL"),
    "jules": ("MOONMIND_JULES_MODEL", "JULES_MODEL"),
}

_RUNTIME_EFFORT_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "codex_cli": (
        "MOONMIND_CODEX_EFFORT",
        "CODEX_MODEL_REASONING_EFFORT",
        "MODEL_REASONING_EFFORT",
    ),
    "gemini_cli": ("MOONMIND_GEMINI_EFFORT", "GEMINI_REASONING_EFFORT"),
    "claude_code": ("MOONMIND_CLAUDE_EFFORT", "CLAUDE_REASONING_EFFORT"),
    "jules": ("MOONMIND_JULES_EFFORT", "JULES_REASONING_EFFORT"),
}

def _clean_optional_string(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None

def normalize_runtime_id(runtime: object) -> str:
    """Return the canonical managed runtime id for *runtime*.

    Applies short aliases (``codex`` → ``codex_cli``, ``claude`` →
    ``claude_code``) and lowercases the result.  Unknown values are
    returned as-is (lowercased) so callers can handle them gracefully.
    """
    key = (_clean_optional_string(runtime) or DEFAULT_TASK_RUNTIME).lower()
    return _RUNTIME_ALIASES.get(key, key)

def resolve_default_task_runtime(
    workflow_settings: Any,
    *,
    fallback: str = DEFAULT_TASK_RUNTIME,
) -> str:
    """Return the configured default runtime with a stable fallback."""

    configured = _clean_optional_string(
        getattr(workflow_settings, "default_task_runtime", None)
    )
    raw = (configured or fallback).lower()
    return normalize_runtime_id(raw)

def resolve_runtime_defaults(
    runtime: object,
    *,
    workflow_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Return default model/effort values for a normalized runtime."""

    runtime_key = normalize_runtime_id(runtime)
    resolved_env = env if env is not None else os.environ

    if runtime_key == "codex_cli" and workflow_settings is not None:
        configured_model = _clean_optional_string(
            getattr(workflow_settings, "codex_model", None)
        )
        configured_effort = _clean_optional_string(
            getattr(workflow_settings, "codex_effort", None)
        )
        if configured_model or configured_effort:
            return (
                configured_model or _DEFAULT_RUNTIME_MODELS.get(runtime_key),
                configured_effort or _DEFAULT_RUNTIME_EFFORTS.get(runtime_key),
            )

    model = next(
        (
            candidate
            for key in _RUNTIME_MODEL_ENV_KEYS.get(runtime_key, ())
            if (candidate := _clean_optional_string(resolved_env.get(key))) is not None
        ),
        _DEFAULT_RUNTIME_MODELS.get(runtime_key),
    )
    effort = next(
        (
            candidate
            for key in _RUNTIME_EFFORT_ENV_KEYS.get(runtime_key, ())
            if (candidate := _clean_optional_string(resolved_env.get(key))) is not None
        ),
        _DEFAULT_RUNTIME_EFFORTS.get(runtime_key),
    )
    return model, effort

__all__ = [
    "DEFAULT_REPOSITORY",
    "DEFAULT_TASK_RUNTIME",
    "normalize_runtime_id",
    "resolve_default_task_runtime",
    "resolve_runtime_defaults",
]
