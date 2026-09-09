"""Retrieval evaluation over recorded results (vector-free).

MoonLadderStudios/MoonMind#4108 retired native live-vector retrieval
evaluation wiring. Evaluation runs only over committed ``retrieved_ids``
baselines (provenance ``recorded``) or an explicitly injected retriever
(provenance ``injected``) for tests. Absent execution never passes a quality
gate: missing/unloadable datasets and requests without recorded or injected
results fail closed.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RetrieverFn = Callable[[str, int], List[str]]
_METRIC_NAME_RE = re.compile(r"^(?P<name>[A-Za-z0-9_\-]+)@(?P<k>[1-9][0-9]*)$")

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
    provenance: str = "recorded"

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
                    "provenance": d.provenance,
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
        if "retrieved_ids" in entry:
            raw_ids = entry["retrieved_ids"]
            if not isinstance(raw_ids, list) or not all(
                isinstance(doc_id, str) and doc_id for doc_id in raw_ids
            ):
                raise ValueError(
                    f"Field 'retrieved_ids' must be a non-empty string list on line "
                    f"{line_num} of {p}"
                )
        entries.append(entry)

    return entries

def _metric_name_and_k(metric_name: str, default: int = 10) -> tuple[str, int]:
    """Parse metric names like ``hitRate@10`` and ``ndcg@10``."""
    match = _METRIC_NAME_RE.match(metric_name)
    if match is None:
        return metric_name, default
    return match.group("name"), int(match.group("k"))

def _metric_cutoff(metric_name: str, default: int) -> int:
    _, cutoff = _metric_name_and_k(metric_name, default)
    return cutoff

def _metric_family(metric_name: str) -> str:
    family, _ = _metric_name_and_k(metric_name)
    return family.lower().replace("_", "").replace("-", "")

def _baseline_retrieved_ids(entries: List[Dict[str, Any]]) -> List[List[str]] | None:
    """Return committed baseline retrieval IDs when every entry provides them."""
    has_any = any("retrieved_ids" in entry for entry in entries)
    if not has_any:
        return None

    retrieved: List[List[str]] = []
    for entry in entries:
        raw_ids = entry.get("retrieved_ids")
        if raw_ids is None:
            raise ValueError(
                "Inconsistent dataset: 'retrieved_ids' is provided in some entries "
                "but missing in others."
            )
        retrieved.append(raw_ids)
    return retrieved

def _score_metric(
    metric_name: str,
    entries: List[Dict[str, Any]],
    retrieved: List[List[str]] | None,
    *,
    default_top_k: int = 10,
) -> float:
    """Compute supported retrieval metrics for a loaded golden dataset."""
    if retrieved is None:
        return 0.0

    family = _metric_family(metric_name)
    cutoff = _metric_cutoff(metric_name, default_top_k)
    if family == "hitrate":
        return hit_rate_at_k(entries, retrieved, k=cutoff)
    if family == "ndcg":
        return ndcg_at_k(entries, retrieved, k=cutoff)
    logger.warning("Unsupported evaluation metric '%s'; reporting 0.0", metric_name)
    return 0.0

def evaluate_manifest(
    manifest: Any,  # ManifestV0 — Any to avoid circular imports
    dataset_filter: Optional[str] = None,
    retriever: RetrieverFn | None = None,
) -> dict:
    """Run evaluation over recorded/injected results only (no live retrieval).

    Provenance is explicit: ``recorded`` for committed ``retrieved_ids``
    baselines, ``injected`` for a caller-supplied retriever (tests), and
    ``unavailable`` for missing/unloadable datasets (always fails). Requests
    without recorded or injected results raise actionably instead of scoring
    a measured zero that could pass a gate.
    """
    eval_config = manifest.evaluation
    if eval_config is None:
        return {"manifest": manifest.metadata.name, "passed": True, "datasets": []}

    result = EvaluationResult(manifest_name=manifest.metadata.name)
    default_top_k = 10

    for ds_cfg in eval_config.datasets:
        if dataset_filter and ds_cfg.name != dataset_filter:
            continue

        # Try loading dataset; missing/unloadable never passes a gate.
        try:
            entries = _load_dataset(ds_cfg.path)
            retrieved = _baseline_retrieved_ids(entries)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Could not load dataset '%s': %s", ds_cfg.name, exc)
            ds_eval = DatasetEvaluation(
                dataset_name=ds_cfg.name, provenance="unavailable"
            )
            for metric_cfg in eval_config.metrics:
                # Fail closed: an absent execution is not a measured zero
                # that passes when no threshold is set.
                threshold = metric_cfg.threshold
                if threshold is None:
                    threshold = 1.0
                ds_eval.metrics.append(
                    MetricScore(
                        name=metric_cfg.name,
                        score=0.0,
                        threshold=threshold,
                    )
                )
            result.datasets.append(ds_eval)
            continue

        metric_cutoffs = [
            _metric_cutoff(metric_cfg.name, default_top_k)
            for metric_cfg in eval_config.metrics
        ]
        max_top_k = max(metric_cutoffs, default=default_top_k)
        if retrieved is None:
            if retriever is None:
                raise RuntimeError(
                    f"Dataset '{ds_cfg.name}' has no committed 'retrieved_ids' "
                    "and no injected retriever was provided "
                    "(MoonLadderStudios/MoonMind#4108): live-vector retrieval "
                    "evaluation is retired. Commit 'retrieved_ids' baselines "
                    "or inject an explicit retriever; absent execution never "
                    "passes a quality gate."
                )
            retrieved = [
                retriever(str(entry["query"]), max_top_k) for entry in entries
            ]
            provenance = "injected"
        else:
            provenance = "recorded"
        ds_eval = DatasetEvaluation(
            dataset_name=ds_cfg.name, provenance=provenance
        )
        for metric_cfg in eval_config.metrics:
            score = _score_metric(
                metric_cfg.name,
                entries,
                retrieved,
                default_top_k=default_top_k,
            )
            ds_eval.metrics.append(
                MetricScore(
                    name=metric_cfg.name,
                    score=score,
                    threshold=metric_cfg.threshold,
                )
            )

        result.datasets.append(ds_eval)

    return result.to_dict()
