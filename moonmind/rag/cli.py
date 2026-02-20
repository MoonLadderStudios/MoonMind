"""Command helpers wired into the moonmind CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from moonmind.rag.context_pack import ContextPack
from moonmind.rag.overlay import upsert_overlay_files
from moonmind.rag.overlay_cleanup import clean_overlay_run
from moonmind.rag.service import ContextRetrievalService
from moonmind.rag.settings import RagRuntimeSettings


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


def run_search(
    *,
    query: str,
    filter_args: Sequence[str],
    budget_args: Sequence[str],
    top_k: int | None,
    overlay_policy: str,
    transport: str | None,
    output_file: Path | None,
) -> ContextPack:
    if not query.strip():
        raise CliError("Query text cannot be empty")
    user_filters = parse_filters(filter_args)
    settings = RagRuntimeSettings.from_env(os.environ)
    filters = {**settings.as_filter_metadata(), **user_filters}
    cli_budgets = parse_budget_args(budget_args)
    budgets = _build_budget_config(cli_budgets)
    resolved_transport = settings.resolved_transport(transport)
    service = ContextRetrievalService(settings=settings, env=os.environ)
    pack = service.retrieve(
        query=query,
        filters=filters,
        top_k=top_k or settings.similarity_top_k,
        overlay_policy=overlay_policy,
        budgets=budgets,
        transport=resolved_transport,
    )
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(pack.to_json(), encoding="utf-8")
    return pack


def run_overlay_upsert(
    *,
    paths: Sequence[str],
    run_id: str | None,
) -> int:
    if not paths:
        raise CliError("At least one path must be provided for overlay upsert")
    settings = RagRuntimeSettings.from_env(os.environ)
    resolved_run_id = run_id or settings.run_id
    if not resolved_run_id:
        raise CliError("--run-id is required when MOONMIND_RUN_ID is not set")
    files = [Path(p).resolve() for p in paths]
    for path in files:
        if not path.exists():
            raise CliError(f"Overlay file not found: {path}")
    service = ContextRetrievalService(settings=settings, env=os.environ)
    count = upsert_overlay_files(
        files=files,
        run_id=resolved_run_id,
        settings=settings,
        embedder=service.embedding_client,
        qdrant=service.qdrant_client,
    )
    return count


def run_overlay_clean(*, run_id: str | None) -> None:
    settings = RagRuntimeSettings.from_env(os.environ)
    resolved_run_id = run_id or settings.run_id
    if not resolved_run_id:
        raise CliError("--run-id is required when MOONMIND_RUN_ID is not set")
    service = ContextRetrievalService(settings=settings, env=os.environ)
    clean_overlay_run(
        run_id=resolved_run_id,
        settings=settings,
        qdrant=service.qdrant_client,
    )


def _build_budget_config(cli_budgets: Mapping[str, int] | None = None) -> dict[str, int]:
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


def run_sync_embedding(*, collection: str | None, force: bool) -> tuple[str, int, str]:
    settings = RagRuntimeSettings.from_env(os.environ)
    if not settings.qdrant_enabled:
        raise CliError("Qdrant access is disabled; cannot sync embedding dimensions.")
    target_collection = collection or settings.vector_collection
    service = ContextRetrievalService(settings=settings, env=os.environ)
    try:
        target_dimension = settings.embedding_dimensions or service.embedding_client.embedding_dimension()
    except Exception as exc:  # pragma: no cover - propagates provider errors
        raise CliError(f"Failed to determine embedding dimension: {exc}") from exc
    if not target_dimension or target_dimension <= 0:
        raise CliError("Embedding dimension must be a positive integer.")
    try:
        status = service.qdrant_client.sync_collection_dimensions(
            collection_name=target_collection,
            expected_size=target_dimension,
            force=force,
        )
    except Exception as exc:  # pragma: no cover - qdrant errors are surfaced to user
        raise CliError(str(exc)) from exc
    return target_collection, target_dimension, status
