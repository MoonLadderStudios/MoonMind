"""Unit tests for the retired vector guardrail boundary (#4112)."""

from __future__ import annotations

from unittest.mock import patch

from moonmind.rag.guardrails import ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings


def _settings(**overrides: object) -> RagRuntimeSettings:
    defaults = dict(
        qdrant_url=None,
        qdrant_host="localhost",
        qdrant_port=6333,
        qdrant_api_key=None,
        vector_collection="test_collection",
        vector_collections=("test_collection",),
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
        memory_enabled=True,
        memory_planning="off",
        memory_history="off",
        memory_long_term="off",
        memory_fail_open=True,
        memory_context_budget_tokens=4096,
        planning_workspace_root=None,
        beads_command="bd",
        memory_namespace_id="default",
        mem0_api_key=None,
        mem0_user_id=None,
    )
    defaults.update(overrides)
    return RagRuntimeSettings(**defaults)


def test_ensure_rag_ready_noop_when_disabled() -> None:
    """RAG disabled succeeds without touching any backend."""

    settings = _settings(rag_enabled=False)
    ensure_rag_ready(settings)  # should not raise


def test_ensure_rag_ready_never_probes_vector_backend() -> None:
    """Retired guardrail performs no Qdrant or gateway network access."""

    settings = _settings(
        qdrant_enabled=False,
        retrieval_gateway_url=None,
    )
    with patch("httpx.get") as mock_get:
        ensure_rag_ready(settings)
        mock_get.assert_not_called()


def test_ensure_rag_ready_ignores_gateway_url_without_network() -> None:
    """Even a configured gateway URL triggers no health probe (#4112)."""

    settings = _settings(retrieval_gateway_url="http://gw:8000")
    with patch("httpx.get") as mock_get:
        ensure_rag_ready(settings)
        mock_get.assert_not_called()
