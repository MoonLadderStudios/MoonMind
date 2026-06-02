"""Retrieval evaluation metrics for manifest pipelines.

Implements hitRate@k and ndcg@k as specified in
``docs/RAG/LlamaIndexManifestSystem.md`` §7.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RetrieverFn = Callable[[str, int], List[str]]

@dataclass
class MetricScore:
    """A single evaluation metric result."""

    name: str
    score: float
    threshold: Optional[float] = None

    @property
    def passed(self) -> bool:
        if self.threshold is None:
            return True
        return self.score >= self.threshold

@dataclass
class DatasetEvaluation:
    """Evaluation results for one dataset."""

    dataset_name: str
    metrics: List[MetricScore] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(m.passed for m in self.metrics)

@dataclass
class EvaluationResult:
    """Aggregated evaluation output for a manifest."""

    manifest_name: str
    datasets: List[DatasetEvaluation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(d.passed for d in self.datasets)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest_name,
            "passed": self.passed,
            "datasets": [
                {
                    "name": d.dataset_name,
                    "passed": d.passed,
                    "metrics": [
                        {
                            "name": m.name,
                            "score": round(m.score, 4),
                            "threshold": m.threshold,
                            "passed": m.passed,
                        }
                        for m in d.metrics
                    ],
                }
                for d in self.datasets
            ],
        }

# ---------------------------------------------------------------------------
# Metric implementations
# ---------------------------------------------------------------------------

def hit_rate_at_k(
    queries: List[Dict[str, Any]],
    retrieved: List[List[str]],
    k: int = 10,
) -> float:
    """Compute hit rate @ k.

    For each query, checks if at least one relevant document appears
    in the top-k retrieved results.

    Args:
        queries: List of dicts with ``relevant_ids`` key (list of gold doc IDs).
        retrieved: Parallel list of retrieved doc ID lists (ordered by rank).
        k: Cutoff rank.

    Returns:
        Fraction of queries with at least one hit in top-k.
    """
    if not queries:
        return 0.0

    hits = 0
    for query, docs in zip(queries, retrieved):
        gold = set(query.get("relevant_ids", []))
        top_k = docs[:k]
        if gold & set(top_k):
            hits += 1

    return hits / len(queries)

def ndcg_at_k(
    queries: List[Dict[str, Any]],
    retrieved: List[List[str]],
    k: int = 10,
) -> float:
    """Compute NDCG @ k.

    Uses binary relevance (1 if doc is in gold set, 0 otherwise).

    Args:
        queries: List of dicts with ``relevant_ids`` key.
        retrieved: Parallel list of retrieved doc ID lists (ordered by rank).
        k: Cutoff rank.

    Returns:
        Mean NDCG@k across all queries.
    """
    if not queries:
        return 0.0

    total_ndcg = 0.0
    for query, docs in zip(queries, retrieved):
        gold = set(query.get("relevant_ids", []))
        top_k = docs[:k]

        # DCG with binary relevance
        dcg = 0.0
        for i, doc_id in enumerate(top_k):
            if doc_id in gold:
                dcg += 1.0 / math.log2(i + 2)  # position 1-indexed → log2(rank+1)

        # Ideal DCG: all relevant docs at top positions
        n_relevant = min(len(gold), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(n_relevant))

        if idcg > 0:
            total_ndcg += dcg / idcg

    return total_ndcg / len(queries)

# ---------------------------------------------------------------------------
# Evaluation runner
# ---------------------------------------------------------------------------

def _load_dataset(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL evaluation dataset.

    Each line must be a JSON object with at least:
    - ``query``: the search query string
    - ``relevant_ids``: list of relevant document IDs
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Evaluation dataset not found: {p}")

    entries: List[Dict[str, Any]] = []
    for line_num, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line {line_num} of {p}: {exc}"
            ) from exc
        if "query" not in entry:
            raise ValueError(f"Missing 'query' field on line {line_num} of {p}")
        entries.append(entry)

    return entries

def _metric_cutoff(metric_name: str, default: int) -> int:
    match = re.search(r"@(\d+)$", metric_name)
    if not match:
        return default
    return max(1, int(match.group(1)))

def _doc_id_from_item(item: Any) -> str:
    payload = getattr(item, "payload", None)
    if isinstance(payload, dict):
        for key in ("id", "doc_id", "document_id", "source", "path", "file_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for attr in ("source", "chunk_hash"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(item)

def _default_top_k(manifest: Any) -> int:
    try:
        retriever = manifest.retrievers[0]
    except (AttributeError, IndexError):
        return 10
    params = getattr(retriever, "params", None)
    top_k = getattr(params, "topK", None)
    return int(top_k or 10)

def _settings_overrides_from_manifest(manifest: Any) -> Dict[str, str]:
    overrides: Dict[str, str] = {}

    vector_store = getattr(manifest, "vectorStore", None)
    index_name = getattr(vector_store, "indexName", None)
    if isinstance(index_name, str) and index_name.strip():
        overrides["VECTOR_STORE_COLLECTION_NAME"] = index_name.strip()
        overrides["VECTOR_STORE_COLLECTION_NAMES"] = index_name.strip()

    embeddings = getattr(manifest, "embeddings", None)
    provider = getattr(embeddings, "provider", None)
    if isinstance(provider, str) and provider.strip():
        normalized_provider = provider.strip().lower()
        overrides["DEFAULT_EMBEDDING_PROVIDER"] = normalized_provider
        model = getattr(embeddings, "model", None)
        if isinstance(model, str) and model.strip():
            if normalized_provider == "google":
                overrides["GOOGLE_EMBEDDING_MODEL"] = model.strip()
            elif normalized_provider == "openai":
                overrides["OPENAI_EMBEDDING_MODEL"] = model.strip()

    return overrides

def _build_service_retriever(manifest: Any) -> RetrieverFn:
    from moonmind.rag.service import ContextRetrievalService
    from moonmind.rag.settings import RagRuntimeSettings

    settings_source = dict(os.environ)
    settings_source.update(_settings_overrides_from_manifest(manifest))
    settings = RagRuntimeSettings.from_env(settings_source)
    executable, reason = settings.retrieval_execution_reason(None)
    if not executable:
        raise RuntimeError(
            "Retrieval evaluation requires executable RAG settings "
            f"(reason: {reason})."
        )
    service = ContextRetrievalService(settings=settings)
    filters = settings.as_filter_metadata()

    def retrieve(query: str, top_k: int) -> List[str]:
        pack = service.retrieve(
            query=query,
            filters=filters,
            top_k=top_k,
            overlay_policy="skip",
            budgets={},
            transport=settings.resolved_transport(None),
            initiation_mode="evaluation",
        )
        return [_doc_id_from_item(item) for item in pack.items]

    return retrieve

def evaluate_manifest(
    manifest: Any,  # ManifestV0 — Any to avoid circular imports
    dataset_filter: Optional[str] = None,
    retriever: RetrieverFn | None = None,
) -> dict:
    """Run evaluation for a manifest's configured datasets and metrics.

    The retriever returns ordered document ids for one query. Tests and local
    smoke checks can inject a deterministic retriever; CLI callers use the
    configured RAG retrieval service.

    Returns:
        Dict representation of :class:`EvaluationResult`.
    """
    eval_config = manifest.evaluation
    if eval_config is None:
        return {"manifest": manifest.metadata.name, "passed": True, "datasets": []}

    result = EvaluationResult(manifest_name=manifest.metadata.name)
    active_retriever = retriever or _build_service_retriever(manifest)
    default_top_k = _default_top_k(manifest)

    for ds_cfg in eval_config.datasets:
        if dataset_filter and ds_cfg.name != dataset_filter:
            continue

        ds_eval = DatasetEvaluation(dataset_name=ds_cfg.name)

        # Try loading dataset (non-fatal if not found for now)
        try:
            entries = _load_dataset(ds_cfg.path)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Could not load dataset '%s': %s", ds_cfg.name, exc)
            for metric_cfg in eval_config.metrics:
                ds_eval.metrics.append(
                    MetricScore(
                        name=metric_cfg.name,
                        score=0.0,
                        threshold=metric_cfg.threshold,
                    )
                )
            result.datasets.append(ds_eval)
            continue

        metric_cutoffs = [
            _metric_cutoff(metric_cfg.name, default_top_k)
            for metric_cfg in eval_config.metrics
        ]
        max_top_k = max(metric_cutoffs, default=default_top_k)
        retrieved = [
            active_retriever(str(entry["query"]), max_top_k)
            for entry in entries
        ]
        for metric_cfg in eval_config.metrics:
            cutoff = _metric_cutoff(metric_cfg.name, default_top_k)
            metric_name = metric_cfg.name.lower()
            if metric_name.startswith("hitrate"):
                score = hit_rate_at_k(entries, retrieved, k=cutoff)
            elif metric_name.startswith("ndcg"):
                score = ndcg_at_k(entries, retrieved, k=cutoff)
            else:
                logger.warning("Unsupported evaluation metric '%s'", metric_cfg.name)
                score = 0.0
            ds_eval.metrics.append(
                MetricScore(
                    name=metric_cfg.name,
                    score=score,
                    threshold=metric_cfg.threshold,
                )
            )

        result.datasets.append(ds_eval)

    return result.to_dict()
