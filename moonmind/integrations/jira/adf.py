"""Helpers for Atlassian Document Format payloads."""

from __future__ import annotations

from typing import Any


def text_to_adf_document(text: str) -> dict[str, Any]:
    """Convert plain text into a minimal Atlassian Document Format document."""

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = normalized.split("\n\n")
    content: list[dict[str, Any]] = []

    for raw_paragraph in paragraphs:
        lines = raw_paragraph.split("\n")
        paragraph_content: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if line:
                paragraph_content.append({"type": "text", "text": line})
            if index < len(lines) - 1:
                paragraph_content.append({"type": "hardBreak"})
        content.append({"type": "paragraph", "content": paragraph_content})

    return {"type": "doc", "version": 1, "content": content}


def ensure_adf_document(value: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Return an ADF document for one Jira rich-text field value."""

    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return text_to_adf_document(value)

