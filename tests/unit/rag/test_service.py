"""Unit tests for ContextRetrievalService budget enforcement (DOC-REQ-005)."""

from __future__ import annotations

import pytest

from moonmind.rag.service import ContextRetrievalService, RetrievalBudgetExceededError
from moonmind.rag.settings import RagRuntimeSettings

def _settings(**overrides: object) -> RagRuntimeSettings:
    defaults = dict(
        qdrant_url=None,
        qdrant_host="localhost",
        qdrant_port=6333,
        qdrant_api_key=None,
        vector_collection="test_collection",
        embedding_provider="google",
        embedding_model="test-model",
        embedding_dimensions=768,
        similarity_top_k=5,
        max_context_chars=8000,
        overlay_mode="collection",
        overlay_ttl_hours=24,
        overlay_chunk_chars=1200,
        overlay_chunk_overlap=120,
        retrieval_gateway_url=None,
        statsd_host=None,
        statsd_port=None,
        job_id=None,
        run_id=None,
        rag_enabled=True,
        qdrant_enabled=True,
    )
    defaults.update(overrides)
    return RagRuntimeSettings(**defaults)

def test_normalize_budgets_extracts_valid_integers() -> None:
    result = ContextRetrievalService._normalize_budgets(
        {"tokens": 500, "latency_ms": "800", "extra": "ignored"}
    )
    assert result == {"tokens": 500, "latency_ms": 800}

def test_normalize_budgets_skips_invalid_values() -> None:
    result = ContextRetrievalService._normalize_budgets(
        {"tokens": "abc", "latency_ms": -5}
    )
    assert result == {}

def test_normalize_budgets_handles_empty() -> None:
    assert ContextRetrievalService._normalize_budgets({}) == {}

def test_enforce_token_budget_raises_when_exceeded() -> None:
    settings = _settings(overlay_chunk_chars=1200)
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    with pytest.raises(RetrievalBudgetExceededError, match="Token budget exceeded"):
        service._enforce_token_budget(
            query="test query",
            top_k=100,
            budgets={"tokens": 10},
        )

def test_enforce_token_budget_passes_when_within_limits() -> None:
    settings = _settings(overlay_chunk_chars=100)
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    # Should not raise
    service._enforce_token_budget(
        query="test query",
        top_k=1,
        budgets={"tokens": 10000},
    )

def test_enforce_token_budget_noop_without_budget() -> None:
    settings = _settings()
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    # No tokens budget key → no enforcement
    service._enforce_token_budget(
        query="very long query " * 1000,
        top_k=100,
        budgets={},
    )

def test_retrieval_budget_error_has_budget_type() -> None:
    error = RetrievalBudgetExceededError("Too slow", budget_type="latency_ms")
    assert error.budget_type == "latency_ms"
    assert "Too slow" in str(error)

def test_resolved_transport_prefers_explicit() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    assert settings.resolved_transport("direct") == "direct"
    assert settings.resolved_transport("gateway") == "gateway"

def test_resolved_transport_defaults_to_gateway_when_url_set() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    assert settings.resolved_transport(None) == "gateway"

def test_resolved_transport_defaults_to_direct_when_no_url() -> None:
    settings = _settings(retrieval_gateway_url=None)
    assert settings.resolved_transport(None) == "direct"
