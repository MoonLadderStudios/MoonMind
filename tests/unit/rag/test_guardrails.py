"""Unit tests for RAG guardrails (DOC-REQ-002, DOC-REQ-004)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
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

def test_ensure_rag_ready_noop_when_disabled() -> None:
    """RAG disabled should succeed without touching Qdrant."""
    settings = _settings(rag_enabled=False)
    ensure_rag_ready(settings)  # should not raise

def test_ensure_rag_ready_raises_when_no_qdrant_and_no_gateway() -> None:
    """Direct transport with Qdrant disabled and no gateway should fail."""
    settings = _settings(qdrant_enabled=False, retrieval_gateway_url=None)
    with pytest.raises(GuardrailError, match="Qdrant access disabled"):
        ensure_rag_ready(settings)

def test_ensure_rag_ready_uses_gateway_when_url_set() -> None:
    """Gateway transport should verify gateway health, not direct Qdrant."""
    settings = _settings(retrieval_gateway_url="http://gw:8000")

    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch.object(httpx, "get", return_value=mock_response) as mock_get:
        ensure_rag_ready(settings)
        mock_get.assert_called_once_with("http://gw:8000/health", timeout=5.0)

def test_ensure_rag_ready_raises_on_gateway_health_failure() -> None:
    """Gateway health check failure should raise GuardrailError."""
    settings = _settings(retrieval_gateway_url="http://gw:8000")

    mock_response = MagicMock()
    mock_response.status_code = 503
    with patch("httpx.get", return_value=mock_response):
        with pytest.raises(GuardrailError, match="health check failed"):
            ensure_rag_ready(settings)
