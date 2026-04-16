"""Helpers for reading selected agent-skill metadata from runtime payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def selected_agent_skill(parameters: Mapping[str, Any] | None) -> str:
    """Return the selected agent skill from supported request metadata shapes."""

    if not isinstance(parameters, Mapping):
        return ""

    metadata = parameters.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    moonmind = metadata_map.get("moonmind")
    moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}

    selected = (
        moonmind_map.get("selectedSkill")
        or metadata_map.get("selectedSkill")
        or parameters.get("selectedSkill")
        or ""
    )
    return str(selected).strip().lower()
