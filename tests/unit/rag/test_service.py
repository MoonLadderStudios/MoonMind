"""Unit tests for ContextRetrievalService budget enforcement (DOC-REQ-005)."""

from __future__ import annotations

import pytest

from moonmind.rag.context_pack import ContextItem
from moonmind.rag.qdrant_client import SearchResult
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
            budgets={"tokens": 5000},
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


class _StubEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str):
        self.calls.append(text)
        return [0.1, 0.2]


class _StubQdrant:
    def __init__(self) -> None:
        self.ensured = False
        self.calls: list[dict[str, object]] = []

    def ensure_collection_ready(self) -> None:
        self.ensured = True

    def search(self, **kwargs):
        self.calls.append(kwargs)
        item = ContextItem(score=0.8, source="src/file.py", text="snippet")
        return SearchResult(items=[item], latency_ms=5.0)


def test_retrieve_direct_flow_uses_embedding_and_qdrant_search() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    settings = _settings(run_id="run-xyz")
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
    )

    pack = service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        initiation_mode="session",
    )

    assert embedder.calls == ["How to integrate RAG?"]
    assert qdrant.ensured is True
    assert len(qdrant.calls) == 1
    assert pack.transport == "direct"
    assert pack.initiation_mode == "session"
    assert pack.truncated is False
    assert pack.items
    assert "Retrieved Context" in pack.context_text


def test_retrieve_gateway_flow_skips_embedding_and_preserves_contract_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")

    class _UnexpectedEmbeddingClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("embedding client should not initialize in gateway mode")

    class _GatewayResponse:
        def json(self):
            return {
                "items": [
                    {
                        "score": 0.9,
                        "source": "docs/spec.md",
                        "text": "gateway snippet",
                        "trust_class": "canonical",
                    }
                ],
                "filters": {"repo": "moonmind"},
                "budgets": {"tokens": 10},
                "usage": {"tokens": 8, "latency_ms": 4},
                "context_text": "### Retrieved Context\n1. docs/spec.md",
                "retrieved_at": "2026-04-24T00:00:00Z",
                "telemetry_id": "tid",
                "initiation_mode": "session",
                "truncated": False,
            }

        def raise_for_status(self):
            return None

    class _GatewayClient:
        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return _GatewayResponse()

    monkeypatch.setattr("moonmind.rag.service.EmbeddingClient", _UnexpectedEmbeddingClient)
    monkeypatch.setattr("moonmind.rag.service.httpx.Client", _GatewayClient)

    service = ContextRetrievalService(settings=settings, env={}, qdrant_client=_StubQdrant())
    pack = service.retrieve(
        query="gateway query",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={"tokens": 5000},
        transport="gateway",
        initiation_mode="session",
    )

    assert pack.transport == "gateway"
    assert pack.initiation_mode == "session"
    assert pack.truncated is False
    assert pack.filters == {"repo": "moonmind"}
    assert pack.budgets == {"tokens": 10}
    assert pack.usage == {"tokens": 8, "latency_ms": 4}
    assert pack.items[0].source == "docs/spec.md"
    assert "Retrieved Context" in pack.context_text



def test_retrieve_direct_flow_does_not_serialize_secret_env_values() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    service = ContextRetrievalService(
        settings=_settings(run_id="run-secret-safe"),
        env={
            "GOOGLE_API_KEY": "google-secret-key",
            "OPENAI_API_KEY": "openai-secret-key",
            "MOONMIND_WORKER_TOKEN": "worker-token-secret",
        },
        embedding_client=embedder,
        qdrant_client=qdrant,
    )

    pack = service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        initiation_mode="automatic",
    )

    serialized = pack.to_json()
    assert "google-secret-key" not in serialized
    assert "openai-secret-key" not in serialized
    assert "worker-token-secret" not in serialized
