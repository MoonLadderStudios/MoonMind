"""Retrieval evaluation metrics for manifest pipelines.

Implements hitRate@k and ndcg@k as specified in
``docs/RAG/LlamaIndexManifestSystem.md`` §7.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_METRIC_NAME_RE = re.compile(r"^(?P<name>[A-Za-z]+)@(?P<k>[1-9][0-9]*)$")

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
    - ``relevant_ids`` or ``gold``: list of relevant document IDs
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
        if "relevant_ids" not in entry:
            if "gold" in entry:
                entry["relevant_ids"] = entry["gold"]
            else:
                raise ValueError(
                    f"Missing 'relevant_ids' field on line {line_num} of {p}"
                )
        if not isinstance(entry["relevant_ids"], list) or not all(
            isinstance(doc_id, str) and doc_id for doc_id in entry["relevant_ids"]
        ):
            raise ValueError(
                f"Field 'relevant_ids' must be a non-empty string list on line "
                f"{line_num} of {p}"
            )
        entries.append(entry)

    return entries

def _metric_name_and_k(metric_name: str) -> tuple[str, int]:
    """Parse metric names like ``hitRate@10`` and ``ndcg@10``."""
    match = _METRIC_NAME_RE.match(metric_name)
    if match is None:
        return metric_name, 10
    return match.group("name"), int(match.group("k"))

def _baseline_retrieved_ids(entries: List[Dict[str, Any]]) -> List[List[str]] | None:
    """Return committed baseline retrieval IDs when every entry provides them."""
    retrieved: List[List[str]] = []
    for entry in entries:
        raw_ids = entry.get("retrieved_ids")
        if raw_ids is None:
            return None
        if not isinstance(raw_ids, list) or not all(
            isinstance(doc_id, str) and doc_id for doc_id in raw_ids
        ):
            raise ValueError(
                "Field 'retrieved_ids' must be a string list when provided"
            )
        retrieved.append(raw_ids)
    return retrieved

def _score_metric(
    metric_name: str,
    entries: List[Dict[str, Any]],
    retrieved: List[List[str]] | None,
) -> float:
    """Compute supported retrieval metrics for a loaded golden dataset."""
    if retrieved is None:
        return 0.0

    family, k = _metric_name_and_k(metric_name)
    normalized = family.lower()
    if normalized == "hitrate":
        return hit_rate_at_k(entries, retrieved, k=k)
    if normalized == "ndcg":
        return ndcg_at_k(entries, retrieved, k=k)
    logger.warning("Unsupported evaluation metric '%s'; reporting 0.0", metric_name)
    return 0.0

def evaluate_manifest(
    manifest: Any,  # ManifestV0 — Any to avoid circular imports
    dataset_filter: Optional[str] = None,
) -> dict:
    """Run evaluation for a manifest's configured datasets and metrics.

    This is a stub that computes metrics against loaded datasets.
    Full retrieval integration (actually querying Qdrant) requires Phase 4.
    For now, returns the dataset structure and metric definitions.

    Returns:
        Dict representation of :class:`EvaluationResult`.
    """
    eval_config = manifest.evaluation
    if eval_config is None:
        return {"manifest": manifest.metadata.name, "passed": True, "datasets": []}

    result = EvaluationResult(manifest_name=manifest.metadata.name)

    for ds_cfg in eval_config.datasets:
        if dataset_filter and ds_cfg.name != dataset_filter:
            continue

        ds_eval = DatasetEvaluation(dataset_name=ds_cfg.name)

        # Try loading dataset (non-fatal if not found for now)
        try:
            entries = _load_dataset(ds_cfg.path)
            retrieved = _baseline_retrieved_ids(entries)
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

        for metric_cfg in eval_config.metrics:
            score = _score_metric(metric_cfg.name, entries, retrieved)
            ds_eval.metrics.append(
                MetricScore(
                    name=metric_cfg.name,
                    score=score,
                    threshold=metric_cfg.threshold,
                )
            )

        result.datasets.append(ds_eval)

    return result.to_dict()
