"""Helpers for worker runtime/queue mode resolution."""

from __future__ import annotations

import os
from typing import Callable, Literal
from typing import Mapping

ALLOWED_WORKER_RUNTIMES = frozenset({"codex", "gemini", "claude", "universal"})
ALLOWED_GEMINI_CLI_AUTH_MODES = frozenset({"api_key", "oauth"})
GeminiHomeValidationIssue = Literal[
    "missing_for_oauth",
    "not_directory",
    "not_writable_for_oauth",
]


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
    mode = raw.lower() if raw else default_mode
    if mode not in ALLOWED_GEMINI_CLI_AUTH_MODES:
        return default_mode, raw
    return mode, raw


def is_invalid_gemini_cli_auth_mode(raw_value: str) -> bool:
    """Return whether raw auth-mode input is an unsupported non-empty value."""

    raw = str(raw_value).strip()
    return bool(raw) and raw.lower() not in ALLOWED_GEMINI_CLI_AUTH_MODES


def summarize_untrusted_auth_mode_value(raw_value: str) -> str:
    """Return a safe summary for untrusted auth-mode inputs."""

    raw = str(raw_value).strip()
    if not raw:
        return "<empty>"
    return f"<redacted:{len(raw)} chars>"


def format_invalid_gemini_cli_auth_mode_error(raw_value: str) -> str:
    """Build a safe, actionable invalid-auth-mode error message."""

    summary = summarize_untrusted_auth_mode_value(raw_value)
    return (
        "MOONMIND_GEMINI_CLI_AUTH_MODE must be one of: api_key, oauth "
        f"(received {summary})"
    )


def inspect_gemini_home_for_auth_mode(
    *,
    auth_mode: str,
    gemini_home: str | None,
    isdir: Callable[[str], bool] = os.path.isdir,
    access: Callable[[str, int], bool] = os.access,
) -> tuple[str | None, GeminiHomeValidationIssue | None]:
    """Validate GEMINI_HOME rules for the requested auth mode."""

    normalized_home = str(gemini_home or "").strip() or None
    if not normalized_home:
        if auth_mode == "oauth":
            return None, "missing_for_oauth"
        return None, None

    if not isdir(normalized_home):
        return normalized_home, "not_directory"

    if auth_mode == "oauth" and not access(normalized_home, os.W_OK | os.X_OK):
        return normalized_home, "not_writable_for_oauth"

    return normalized_home, None
