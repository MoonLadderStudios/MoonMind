"""Unit tests for evaluation metrics (hitRate@k, ndcg@k)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from moonmind.manifest.manifest_cli import run_evaluate
from moonmind.manifest.evaluation import (
    DatasetEvaluation,
    EvaluationResult,
    MetricScore,
    _baseline_retrieved_ids,
    _build_service_retriever,
    _score_metric,
    evaluate_manifest,
    hit_rate_at_k,
    ndcg_at_k,
    _load_dataset,
)
from moonmind.schemas.manifest_v0_models import ManifestV0

# ---------------------------------------------------------------------------
# hitRate@k
# ---------------------------------------------------------------------------

class TestHitRateAtK:
    def test_perfect_hit_rate(self):
        queries = [{"relevant_ids": ["d1"]}, {"relevant_ids": ["d2"]}]
        retrieved = [["d1", "d3"], ["d2", "d4"]]
        assert hit_rate_at_k(queries, retrieved, k=2) == 1.0

    def test_zero_hit_rate(self):
        queries = [{"relevant_ids": ["d1"]}, {"relevant_ids": ["d2"]}]
        retrieved = [["d3", "d4"], ["d5", "d6"]]
        assert hit_rate_at_k(queries, retrieved, k=2) == 0.0

    def test_partial_hit_rate(self):
        queries = [{"relevant_ids": ["d1"]}, {"relevant_ids": ["d2"]}]
        retrieved = [["d1", "d3"], ["d5", "d6"]]
        assert hit_rate_at_k(queries, retrieved, k=2) == 0.5

    def test_empty_queries(self):
        assert hit_rate_at_k([], [], k=5) == 0.0

    def test_k_cutoff(self):
        queries = [{"relevant_ids": ["d3"]}]
        retrieved = [["d1", "d2", "d3"]]
        assert hit_rate_at_k(queries, retrieved, k=2) == 0.0
        assert hit_rate_at_k(queries, retrieved, k=3) == 1.0

# ---------------------------------------------------------------------------
# ndcg@k
# ---------------------------------------------------------------------------

class TestNdcgAtK:
    def test_perfect_ndcg(self):
        queries = [{"relevant_ids": ["d1", "d2"]}]
        retrieved = [["d1", "d2", "d3"]]
        assert ndcg_at_k(queries, retrieved, k=3) == pytest.approx(1.0)

    def test_zero_ndcg(self):
        queries = [{"relevant_ids": ["d1"]}]
        retrieved = [["d2", "d3", "d4"]]
        assert ndcg_at_k(queries, retrieved, k=3) == 0.0

    def test_empty_queries(self):
        assert ndcg_at_k([], [], k=5) == 0.0

    def test_partial_ndcg(self):
        # Relevant at position 2 instead of position 1
        queries = [{"relevant_ids": ["d1"]}]
        retrieved = [["d2", "d1", "d3"]]
        # DCG = 1/log2(3) = 0.6309...
        # IDCG = 1/log2(2) = 1.0
        expected = (1.0 / math.log2(3)) / (1.0 / math.log2(2))
        assert ndcg_at_k(queries, retrieved, k=3) == pytest.approx(expected)

# ---------------------------------------------------------------------------
# MetricScore
# ---------------------------------------------------------------------------

class TestMetricScore:
    def test_passed_when_above_threshold(self):
        m = MetricScore(name="hitRate@10", score=0.8, threshold=0.7)
        assert m.passed is True

    def test_failed_when_below_threshold(self):
        m = MetricScore(name="hitRate@10", score=0.5, threshold=0.7)
        assert m.passed is False

    def test_passed_when_no_threshold(self):
        m = MetricScore(name="hitRate@10", score=0.3)
        assert m.passed is True

# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------

class TestEvaluationResult:
    def test_to_dict(self):
        result = EvaluationResult(
            manifest_name="test",
            datasets=[
                DatasetEvaluation(
                    dataset_name="smoke",
                    metrics=[MetricScore(name="hitRate@10", score=0.8)],
                )
            ],
        )
        d = result.to_dict()
        assert d["manifest"] == "test"
        assert d["passed"] is True
        assert len(d["datasets"]) == 1

    def test_evaluate_manifest_uses_retriever_for_scores(self, tmp_path):
        dataset = tmp_path / "golden.jsonl"
        dataset.write_text(
            json.dumps({"query": "where is rag?", "relevant_ids": ["docs/rag.md"]})
            + "\n"
            + json.dumps({"query": "where is jira?", "relevant_ids": ["docs/jira.md"]})
            + "\n"
        )
        manifest = ManifestV0.from_yaml_string(
            f"""
version: "v0"
metadata:
  name: "eval-test"
embeddings:
  provider: "openai"
  model: "text-embedding-3-large"
vectorStore:
  type: "qdrant"
  indexName: "test"
dataSources:
  - id: "local"
    type: "SimpleDirectoryReader"
indices:
  - id: "idx"
    sources: ["local"]
retrievers:
  - id: "ret"
    type: "Vector"
    indices: ["idx"]
    params:
      topK: 2
evaluation:
  datasets:
    - name: "smoke"
      path: "{dataset}"
  metrics:
    - name: "hitRate@1"
      threshold: 1.0
    - name: "ndcg@2"
      threshold: 1.0
"""
        )

        def retriever(query: str, top_k: int) -> list[str]:
            assert top_k == 2
            if "rag" in query:
                return ["docs/rag.md", "docs/other.md"]
            return ["docs/other.md", "docs/jira.md"]

        result = evaluate_manifest(manifest, retriever=retriever)

        metrics = result["datasets"][0]["metrics"]
        assert metrics[0]["name"] == "hitRate@1"
        assert metrics[0]["score"] == 0.5
        assert metrics[0]["passed"] is False
        assert metrics[1]["name"] == "ndcg@2"
        assert metrics[1]["score"] == 0.8155
        assert metrics[1]["passed"] is False

    def test_service_retriever_uses_manifest_vector_store(self, monkeypatch):
        captured = {}

        class FakePack:
            items = [type("Item", (), {"payload": {"doc_id": "docs/rag.md"}})()]

        class FakeService:
            def __init__(self, *, settings):
                captured["settings"] = settings

            def retrieve(self, **kwargs):
                captured["retrieve_kwargs"] = kwargs
                return FakePack()

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("VECTOR_STORE_COLLECTION_NAME", "env-collection")
        monkeypatch.setattr(
            "moonmind.rag.service.ContextRetrievalService", FakeService
        )
        manifest = ManifestV0.from_yaml_string(
            """
version: "v0"
metadata:
  name: "eval-test"
embeddings:
  provider: "openai"
  model: "text-embedding-3-large"
vectorStore:
  type: "qdrant"
  indexName: "manifest-collection"
dataSources:
  - id: "local"
    type: "SimpleDirectoryReader"
indices:
  - id: "idx"
    sources: ["local"]
retrievers:
  - id: "ret"
    type: "Vector"
    indices: ["idx"]
"""
        )

        retriever = _build_service_retriever(manifest)

        assert retriever("where is rag?", 1) == ["docs/rag.md"]
        assert captured["settings"].vector_collection == "manifest-collection"
        assert captured["settings"].vector_collections == ("manifest-collection",)
        assert captured["settings"].embedding_provider == "openai"
        assert captured["settings"].embedding_model == "text-embedding-3-large"

# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

class TestLoadDataset:
    def test_valid_jsonl(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text(
            json.dumps({"query": "what is X?", "relevant_ids": ["d1"]}) + "\n"
            + json.dumps({"query": "what is Y?", "relevant_ids": ["d2"]}) + "\n"
        )
        entries = _load_dataset(str(f))
        assert len(entries) == 2
        assert entries[0]["relevant_ids"] == ["d1"]

    def test_gold_alias_normalizes_to_relevant_ids(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text(json.dumps({"query": "what is X?", "gold": ["d1"]}) + "\n")
        entries = _load_dataset(str(f))
        assert entries == [
            {"query": "what is X?", "gold": ["d1"], "relevant_ids": ["d1"]}
        ]

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            _load_dataset("/nonexistent.jsonl")

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text("{invalid json\n")
        with pytest.raises(ValueError, match="Invalid JSON"):
            _load_dataset(str(f))

    def test_missing_query_field(self, tmp_path):
        f = tmp_path / "no_query.jsonl"
        f.write_text(json.dumps({"relevant_ids": ["d1"]}) + "\n")
        with pytest.raises(ValueError, match="Missing 'query'"):
            _load_dataset(str(f))

    def test_missing_relevance_field(self, tmp_path):
        f = tmp_path / "no_relevance.jsonl"
        f.write_text(json.dumps({"query": "what is X?"}) + "\n")
        with pytest.raises(ValueError, match="Missing 'relevant_ids'"):
            _load_dataset(str(f))

    def test_invalid_retrieved_ids_field(self, tmp_path):
        f = tmp_path / "bad_retrieved.jsonl"
        f.write_text(
            json.dumps(
                {
                    "query": "what is X?",
                    "relevant_ids": ["d1"],
                    "retrieved_ids": "not-a-list",
                }
            )
            + "\n"
        )
        with pytest.raises(
            ValueError,
            match="Field 'retrieved_ids' must be a non-empty string list",
        ):
            _load_dataset(str(f))

    def test_invalid_retrieved_ids_item_reports_line_number(self, tmp_path):
        f = tmp_path / "bad_retrieved_item.jsonl"
        f.write_text(
            json.dumps({"query": "what is X?", "relevant_ids": ["d1"]}) + "\n"
            + json.dumps(
                {
                    "query": "what is Y?",
                    "relevant_ids": ["d2"],
                    "retrieved_ids": ["d2", ""],
                }
            )
            + "\n"
        )
        with pytest.raises(
            ValueError,
            match=r"Field 'retrieved_ids' must be a non-empty string list on line 2",
        ):
            _load_dataset(str(f))

# ---------------------------------------------------------------------------
# Committed baseline
# ---------------------------------------------------------------------------

class TestCommittedBaseline:
    def test_baseline_retrieved_ids_returns_none_when_absent(self):
        assert _baseline_retrieved_ids([{"relevant_ids": ["d1"]}]) is None

    def test_baseline_retrieved_ids_rejects_inconsistent_entries(self):
        with pytest.raises(ValueError, match="Inconsistent dataset"):
            _baseline_retrieved_ids(
                [
                    {"relevant_ids": ["d1"], "retrieved_ids": ["d1"]},
                    {"relevant_ids": ["d2"]},
                ]
            )

    def test_metric_name_accepts_separator_variants(self):
        entries = [{"relevant_ids": ["d1"]}]
        retrieved = [["d1"]]
        assert _score_metric("hit_rate@10", entries, retrieved) == 1.0
        assert _score_metric("hit-rate@10", entries, retrieved) == 1.0
        assert _score_metric("hitRate@10", entries, retrieved) == 1.0

    def test_mm756_smoke_baseline_passes_manifest_thresholds(self):
        manifest_path = Path("examples/readers-full-example.yaml")
        result = run_evaluate(manifest_path=str(manifest_path), dataset="smoke")
        assert result["passed"] is True
        assert result["datasets"] == [
            {
                "name": "smoke",
                "passed": True,
                "metrics": [
                    {
                        "name": "hitRate@10",
                        "score": 1.0,
                        "threshold": 0.8,
                        "passed": True,
                    },
                    {
                        "name": "ndcg@10",
                        "score": 0.877,
                        "threshold": 0.7,
                        "passed": True,
                    },
                ],
            }
        ]
