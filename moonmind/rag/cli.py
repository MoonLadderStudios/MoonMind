"""Parsing helpers for CLI input (vector-free).

MoonLadderStudios/MoonMind#4112 retired all vector-only CLI subcommands
(search, overlay-upsert, overlay-clean, collection inspection and embedding
dimension sync). This module now retains only generic key=value parsing
helpers shared by remaining CLI surfaces; it performs no vector network
access and imports no vector backend.
"""

from __future__ import annotations

import json
from typing import Mapping, Sequence

class CliError(RuntimeError):
    """Raised for CLI usage errors."""

def parse_filters(filter_args: Sequence[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for arg in filter_args:
        if "=" not in arg:
            raise CliError(f"Invalid filter '{arg}'. Expected key=value format.")
        key, value = arg.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise CliError(f"Invalid filter '{arg}'. Both key and value required.")
        filters[key] = value
    return filters

def parse_budget_args(budget_args: Sequence[str]) -> dict[str, int]:
    budgets: dict[str, int] = {}
    for arg in budget_args:
        if "=" not in arg:
            raise CliError(f"Invalid budget '{arg}'. Expected key=value format.")
        key, value = arg.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise CliError(f"Invalid budget '{arg}'. Both key and value required.")
        try:
            budgets[key] = int(value)
        except ValueError as exc:
            raise CliError(f"Budget '{arg}' must use an integer value") from exc
    return budgets

def format_json(data: Mapping[str, object]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)
