"""Core retrieval service shared by CLI and RetrievalGateway."""

from __future__ import annotations

import os
import uuid
import time
from typing import Any, Mapping

import httpx

from moonmind.rag.context_pack import ContextItem, ContextPack, build_context_pack
from moonmind.rag.embedding import EmbeddingClient, EmbeddingConfig
from moonmind.rag.qdrant_client import RagQdrantClient
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.rag.telemetry import VectorTelemetry


class RetrievalBudgetExceededError(RuntimeError):
    """Raised when retrieval budgets are exceeded."""

    def __init__(self, message: str, *, budget_type: str) -> None:
        super().__init__(message)
        self.budget_type = budget_type


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
        self._worker_token = str(self._env.get("MOONMIND_WORKER_TOKEN", "")).strip() or None
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
        normalized_budgets = self._normalize_budgets(budgets)
        self._enforce_token_budget(query=query, top_k=top_k, budgets=normalized_budgets)
        started = time.perf_counter()
        if transport == "gateway":
            return self._retrieve_via_gateway(
                query=query,
                filters=filters,
                top_k=top_k,
                overlay_policy=overlay_policy,
                budgets=normalized_budgets,
            )
        self._qdrant.ensure_collection_ready()
        with self._telemetry.timer("embedding"):
            vector = self._embedding.embed(query)
        overlay_collection = None
        if (
            overlay_policy == "include"
            and self._settings.run_id
            and self._settings.overlay_mode == "collection"
        ):
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
        self._enforce_latency_budget(started=started, budgets=normalized_budgets, usage=usage)
        telemetry_id = uuid.uuid4().hex
        return build_context_pack(
            items=result.items,
            filters=filters,
            budgets=normalized_budgets,
            usage=usage,
            transport=transport,
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
        headers: dict[str, str] = {}
        if self._worker_token:
            headers["Authorization"] = f"Bearer {self._worker_token}"
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(
                f"RetrievalGateway request failed with status {status_code}. Verify MOONMIND_RETRIEVAL_URL and worker token permissions."
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "RetrievalGateway request failed due to a network error. Verify connectivity to MOONMIND_RETRIEVAL_URL."
            ) from exc
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

    @staticmethod
    def _normalize_budgets(budgets: Mapping[str, Any]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for key in ("tokens", "latency_ms"):
            raw = budgets.get(key)
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                normalized[key] = value
        return normalized

    def _enforce_token_budget(
        self,
        *,
        query: str,
        top_k: int,
        budgets: Mapping[str, int],
    ) -> None:
        token_budget = budgets.get("tokens")
        if not token_budget:
            return
        estimated = _estimate_tokens(query) + (
            top_k * max(1, self._settings.overlay_chunk_chars // 4)
        )
        if estimated > token_budget:
            raise RetrievalBudgetExceededError(
                f"Token budget exceeded before retrieval ({estimated}>{token_budget}). Reduce top_k or increase budgets.tokens."
                ,
                budget_type="tokens",
            )

    @staticmethod
    def _enforce_latency_budget(
        *,
        started: float,
        budgets: Mapping[str, int],
        usage: Mapping[str, Any],
    ) -> None:
        latency_budget = budgets.get("latency_ms")
        if not latency_budget:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > latency_budget:
            actual = usage.get("latency_ms", round(elapsed_ms, 2))
            raise RetrievalBudgetExceededError(
                f"Latency budget exceeded ({actual}ms>{latency_budget}ms)."
                ,
                budget_type="latency_ms",
            )
