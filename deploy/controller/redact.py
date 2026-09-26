"""Credential redaction for controller diagnostics and records.

Keeps registry/host diagnostics readable while hiding credential material.
Stdlib-only; mirrors the redaction semantics of the legacy in-app updater.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_URL_USERINFO_RE = re.compile(r"(://)[^/\s:]+:[^/\s@]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)(\s*[:=]\s*)(\S+)"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|credential|authorization|"
    r"auth[_-]?header|api[_-]?key|registry[_-]?password)",
    re.IGNORECASE,
)


def redact_text(text: str | None) -> str:
    """Redact likely credential material from free-form diagnostics."""
    redacted = _URL_USERINFO_RE.sub(r"\1***@", text or "")
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", redacted)


def redact_mapping(value: Any, key: str | None = None) -> Any:
    """Recursively redact sensitive keys; never mutates the input."""
    if isinstance(value, dict):
        return {
            str(k): (
                REDACTED
                if _SENSITIVE_KEY_RE.search(str(k))
                else redact_mapping(v, str(k))
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item, key) for item in value)
    if isinstance(value, str) and key is not None and _SENSITIVE_KEY_RE.search(key):
        return REDACTED
    return value


def tail_text(payload: str | bytes, *, max_chars: int = 4000) -> str:
    """Keep the tail of long output so records stay small and readable."""
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else (payload or "")
    if max_chars <= 0:
        return ""
    return text[-max_chars:]
