"""Non-vector CLI parsing helpers.

MoonLadderStudios/MoonMind#4112: the native vector backend is retired. All
vector-only entry points (retrieval search, overlay upsert/clean, collection
inspection and embedding-dimension sync) were deleted with their imports and
registrations. This module retains only the dependency-free ``key=value``
parsing helpers, which carry no vector semantics and have no Qdrant import.
"""

from __future__ import annotations

import json
import os
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


def _build_budget_config(
    cli_budgets: Mapping[str, int] | None = None,
) -> dict[str, int]:
    budget: dict[str, int] = dict(cli_budgets or {})
    tokens_raw = os.getenv("RAG_QUERY_TOKEN_BUDGET")
    latency_raw = os.getenv("RAG_LATENCY_BUDGET_MS")
    if "tokens" not in budget and tokens_raw:
        try:
            budget["tokens"] = int(tokens_raw)
        except ValueError as exc:  # pragma: no cover - configuration error
            raise CliError("RAG_QUERY_TOKEN_BUDGET must be an integer") from exc
    if "latency_ms" not in budget and latency_raw:
        try:
            budget["latency_ms"] = int(latency_raw)
        except ValueError as exc:  # pragma: no cover - configuration error
            raise CliError("RAG_LATENCY_BUDGET_MS must be an integer") from exc
    return budget


def format_json(data: Mapping[str, object]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


__all__ = [
    "CliError",
    "parse_filters",
    "parse_budget_args",
    "format_json",
]
