"""Shared publish-text sanitization helpers for worker and handler paths."""

from __future__ import annotations

import re
from collections.abc import Mapping

from moonmind.utils.logging import redact_sensitive_text

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
_CLOSING_REFERENCE = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)(\s*:?\s+)"
    r"(?=(?:(?:[\w.-]+/[\w.-]+)?#|https://github\.com/[^/\s]+/[^/\s]+/issues/)\d)",
    re.IGNORECASE,
)


def publication_task_instructions(task: Mapping[str, object]) -> str:
    """Read the requested outcome and all authored steps, not a retry summary."""
    parts = []
    instructions = task.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        parts.append(instructions.strip())
    steps = task.get("steps")
    for step in steps if isinstance(steps, list) else ():
        if not isinstance(step, Mapping):
            continue
        instructions = step.get("instructions")
        if (
            isinstance(instructions, str)
            and instructions.strip()
            and instructions.strip() not in parts
        ):
            parts.append(instructions.strip())
    return "\n\n".join(parts)


def append_publication_task(body: str, *, instructions: str, source: str = "") -> str:
    """Keep original scope separate from an implementation's own summary.

    Quoted task text is review context, not GitHub issue-closing authority.
    Large inputs retain their source reference and disclose truncation.
    """

    scope = redact_sensitive_text(instructions).strip()
    if not scope:
        return body
    scope = _CLOSING_REFERENCE.sub(r"\1\2issue ", scope).replace("<!--", "&lt;!--")
    if len(scope) > 20_000:
        scope = (
            scope[:20_000] + "\n[Task excerpt truncated; consult the original task.]"
        )
    origin = f"\nSource: {redact_sensitive_text(source).strip()}\n" if source else ""
    section = f"## Admitted task\n{origin}\n{scope}"
    # GitHub accepts 65,536 characters. Reserve space for downstream disclosure
    # and closing lines while retaining the original outcome over a long summary.
    available = 30_000 - len(section) - 2
    if len(body) > available:
        body = body[: available - 40] + "\n[Implementation summary truncated.]"
    return f"{body.rstrip()}\n\n{section}"


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
