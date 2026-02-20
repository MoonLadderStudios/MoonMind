"""Guardrail checks shared between CLI and worker doctor."""

from __future__ import annotations

import httpx

from moonmind.rag.qdrant_client import RagQdrantClient
from moonmind.rag.settings import RagRuntimeSettings


class GuardrailError(RuntimeError):
    """Raised when a required guardrail fails."""


def ensure_rag_ready(settings: RagRuntimeSettings) -> None:
    if not settings.rag_enabled:
        return
    transport = settings.resolved_transport(None)
    if transport == "gateway" and settings.retrieval_gateway_url:
        _verify_gateway(settings.retrieval_gateway_url)
        return
    if not settings.qdrant_enabled:
        raise GuardrailError(
            "Qdrant access disabled while no RetrievalGateway URL configured"
        )
    client = RagQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.vector_collection,
        overlay_mode=settings.overlay_mode,
        overlay_ttl_hours=settings.overlay_ttl_hours,
        overlay_chunk_chars=settings.overlay_chunk_chars,
        overlay_chunk_overlap=settings.overlay_chunk_overlap,
        embedding_dimensions=settings.embedding_dimensions,
    )
    client.ensure_collection_ready()


def _verify_gateway(url: str) -> None:
    try:
        response = httpx.get(url.rstrip("/") + "/health", timeout=5.0)
    except httpx.HTTPError as exc:  # pragma: no cover
        raise GuardrailError(f"RetrievalGateway unreachable: {exc}") from exc
    if response.status_code >= 300:
        raise GuardrailError(
            f"RetrievalGateway health check failed with status {response.status_code}"
        )
