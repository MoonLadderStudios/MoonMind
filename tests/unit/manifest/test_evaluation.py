"""Unit tests for evaluation metrics (hitRate@k, ndcg@k)."""

from __future__ import annotations

import json
import math

import pytest

from moonmind.manifest.evaluation import (
    DatasetEvaluation,
    EvaluationResult,
    MetricScore,
    hit_rate_at_k,
    ndcg_at_k,
    _load_dataset,
)

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
