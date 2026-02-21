"""Helpers for worker runtime/queue mode resolution."""

from __future__ import annotations

import os
from typing import Mapping

ALLOWED_WORKER_RUNTIMES = frozenset({"codex", "gemini", "claude", "universal"})
ALLOWED_GEMINI_CLI_AUTH_MODES = frozenset({"api_key", "oauth"})


def resolve_worker_runtime(
    *,
    default_runtime: str,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve and validate worker runtime mode from environment."""

    env_map = env or os.environ
    runtime = (
        (env_map.get("MOONMIND_WORKER_RUNTIME") or default_runtime).strip().lower()
    )
    if runtime not in ALLOWED_WORKER_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_WORKER_RUNTIMES))
        raise RuntimeError(
            f"Invalid MOONMIND_WORKER_RUNTIME={runtime!r}; expected one of: {allowed}"
        )
    ai_cli = runtime if runtime != "universal" else "universal"
    return runtime, ai_cli


def resolve_worker_queue(
    *,
    default_queue: str,
    legacy_queue_env: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve a worker queue with single-queue-first precedence."""

    env_map = env or os.environ
    queue = (env_map.get("MOONMIND_QUEUE") or "").strip()
    if queue:
        return queue

    if legacy_queue_env:
        legacy = (env_map.get(legacy_queue_env) or "").strip()
        if legacy:
            return legacy

    celery_default = (env_map.get("CELERY_DEFAULT_QUEUE") or "").strip()
    if celery_default:
        return celery_default

    return default_queue.strip() or "moonmind.jobs"


def resolve_gemini_cli_auth_mode(
    *,
    env: Mapping[str, str] | None = None,
    default_mode: str = "api_key",
) -> tuple[str, str]:
    """Resolve Gemini CLI auth mode and return (mode, raw_value)."""

    env_map = env or os.environ
    raw = str(env_map.get("MOONMIND_GEMINI_CLI_AUTH_MODE", default_mode)).strip()
    mode = raw.lower() or default_mode
    if mode not in ALLOWED_GEMINI_CLI_AUTH_MODES:
        return default_mode, raw or mode
    return mode, raw
