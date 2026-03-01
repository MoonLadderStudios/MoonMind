"""Shared publish-text sanitization helpers for worker and handler paths."""

from __future__ import annotations

import re

_FULL_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_MOONMIND_WORD_PATTERN = re.compile(r"\bmoonmind\b", re.IGNORECASE)
_SECRET_LIKE_METADATA_PATTERN = re.compile(
    r"""(?ix)
    (?:
        gh[pousr]_[A-Za-z0-9]{8,}
        | github_pat_[A-Za-z0-9_]{10,}
        | AIza[0-9A-Za-z_-]{10,}
        | ATATT[A-Za-z0-9_-]{6,}
        | AKIA[0-9A-Z]{8,}
        | -----BEGIN [A-Z ]+PRIVATE KEY-----
        | (?:token|password|secret)\s*[:=]
    )
    """
)


def sanitize_publish_subject(
    value: str,
    *,
    max_chars: int,
    redact_uuids: bool = True,
) -> str:
    """Normalize auto-generated publish subjects and scrub secret-like content."""

    sanitized = _MOONMIND_WORD_PATTERN.sub("", value)
    if redact_uuids:
        sanitized = _FULL_UUID_PATTERN.sub("job", sanitized)
    sanitized = " ".join(sanitized.split())
    if _SECRET_LIKE_METADATA_PATTERN.search(sanitized):
        sanitized = "[REDACTED]"
    if not sanitized:
        sanitized = "Automated update"
    if len(sanitized) <= max_chars:
        return sanitized
    return f"{sanitized[: max_chars - 3].rstrip()}..."


def sanitize_metadata_footer_value(
    value: str | None, *, fallback: str = "unknown"
) -> str:
    """Normalize metadata footer values and replace secret-like tokens."""

    normalized = " ".join(str(value or "").split())
    if not normalized:
        return fallback
    if _SECRET_LIKE_METADATA_PATTERN.search(normalized):
        return "[REDACTED]"
    return normalized
