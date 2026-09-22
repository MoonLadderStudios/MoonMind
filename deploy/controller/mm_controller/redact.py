"""Credential redaction for controller logs and state summaries.

Stdlib only. Carried forward from the extraction source
``moonmind.workflows.skills.deployment_execution`` without importing it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"("
    r"token|secret|password|passwd|credential|authorization|"
    r"auth[_-]?header|api[_-]?key|registry[_-]?password|cookie|session"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:token|password|passwd|secret)=[^ \t\n\r,;&\"']+)",
    re.IGNORECASE,
)
_URL_USERINFO_PATTERN = re.compile(r"(://)[^/\s:]+:[^/\s@]+@")


def redact_text(text: str) -> str:
    """Redact credential-like material from free-form log text."""
    redacted = _URL_USERINFO_PATTERN.sub(r"\1***@", text or "")
    return _SENSITIVE_VALUE_PATTERN.sub(REDACTED, redacted)


def redact_value(value: Any, key: str | None = None) -> Any:
    """Recursively redact mappings/lists, redacting whole values for sensitive keys."""
    if key and _SENSITIVE_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): redact_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, key) for item in value)
    if isinstance(value, str):
        return _SENSITIVE_VALUE_PATTERN.sub(REDACTED, value)
    return value


def bound_tail(text: str, limit: int = 4000) -> str:
    """Keep both ends of a failure log so the cause line survives bounding."""
    if len(text) <= limit:
        return text
    elision = "\n...[elided]...\n"
    keep = limit - len(elision)
    head = keep // 2
    return text[:head] + elision + text[-(keep - head):]
