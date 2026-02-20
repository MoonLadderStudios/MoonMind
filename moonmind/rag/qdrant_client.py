"""Thin wrapper around qdrant-client geared toward worker retrieval."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Sequence
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from moonmind.rag.context_pack import ContextItem

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchResult:
    items: List[ContextItem]
    latency_ms: float


class RagQdrantClient:
    """High-level helper that enforces guardrails for vector operations."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        url: Optional[str],
        api_key: Optional[str],
        collection: str,
        overlay_mode: str,
        overlay_ttl_hours: int,
        overlay_chunk_chars: int,
        overlay_chunk_overlap: int,
        embedding_dimensions: Optional[int],
    ) -> None:
        self.collection = collection
        self.overlay_mode = overlay_mode
        self.overlay_ttl_hours = overlay_ttl_hours
        self.overlay_chunk_chars = overlay_chunk_chars
        self.overlay_chunk_overlap = overlay_chunk_overlap
        self._embedding_dimensions = embedding_dimensions
        if url:
            self._client = QdrantClient(url=url, api_key=api_key, timeout=10)
        else:
            self._client = QdrantClient(host=host, port=port, api_key=api_key, timeout=10)

    @property
    def client(self) -> QdrantClient:
        return self._client

    def ensure_collection_ready(self, collection_name: Optional[str] = None) -> None:
        target = collection_name or self.collection
        if not self._embedding_dimensions:
            return
        try:
            info = self._client.get_collection(target)
        except UnexpectedResponse as exc:
            raise RuntimeError(f"Qdrant collection '{target}' is not available: {exc}") from exc
        size = info.config.params.vectors.size
        if size != self._embedding_dimensions:
            raise RuntimeError(
                f"Qdrant collection '{target}' vector size {size} does not match embedding dimension {self._embedding_dimensions}."
            )

    def _build_filter(self, filters: Mapping[str, Any]) -> Optional[qmodels.Filter]:
        must: List[qmodels.FieldCondition] = []
        for key, raw_value in filters.items():
            if raw_value is None:
                continue
            must.append(
                qmodels.FieldCondition(
                    key=key,
                    match=qmodels.MatchValue(value=raw_value),
                )
            )
        if not must:
            return None
        return qmodels.Filter(must=must)

    def search(
        self,
        *,
        query_vector: Sequence[float],
        filters: Mapping[str, Any],
        top_k: int,
        overlay_policy: str,
        overlay_collection: Optional[str],
        trust_overrides: Optional[Mapping[str, str]] = None,
    ) -> SearchResult:
        start = time.perf_counter()
        filter_obj = self._build_filter(filters)
        canonical = self._client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
            query_filter=filter_obj,
        )
        overlay_points = []
        if overlay_policy == "include" and overlay_collection:
            try:
                overlay_filter = self._build_filter(filters)
                overlay_points = self._client.search(
                    collection_name=overlay_collection,
                    query_vector=query_vector,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,
                    query_filter=overlay_filter,
                )
            except UnexpectedResponse:
                overlay_points = []
        merge = self._merge_results(overlay_points, canonical, trust_overrides)
        latency_ms = (time.perf_counter() - start) * 1000
        return SearchResult(items=merge, latency_ms=latency_ms)

    def _merge_results(
        self,
        overlay_points: Iterable[qmodels.ScoredPoint],
        canonical_points: Iterable[qmodels.ScoredPoint],
        trust_overrides: Optional[Mapping[str, str]] = None,
    ) -> List[ContextItem]:
        def resolve_source(payload: MutableMapping[str, Any]) -> str:
            for key in ("source", "path", "file_path", "document_path"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            if "metadata" in payload and isinstance(payload["metadata"], Mapping):
                metadata = payload["metadata"]
                for key in ("source", "path"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
            return str(payload.get("id") or payload.get("point_id") or "unknown")

        def resolve_text(payload: MutableMapping[str, Any]) -> str:
            for key in ("text", "content", "chunk", "body"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return ""

        def to_item(point: qmodels.ScoredPoint, default_trust: str) -> ContextItem:
            payload = point.payload or {}
            trust = payload.get("trust_class") or default_trust
            chunk_hash = payload.get("chunk_hash") or payload.get("chunk-id")
            return ContextItem(
                score=float(point.score or 0.0),
                source=resolve_source(payload),
                text=resolve_text(payload),
                offset_start=payload.get("offset_start"),
                offset_end=payload.get("offset_end"),
                trust_class=trust,
                chunk_hash=chunk_hash,
                payload=payload,
            )

        ordered: List[ContextItem] = []
        seen: set[tuple[str, Optional[str]]] = set()

        def append(points: Iterable[qmodels.ScoredPoint], trust: str) -> None:
            for point in points:
                item = to_item(point, trust)
                key = (item.source, item.chunk_hash)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(item)

        append(overlay_points, "workspace_overlay")
        append(canonical_points, "canonical")

        if trust_overrides:
            for item in ordered:
                if item.source in trust_overrides:
                    item.trust_class = trust_overrides[item.source]
        return ordered

    def ensure_overlay_collection(self, collection_name: str) -> None:
        if self.overlay_mode != "collection":
            return
        try:
            self._client.get_collection(collection_name)
            return
        except UnexpectedResponse:
            if not self._embedding_dimensions:
                raise RuntimeError("Cannot create overlay collection: unknown vector size")
            self._create_collection(collection_name=collection_name, vector_size=self._embedding_dimensions)

    def upsert_overlay_vectors(
        self,
        *,
        collection_name: str,
        vectors: List[List[float]],
        payloads: List[MutableMapping[str, Any]],
    ) -> None:
        ids = [str(uuid4()) for _ in vectors]
        batch = qmodels.Batch(ids=ids, vectors=vectors, payloads=payloads)
        self._client.upsert(collection_name=collection_name, points=batch)

    def delete_overlay_collection(self, collection_name: str) -> None:
        try:
            self._client.delete_collection(collection_name=collection_name)
        except UnexpectedResponse:
            logger.debug("Overlay collection %s already absent", collection_name)

    def sync_collection_dimensions(
        self,
        *,
        collection_name: str,
        expected_size: int,
        force: bool = False,
    ) -> str:
        if expected_size <= 0:
            raise RuntimeError("Expected embedding dimension must be positive")
        try:
            info = self._client.get_collection(collection_name)
        except UnexpectedResponse:
            self._create_collection(collection_name=collection_name, vector_size=expected_size)
            return "created"

        current_size = info.config.params.vectors.size
        if current_size == expected_size:
            return "unchanged"
        if not force:
            raise RuntimeError(
                "Qdrant collection "
                f"'{collection_name}' uses vector size {current_size} which does not match the expected {expected_size}. "
                "Re-run with --force after reindexing to recreate the collection."
            )

        self._client.delete_collection(collection_name=collection_name)
        self._create_collection(collection_name=collection_name, vector_size=expected_size)
        return "recreated"

    def _create_collection(self, *, collection_name: str, vector_size: int) -> None:
        vectors = qmodels.VectorParams(
            size=vector_size,
            distance=qmodels.Distance.COSINE,
        )
        self._client.create_collection(collection_name=collection_name, vectors_config=vectors)
