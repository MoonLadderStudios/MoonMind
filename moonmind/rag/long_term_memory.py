"""Long-term memory adapter for Mem0-backed Plane C retrieval."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Protocol, Sequence

from moonmind.rag.context_pack import ContextItem
from moonmind.rag.settings import RagRuntimeSettings


class LongTermMemoryError(RuntimeError):
    """Raised when long-term memory cannot be used as configured."""


class Mem0ClientProtocol(Protocol):
    """Small protocol covering the Mem0 SDK methods MoonMind uses."""

    def search(self, query: str, **kwargs: Any) -> Any: ...

    def add(self, memory: str, **kwargs: Any) -> Any: ...

    def update(self, memory_id: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class LongTermMemory:
    """Normalized long-term memory entry with required Plane C metadata."""

    text: str
    memory_id: str | None = None
    score: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LongTermMemoryService:
    """Adapter-bound service for Mem0 long-term memory operations."""

    def __init__(
        self,
        *,
        settings: RagRuntimeSettings,
        client: Mem0ClientProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Mem0ClientProtocol:
        if self._client is None:
            self._client = self._build_mem0_client()
        return self._client

    def search(
        self,
        *,
        query: str,
        repo: str | None,
        scope: str = "project",
        limit: int | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> tuple[list[ContextItem], float]:
        """Search approved Mem0 memories and return context-pack items."""

        started = time.perf_counter()
        metadata_filter = self._metadata_filter(
            repo=repo,
            scope=scope,
            review_state="approved",
            extra=filters,
        )
        kwargs: dict[str, Any] = {
            "user_id": self._user_id(scope=scope, repo=repo),
            "metadata": metadata_filter,
        }
        if limit is not None and limit > 0:
            kwargs["limit"] = limit
        try:
            raw = self.client.search(query, **kwargs)
        except Exception as exc:  # pragma: no cover - SDK-specific failures
            raise LongTermMemoryError("Mem0 search failed") from exc
        memories = self._normalize_search_result(raw)
        items = [self._to_context_item(memory) for memory in memories]
        latency_ms = (time.perf_counter() - started) * 1000
        return items, latency_ms

    def add_or_update(
        self,
        *,
        text: str,
        repo: str,
        scope: str = "project",
        review_state: str = "draft",
        provenance: Mapping[str, Any],
        memory_id: str | None = None,
    ) -> Any:
        """Add or update one Mem0 long-term memory with Plane C metadata."""

        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise LongTermMemoryError("Long-term memory text is required")
        metadata = self._memory_metadata(
            repo=repo,
            scope=scope,
            review_state=review_state,
            provenance=provenance,
        )
        kwargs = {
            "user_id": self._user_id(scope=scope, repo=repo),
            "metadata": metadata,
        }
        try:
            if memory_id:
                return self.client.update(
                    memory_id,
                    data=normalized_text,
                    **kwargs,
                )
            return self.client.add(normalized_text, **kwargs)
        except Exception as exc:  # pragma: no cover - SDK-specific failures
            raise LongTermMemoryError("Mem0 write failed") from exc

    def _build_mem0_client(self) -> Mem0ClientProtocol:
        if not self._settings.mem0_api_key:
            raise LongTermMemoryError("MEM0_API_KEY is required for Mem0 memory")
        try:
            module = importlib.import_module("mem0")
        except ImportError as exc:
            raise LongTermMemoryError(
                "Mem0 SDK is not installed; install mem0ai to enable Plane C memory"
            ) from exc
        client_type = getattr(module, "MemoryClient", None)
        if client_type is None:
            raise LongTermMemoryError("Mem0 SDK does not expose MemoryClient")
        return client_type(api_key=self._settings.mem0_api_key)

    def _metadata_filter(
        self,
        *,
        repo: str | None,
        scope: str,
        review_state: str,
        extra: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "namespace_id": self._settings.memory_namespace_id,
            "scope": scope,
            "review_state": review_state,
        }
        if repo:
            metadata["repo"] = repo
        if extra:
            metadata.update(dict(extra))
        return metadata

    def _memory_metadata(
        self,
        *,
        repo: str,
        scope: str,
        review_state: str,
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_repo = str(repo or "").strip()
        if not normalized_repo:
            raise LongTermMemoryError("repo is required for long-term memory")
        normalized_scope = str(scope or "").strip() or "project"
        if normalized_scope not in {"project", "team", "user"}:
            raise LongTermMemoryError("scope must be project, team, or user")
        normalized_review_state = str(review_state or "").strip() or "draft"
        if normalized_review_state not in {"draft", "approved", "deprecated"}:
            raise LongTermMemoryError(
                "review_state must be draft, approved, or deprecated"
            )
        normalized_provenance = {
            key: value
            for key, value in dict(provenance or {}).items()
            if value is not None and str(value).strip()
        }
        if not normalized_provenance:
            raise LongTermMemoryError("provenance is required for long-term memory")
        return {
            "namespace_id": self._settings.memory_namespace_id,
            "repo": normalized_repo,
            "scope": normalized_scope,
            "review_state": normalized_review_state,
            "provenance": normalized_provenance,
        }

    def _user_id(self, *, scope: str, repo: str | None) -> str:
        if self._settings.mem0_user_id:
            return self._settings.mem0_user_id
        repo_part = str(repo or "global").strip().replace("/", ":") or "global"
        return f"{self._settings.memory_namespace_id}:{scope}:{repo_part}"

    @staticmethod
    def _normalize_search_result(raw: Any) -> list[LongTermMemory]:
        if isinstance(raw, Mapping):
            candidates = raw.get("results", raw.get("memories", raw.get("data", [])))
        else:
            candidates = raw
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            return []
        memories: list[LongTermMemory] = []
        for candidate in candidates:
            normalized = LongTermMemoryService._normalize_memory(candidate)
            if normalized is not None:
                memories.append(normalized)
        return memories

    @staticmethod
    def _normalize_memory(candidate: Any) -> LongTermMemory | None:
        if isinstance(candidate, str):
            text = candidate.strip()
            if not text:
                return None
            return LongTermMemory(text=text)
        if not isinstance(candidate, Mapping):
            return None
        text = str(
            candidate.get("memory")
            or candidate.get("text")
            or candidate.get("content")
            or ""
        ).strip()
        if not text:
            return None
        metadata = candidate.get("metadata")
        if not isinstance(metadata, MutableMapping):
            metadata = {}
        score_raw = candidate.get("score", candidate.get("similarity", 0.0))
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        memory_id = candidate.get("id", candidate.get("memory_id"))
        return LongTermMemory(
            text=text,
            memory_id=str(memory_id) if memory_id else None,
            score=score,
            metadata=dict(metadata),
        )

    @staticmethod
    def _to_context_item(memory: LongTermMemory) -> ContextItem:
        source = "mem0"
        if memory.memory_id:
            source = f"mem0:{memory.memory_id}"
        return ContextItem(
            score=memory.score,
            source=source,
            text=memory.text,
            trust_class=str(memory.metadata.get("review_state") or "approved"),
            payload={
                "record_kind": "long_term_memory",
                "memory_provider": "mem0",
                "metadata": dict(memory.metadata),
            },
        )
