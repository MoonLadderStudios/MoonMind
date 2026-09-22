"""Credential-safe text handling for the standalone controller.

Stdlib-only. Mirrors the redaction semantics of the application-owned
updater so extracted diagnostics keep the same operator safety: registry
diagnostics stay readable while likely credential material is redacted.
"""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

_URL_USERINFO_RE = re.compile(r"(://)[^/\s:]+:[^/\s@]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)(\s*[:=]\s*)(\S+)"
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:token|password|passwd|secret)=[^ \t\n\r,;&\"']+)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Redact likely credential material while keeping failure diagnostics."""
    redacted = _URL_USERINFO_RE.sub(r"\1***@", text or "")
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", redacted)
    return _SENSITIVE_VALUE_RE.sub(_REDACTED, redacted)


def log_tail(text: str, *, max_chars: int = 4000) -> str:
    """Return the redacted tail of command output for failure reports."""
    redacted = redact(text or "").strip()
    if len(redacted) <= max_chars:
        return redacted
    return redacted[-max_chars:]
