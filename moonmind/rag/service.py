"""Core retrieval service shared by CLI and RetrievalGateway."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Mapping

import httpx

from moonmind.rag.context_pack import ContextItem, ContextPack, build_context_pack
from moonmind.rag.embedding import EmbeddingClient, EmbeddingConfig
from moonmind.rag.long_term_memory import LongTermMemoryError, LongTermMemoryService
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
        long_term_memory_service: LongTermMemoryService | None = None,
    ) -> None:
        self._settings = settings
        self._env = env or os.environ
        self._retrieval_token = (
            str(self._env.get("MOONMIND_RETRIEVAL_TOKEN", "")).strip() or None
        )
        self._telemetry = VectorTelemetry(
            run_id=settings.run_id, job_id=settings.job_id
        )
        self._embedding = embedding_client
        self._long_term_memory = long_term_memory_service
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
        if self._embedding is None:
            self._embedding = EmbeddingClient(
                EmbeddingConfig(
                    provider=self._settings.embedding_provider,
                    model=self._settings.embedding_model,
                    google_api_key=self._env.get("GOOGLE_API_KEY"),
                    openai_api_key=self._env.get("OPENAI_API_KEY"),
                )
            )
        return self._embedding

    @property
    def qdrant_client(self) -> RagQdrantClient:
        return self._qdrant

    @property
    def long_term_memory_service(self) -> LongTermMemoryService:
        if self._long_term_memory is None:
            self._long_term_memory = LongTermMemoryService(settings=self._settings)
        return self._long_term_memory

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
        initiation_mode: str = "automatic",
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
                initiation_mode=initiation_mode,
            )
        self._qdrant.ensure_collection_ready()
        with self._telemetry.timer("embedding"):
            vector = self.embedding_client.embed(query)
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
        memory_items, memory_latency_ms = self._retrieve_long_term_memory_items(
            query=query,
            filters=filters,
        )
        items = [*memory_items, *result.items]
        usage = {
            "tokens": _estimate_tokens(query)
            + sum(_estimate_tokens(item.text) for item in items),
            "latency_ms": round(result.latency_ms + memory_latency_ms, 2),
        }
        self._enforce_latency_budget(
            started=started, budgets=normalized_budgets, usage=usage
        )
        telemetry_id = uuid.uuid4().hex
        return build_context_pack(
            items=items,
            filters=filters,
            budgets=normalized_budgets,
            usage=usage,
            transport=transport,
            telemetry_id=telemetry_id,
            max_chars=self._settings.max_context_chars,
            initiation_mode=initiation_mode,
        )

    def _retrieve_via_gateway(
        self,
        *,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
        overlay_policy: str,
        budgets: Mapping[str, Any],
        initiation_mode: str,
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
        if self._retrieval_token:
            headers["X-MoonMind-Retrieval-Token"] = self._retrieval_token
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = (
                exc.response.status_code if exc.response is not None else "unknown"
            )
            raise RuntimeError(
                "RetrievalGateway request failed with status "
                f"{status_code}. Verify MOONMIND_RETRIEVAL_URL and "
                "retrieval token permissions."
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "RetrievalGateway request failed due to a network error. "
                "Verify connectivity to MOONMIND_RETRIEVAL_URL."
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
            initiation_mode=str(data.get("initiation_mode") or initiation_mode),
            truncated=bool(data.get("truncated", False)),
        )

    def add_or_update_long_term_memory(
        self,
        *,
        text: str,
        repo: str,
        scope: str = "project",
        review_state: str = "draft",
        provenance: Mapping[str, Any],
        memory_id: str | None = None,
    ) -> Any:
        """Promote a stable learning into Mem0 Plane C memory."""

        executable, reason = self._settings.long_term_memory_execution_reason()
        if not executable:
            if self._settings.memory_fail_open:
                return {"skipped": True, "reason": reason}
            raise LongTermMemoryError(reason)
        try:
            return self.long_term_memory_service.add_or_update(
                text=text,
                repo=repo,
                scope=scope,
                review_state=review_state,
                provenance=provenance,
                memory_id=memory_id,
            )
        except LongTermMemoryError:
            if self._settings.memory_fail_open:
                return {"skipped": True, "reason": "long_term_memory_unavailable"}
            raise

    def _retrieve_long_term_memory_items(
        self,
        *,
        query: str,
        filters: Mapping[str, Any],
    ) -> tuple[list[ContextItem], float]:
        executable, reason = self._settings.long_term_memory_execution_reason()
        if not executable:
            if self._settings.memory_fail_open:
                return [], 0.0
            raise LongTermMemoryError(reason)
        repo = self._repo_from_filters(filters)
        try:
            return self.long_term_memory_service.search(
                query=query,
                repo=repo,
                scope="project",
                limit=self._memory_top_k(),
            )
        except LongTermMemoryError:
            if self._settings.memory_fail_open:
                return [], 0.0
            raise

    def _memory_top_k(self) -> int:
        if not self._settings.memory_context_budget_tokens:
            return min(3, self._settings.similarity_top_k)
        estimated_item_tokens = max(1, self._settings.overlay_chunk_chars // 4)
        return max(
            1,
            self._settings.memory_context_budget_tokens // estimated_item_tokens,
        )

    @staticmethod
    def _repo_from_filters(filters: Mapping[str, Any]) -> str | None:
        for key in ("repo", "repository"):
            value = str(filters.get(key) or "").strip()
            if value:
                return value
        return None

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
                "Token budget exceeded before retrieval "
                f"({estimated}>{token_budget}). Reduce top_k or increase "
                "budgets.tokens.",
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
                f"Latency budget exceeded ({actual}ms>{latency_budget}ms).",
                budget_type="latency_ms",
            )
