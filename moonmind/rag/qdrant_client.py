"""Thin wrapper around qdrant-client geared toward worker retrieval."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass(frozen=True, slots=True)
class IndexCollectionHealth:
    name: str
    status: str
    points_count: int | None
    indexed_vectors_count: int | None
    segments_count: int | None
    vector_size: int | None
    vector_distance: str | None
    freshness_at: str | None
    freshness_source: str | None
    freshness_status: str


@dataclass(frozen=True, slots=True)
class IndexHealthSummary:
    generated_at: str
    total_collections: int
    total_points: int
    collections: list[IndexCollectionHealth]


@dataclass(slots=True)
class CollectionHealth:
    name: str
    status: str
    vectors_count: int | None = None
    points_count: int | None = None
    indexed_vectors_count: int | None = None
    dimensions: int | None = None
    freshness: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "vectors_count": self.vectors_count,
            "points_count": self.points_count,
            "indexed_vectors_count": self.indexed_vectors_count,
            "dimensions": self.dimensions,
            "freshness": self.freshness,
            "error": self.error,
        }


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
            self._client = QdrantClient(
                host=host, port=port, api_key=api_key, timeout=10
            )

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
            raise RuntimeError(
                f"Qdrant collection '{target}' is not available: {exc}"
            ) from exc
        size = info.config.params.vectors.size
        if size != self._embedding_dimensions:
            raise RuntimeError(
                f"Qdrant collection '{target}' vector size {size} does not match embedding dimension {self._embedding_dimensions}."
            )

    def index_health(self, *, freshness_sample_limit: int = 256) -> IndexHealthSummary:
        """Return read-only collection health for Mission Control index monitoring."""

        generated_at = datetime.now(timezone.utc).isoformat()
        response = self._client.get_collections()
        collection_names = sorted(
            str(collection.name)
            for collection in getattr(response, "collections", [])
            if str(getattr(collection, "name", "")).strip()
        )
        collections: list[IndexCollectionHealth] = []

        for collection_name in collection_names:
            try:
                info = self._client.get_collection(collection_name)
            except Exception:
                logger.warning(
                    "Unable to retrieve health for Qdrant collection %s",
                    collection_name,
                    exc_info=True,
                )
                collections.append(
                    IndexCollectionHealth(
                        name=collection_name,
                        status="unavailable",
                        points_count=None,
                        indexed_vectors_count=None,
                        segments_count=None,
                        vector_size=None,
                        vector_distance=None,
                        freshness_at=None,
                        freshness_source=None,
                        freshness_status="unknown",
                    )
                )
                continue
            vector_size, vector_distance = self._vector_config_summary(info)
            points_count = self._optional_int(getattr(info, "points_count", None))
            if points_count and points_count > 0:
                freshness_at, freshness_source = self._collection_freshness(
                    collection_name=collection_name,
                    limit=freshness_sample_limit,
                )
            else:
                freshness_at, freshness_source = None, None
            collections.append(
                IndexCollectionHealth(
                    name=collection_name,
                    status=self._string_value(getattr(info, "status", "unknown")),
                    points_count=points_count,
                    indexed_vectors_count=self._optional_int(
                        getattr(info, "indexed_vectors_count", None)
                    ),
                    segments_count=self._optional_int(
                        getattr(info, "segments_count", None)
                    ),
                    vector_size=vector_size,
                    vector_distance=vector_distance,
                    freshness_at=freshness_at,
                    freshness_source=freshness_source,
                    freshness_status=(
                        "empty"
                        if points_count == 0
                        else "known"
                        if freshness_at
                        else "unknown"
                    ),
                )
            )

        total_points = sum(
            collection.points_count or 0 for collection in collections
        )
        return IndexHealthSummary(
            generated_at=generated_at,
            total_collections=len(collections),
            total_points=total_points,
            collections=collections,
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

    def _collection_freshness(
        self, *, collection_name: str, limit: int
    ) -> tuple[str | None, str | None]:
        if limit <= 0 or not hasattr(self._client, "scroll"):
            return None, None
        offset: Any = None
        seen_offsets: set[str] = set()
        points: list[Any] = []
        try:
            while True:
                kwargs: dict[str, Any] = {
                    "collection_name": collection_name,
                    "limit": limit,
                    "with_payload": True,
                    "with_vectors": False,
                }
                if offset is not None:
                    kwargs["offset"] = offset
                page_points, offset = self._client.scroll(**kwargs)
                points.extend(page_points)
                if offset is None:
                    break
                offset_key = repr(offset)
                if offset_key in seen_offsets:
                    logger.warning(
                        "Qdrant scroll returned repeated offset for collection %s",
                        collection_name,
                    )
                    break
                seen_offsets.add(offset_key)
        except Exception:
            logger.debug(
                "Unable to sample freshness for collection %s",
                collection_name,
                exc_info=True,
            )
            return None, None

        latest: datetime | None = None
        latest_source: str | None = None
        for point in points:
            payload = getattr(point, "payload", None) or {}
            if not isinstance(payload, Mapping):
                continue
            timestamp, source = self._payload_freshness(payload)
            if timestamp is None:
                continue
            if latest is None or timestamp > latest:
                latest = timestamp
                latest_source = source

        if latest is None:
            return None, None
        return latest.isoformat(), latest_source

    @classmethod
    def _payload_freshness(
        cls, payload: Mapping[str, Any]
    ) -> tuple[datetime | None, str | None]:
        for key in (
            "indexed_at",
            "last_indexed_at",
            "source_updated_at",
            "updated_at",
            "modified_at",
            "created_at",
        ):
            timestamp = cls._parse_timestamp(payload.get(key))
            if timestamp is not None:
                return timestamp, key
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            timestamp, source = cls._payload_freshness(metadata)
            if timestamp is not None and source is not None:
                return timestamp, f"metadata.{source}"
        return None, None

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _string_value(cls, value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw or "unknown")

    @classmethod
    def _vector_config_summary(cls, info: Any) -> tuple[int | None, str | None]:
        vectors = getattr(
            getattr(getattr(info, "config", None), "params", None), "vectors", None
        )
        if isinstance(vectors, Mapping):
            first = next(iter(vectors.values()), None)
            return cls._vector_params_summary(first)
        return cls._vector_params_summary(vectors)

    @classmethod
    def _vector_params_summary(cls, vectors: Any) -> tuple[int | None, str | None]:
        if vectors is None:
            return None, None
        size = cls._optional_int(getattr(vectors, "size", None))
        distance = getattr(vectors, "distance", None)
        return size, cls._string_value(distance) if distance is not None else None

    def search(
        self,
        *,
        query_vector: Sequence[float],
        filters: Mapping[str, Any],
        top_k: int,
        collections: Sequence[str] | None = None,
        overlay_policy: str,
        overlay_collection: Optional[str],
        trust_overrides: Optional[Mapping[str, str]] = None,
    ) -> SearchResult:
        start = time.perf_counter()
        filter_obj = self._build_filter(filters)
        target_collections = self._resolve_collections(collections)
        canonical = self._search_canonical_collections(
            collection_names=target_collections,
            query_vector=query_vector,
            limit=top_k,
            query_filter=filter_obj,
        )
        canonical = self._rank_points(canonical)
        overlay_points = []
        if overlay_policy == "include" and overlay_collection:
            try:
                overlay_filter = self._build_filter(filters)
                overlay_points = self._search_collection_points(
                    collection_name=overlay_collection,
                    query_vector=query_vector,
                    limit=top_k,
                    query_filter=overlay_filter,
                )
            except UnexpectedResponse:
                overlay_points = []
        merge = self._merge_results(overlay_points, canonical, trust_overrides)
        merge.sort(key=lambda item: item.score, reverse=True)
        latency_ms = (time.perf_counter() - start) * 1000
        return SearchResult(items=merge[:top_k], latency_ms=latency_ms)

    def _resolve_collections(self, collections: Sequence[str] | None) -> tuple[str, ...]:
        names: list[str] = []
        seen: set[str] = set()
        for item in collections or (self.collection,):
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        if not names:
            raise RuntimeError("At least one Qdrant collection is required.")
        return tuple(names)

    def _search_canonical_collections(
        self,
        *,
        collection_names: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
        query_filter: qmodels.Filter | None,
    ) -> list[qmodels.ScoredPoint]:
        if len(collection_names) == 1:
            return self._search_collection_points(
                collection_name=collection_names[0],
                query_vector=query_vector,
                limit=limit,
                query_filter=query_filter,
            )

        canonical: list[qmodels.ScoredPoint] = []
        with ThreadPoolExecutor(max_workers=len(collection_names)) as executor:
            futures = [
                executor.submit(
                    self._search_collection_points,
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=limit,
                    query_filter=query_filter,
                )
                for collection_name in collection_names
            ]
            for future in futures:
                canonical.extend(future.result())
        return canonical

    def _search_collection_points(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        query_filter: qmodels.Filter | None,
    ) -> list[qmodels.ScoredPoint]:
        points = self._search_points(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
            query_filter=query_filter,
        )
        for point in points:
            payload = point.payload or {}
            payload.setdefault("collection", collection_name)
            point.payload = payload
        return points

    @staticmethod
    def _rank_points(points: Iterable[qmodels.ScoredPoint]) -> list[qmodels.ScoredPoint]:
        return sorted(points, key=lambda point: float(point.score or 0.0), reverse=True)

    def _search_points(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        with_payload: bool,
        with_vectors: bool,
        query_filter: qmodels.Filter | None,
    ) -> list[qmodels.ScoredPoint]:
        if hasattr(self._client, "search"):
            return self._client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=with_payload,
                with_vectors=with_vectors,
                query_filter=query_filter,
            )

        query_response = self._client.query_points(
            collection_name=collection_name,
            query=list(query_vector),
            limit=limit,
            with_payload=with_payload,
            with_vectors=with_vectors,
            query_filter=query_filter,
        )
        return list(query_response.points)

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
        seen: set[tuple[str, str]] = set()
        now_utc = datetime.now(timezone.utc)

        def _is_expired(payload: MutableMapping[str, Any]) -> bool:
            raw_expires = payload.get("expires_at")
            if not isinstance(raw_expires, str) or not raw_expires.strip():
                return False
            normalized = raw_expires.strip()
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            try:
                expires_at = datetime.fromisoformat(normalized)
            except ValueError:
                return False
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at <= now_utc

        def append(points: Iterable[qmodels.ScoredPoint], trust: str) -> None:
            for point in points:
                payload = point.payload or {}
                if trust == "workspace_overlay" and _is_expired(payload):
                    continue
                item = to_item(point, trust)
                discriminator = item.chunk_hash
                if not discriminator:
                    discriminator = f"id:{point.id}|offset:{item.offset_start}:{item.offset_end}|score:{item.score:.8f}"
                key = (item.source, discriminator)
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
                raise RuntimeError(
                    "Cannot create overlay collection: unknown vector size"
                )
            self._create_collection(
                collection_name=collection_name, vector_size=self._embedding_dimensions
            )

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

    def upsert_canonical_vectors(
        self,
        *,
        collection_name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[MutableMapping[str, Any]],
    ) -> None:
        if not (len(ids) == len(vectors) == len(payloads)):
            raise RuntimeError(
                "Canonical vector upsert requires equal id, vector, and payload counts"
            )
        if not ids:
            return
        batch = qmodels.Batch(
            ids=list(ids),
            vectors=[list(vector) for vector in vectors],
            payloads=list(payloads),
        )
        self._client.upsert(collection_name=collection_name, points=batch)

    def delete_vectors(
        self,
        *,
        collection_name: str,
        point_ids: Sequence[str],
    ) -> None:
        if not point_ids:
            return
        selector = qmodels.PointIdsList(points=list(point_ids))
        self._client.delete(collection_name=collection_name, points_selector=selector)

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
            self._create_collection(
                collection_name=collection_name, vector_size=expected_size
            )
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
        self._create_collection(
            collection_name=collection_name, vector_size=expected_size
        )
        return "recreated"

    def _create_collection(self, *, collection_name: str, vector_size: int) -> None:
        vectors = qmodels.VectorParams(
            size=vector_size,
            distance=qmodels.Distance.COSINE,
        )
        self._client.create_collection(
            collection_name=collection_name, vectors_config=vectors
        )

    def collection_health(
        self,
        *,
        collection_names: Sequence[str] | None = None,
    ) -> list[CollectionHealth]:
        targets = tuple(collection_names or (self.collection,))
        health: list[CollectionHealth] = []
        for name in targets:
            try:
                info = self._client.get_collection(name)
            except Exception as exc:
                health.append(
                    CollectionHealth(
                        name=name,
                        status="unavailable",
                        error=str(exc),
                    )
                )
                continue
            vectors_config = getattr(getattr(info, "config", None), "params", None)
            vectors = getattr(vectors_config, "vectors", None)
            dimensions = getattr(vectors, "size", None)
            if dimensions is None:
                dimensions = getattr(vectors_config, "size", None)
            points_count = getattr(info, "points_count", None)
            vectors_count = getattr(info, "vectors_count", None)
            indexed_vectors_count = getattr(info, "indexed_vectors_count", None)
            if points_count is None:
                points_count = vectors_count
            freshness = "empty" if not points_count else "ready"
            health.append(
                CollectionHealth(
                    name=name,
                    status=str(getattr(info, "status", "available")),
                    vectors_count=vectors_count,
                    points_count=points_count,
                    indexed_vectors_count=indexed_vectors_count,
                    dimensions=dimensions,
                    freshness=freshness,
                )
            )
        return health
