"""Core retrieval service shared by CLI and RetrievalGateway."""

from __future__ import annotations

import os
import uuid
from typing import Any, Mapping

import httpx

from moonmind.rag.context_pack import ContextItem, ContextPack, build_context_pack
from moonmind.rag.embedding import EmbeddingClient, EmbeddingConfig
from moonmind.rag.qdrant_client import RagQdrantClient
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.rag.telemetry import VectorTelemetry


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ContextRetrievalService:
    def __init__(
        self,
        *,
        settings: RagRuntimeSettings,
        env: Mapping[str, str] | None = None,
        embedding_client: EmbeddingClient | None = None,
        qdrant_client: RagQdrantClient | None = None,
    ) -> None:
        self._settings = settings
        self._env = env or os.environ
        self._telemetry = VectorTelemetry(
            run_id=settings.run_id, job_id=settings.job_id
        )
        self._embedding = embedding_client or EmbeddingClient(
            EmbeddingConfig(
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                google_api_key=self._env.get("GOOGLE_API_KEY"),
                openai_api_key=self._env.get("OPENAI_API_KEY"),
                ollama_model=self._env.get("OLLAMA_EMBEDDING_MODEL"),
            )
        )
        self._qdrant = qdrant_client or RagQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            url=settings.qdrant_url,
            api_key=self._env.get("QDRANT_API_KEY"),
            collection=settings.vector_collection,
            overlay_mode=settings.overlay_mode,
            overlay_ttl_hours=settings.overlay_ttl_hours,
            overlay_chunk_chars=settings.overlay_chunk_chars,
            overlay_chunk_overlap=settings.overlay_chunk_overlap,
            embedding_dimensions=settings.embedding_dimensions,
        )

    @property
    def embedding_client(self) -> EmbeddingClient:
        return self._embedding

    @property
    def qdrant_client(self) -> RagQdrantClient:
        return self._qdrant

    @property
    def settings(self) -> RagRuntimeSettings:
        return self._settings

    def retrieve(
        self,
        *,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
        overlay_policy: str,
        budgets: Mapping[str, Any],
        transport: str,
    ) -> ContextPack:
        if transport == "gateway":
            return self._retrieve_via_gateway(
                query=query,
                filters=filters,
                top_k=top_k,
                overlay_policy=overlay_policy,
                budgets=budgets,
            )
        self._qdrant.ensure_collection_ready()
        with self._telemetry.timer("embedding"):
            vector = self._embedding.embed(query)
        overlay_collection = None
        if overlay_policy == "include" and self._settings.run_id:
            overlay_collection = self._settings.overlay_collection_name(
                self._settings.run_id
            )
        with self._telemetry.timer("search"):
            result = self._qdrant.search(
                query_vector=vector,
                filters=filters,
                top_k=top_k,
                overlay_policy=overlay_policy,
                overlay_collection=overlay_collection,
                trust_overrides=None,
            )
        usage = {
            "tokens": _estimate_tokens(query)
            + sum(_estimate_tokens(item.text) for item in result.items),
            "latency_ms": round(result.latency_ms, 2),
        }
        telemetry_id = uuid.uuid4().hex
        return build_context_pack(
            items=result.items,
            filters=filters,
            budgets=budgets,
            usage=usage,
            transport="direct",
            telemetry_id=telemetry_id,
            max_chars=self._settings.max_context_chars,
        )

    def _retrieve_via_gateway(
        self,
        *,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
        overlay_policy: str,
        budgets: Mapping[str, Any],
    ) -> ContextPack:
        if not self._settings.retrieval_gateway_url:
            raise RuntimeError("RetrievalGateway URL is not configured")
        payload = {
            "query": query,
            "filters": dict(filters),
            "top_k": top_k,
            "overlay_policy": overlay_policy,
            "budgets": dict(budgets),
        }
        url = self._settings.retrieval_gateway_url.rstrip("/") + "/context"
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        data = response.json()
        items = [
            ContextItem(
                score=item.get("score", 0.0),
                source=item.get("source", "unknown"),
                text=item.get("text", ""),
                offset_start=item.get("offset_start"),
                offset_end=item.get("offset_end"),
                trust_class=item.get("trust_class", "canonical"),
                chunk_hash=item.get("chunk_hash"),
                payload=item.get("payload", {}),
            )
            for item in data.get("items", [])
        ]
        return ContextPack(
            items=items,
            filters=data.get("filters", {}),
            budgets=data.get("budgets", {}),
            usage=data.get("usage", {}),
            transport="gateway",
            context_text=data.get("context_text", ""),
            retrieved_at=data.get("retrieved_at", ""),
            telemetry_id=data.get("telemetry_id", ""),
        )
