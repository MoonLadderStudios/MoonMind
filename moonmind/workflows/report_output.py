"""Helpers for the compact report-output contract."""

from __future__ import annotations


def normalize_report_output_primary_path(raw_path: object) -> str:
    """Return a report path with a Markdown suffix when no suffix was provided."""

    path = str(raw_path or "").strip()
    if not path:
        return ""
    candidate = path.rstrip("/\\")
    if not candidate:
        return ""
    basename = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in basename.strip("."):
        return candidate
    return f"{candidate}md" if candidate.endswith(".") else f"{candidate}.md"


def report_output_display_name(
    raw_path: object, *, default: str = "final-report.md"
) -> str:
    path = normalize_report_output_primary_path(raw_path)
    if not path:
        return default
    name = path.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or default
