"""Completion summary classification helpers."""

from __future__ import annotations

from typing import Any


def is_generic_completion_summary(value: Any) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split()).rstrip(".")
    return normalized in {
        "",
        "completed",
        "completed with status completed",
        "workflow completed successfully",
        "codex managed-session turn completed",
        "agent run completed without a textual report body",
    } or normalized.startswith("process exited with code")
