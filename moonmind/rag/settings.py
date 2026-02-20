"""Runtime configuration helpers for worker-side RAG operations."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, MutableMapping, Optional

from moonmind.config.settings import settings as app_settings
from moonmind.utils.env_bool import env_to_bool

_CANONICAL_BOOL_TRUE = {"1", "true", "yes", "on"}


def _get_env(
    source: Mapping[str, str] | None, key: str, default: str | None = None
) -> str | None:
    if source and key in source:
        return str(source[key])
    return os.getenv(key, default)


@dataclass(slots=True)
class RagRuntimeSettings:
    """Normalized settings consumed by CLI, worker doctor, and gateway flows."""

    qdrant_url: Optional[str]
    qdrant_host: str
    qdrant_port: int
    qdrant_api_key: Optional[str]
    vector_collection: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: Optional[int]
    similarity_top_k: int
    max_context_chars: int
    overlay_mode: str
    overlay_ttl_hours: int
    overlay_chunk_chars: int
    overlay_chunk_overlap: int
    retrieval_gateway_url: Optional[str]
    statsd_host: Optional[str]
    statsd_port: Optional[int]
    job_id: Optional[str]
    run_id: Optional[str]
    rag_enabled: bool
    qdrant_enabled: bool

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> "RagRuntimeSettings":
        env = source or {}
        qdrant_url = _get_env(env, "QDRANT_URL")
        qdrant_host = _get_env(env, "QDRANT_HOST", "qdrant") or "qdrant"
        qdrant_port = int(_get_env(env, "QDRANT_PORT", "6333") or 6333)
        qdrant_api_key = _get_env(env, "QDRANT_API_KEY") or None
        vector_collection = (
            _get_env(
                env,
                "VECTOR_STORE_COLLECTION_NAME",
                app_settings.vector_store_collection_name,
            )
            or app_settings.vector_store_collection_name
        )
        embedding_provider = (
            _get_env(
                env,
                "DEFAULT_EMBEDDING_PROVIDER",
                app_settings.default_embedding_provider,
            )
            or app_settings.default_embedding_provider
        ).lower()
        if embedding_provider == "google":
            default_model = app_settings.google.google_embedding_model
            model_key = "GOOGLE_EMBEDDING_MODEL"
        else:
            default_model = getattr(
                app_settings.openai,
                "openai_embedding_model",
                "text-embedding-3-large",
            )
            model_key = "OPENAI_EMBEDDING_MODEL"
        embedding_model = _get_env(env, model_key, default_model)
        embedding_dimensions_raw = _get_env(
            env,
            (
                "GOOGLE_EMBEDDING_DIMENSIONS"
                if embedding_provider == "google"
                else "OPENAI_EMBEDDING_DIMENSIONS"
            ),
        )
        embedding_dimensions = None
        if embedding_dimensions_raw:
            try:
                embedding_dimensions = int(embedding_dimensions_raw)
            except ValueError:
                embedding_dimensions = None

        similarity_top_k = int(
            _get_env(
                env, "RAG_SIMILARITY_TOP_K", str(app_settings.rag.similarity_top_k)
            )
            or app_settings.rag.similarity_top_k
        )
        max_context_chars = int(
            _get_env(
                env,
                "RAG_MAX_CONTEXT_LENGTH_CHARS",
                str(app_settings.rag.max_context_length_chars),
            )
            or app_settings.rag.max_context_length_chars
        )
        overlay_mode = (
            _get_env(env, "RAG_OVERLAY_MODE", "collection") or "collection"
        ).lower()
        overlay_ttl_hours = int(_get_env(env, "RAG_OVERLAY_TTL_HOURS", "24") or 24)
        overlay_chunk_chars = int(
            _get_env(env, "RAG_OVERLAY_CHARS_PER_CHUNK", "1200") or 1200
        )
        overlay_chunk_overlap = int(
            _get_env(env, "RAG_OVERLAY_CHUNK_OVERLAP", "120") or 120
        )
        retrieval_gateway_url = _get_env(env, "MOONMIND_RETRIEVAL_URL") or None
        statsd_host = _get_env(env, "STATSD_HOST") or None
        statsd_port_raw = _get_env(env, "STATSD_PORT")
        statsd_port = int(statsd_port_raw) if statsd_port_raw else None
        job_id = _get_env(env, "JOB_ID") or _get_env(env, "MOONMIND_JOB_ID")
        run_id = _get_env(env, "RUN_ID") or _get_env(env, "MOONMIND_RUN_ID")
        rag_enabled = env_to_bool(_get_env(env, "RAG_ENABLED", "true"), default=True)
        qdrant_enabled = env_to_bool(
            _get_env(env, "QDRANT_ENABLED", "true"), default=True
        )

        return cls(
            qdrant_url=qdrant_url,
            qdrant_host=qdrant_host,
            qdrant_port=qdrant_port,
            qdrant_api_key=qdrant_api_key,
            vector_collection=vector_collection,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            similarity_top_k=similarity_top_k,
            max_context_chars=max_context_chars,
            overlay_mode=overlay_mode,
            overlay_ttl_hours=overlay_ttl_hours,
            overlay_chunk_chars=overlay_chunk_chars,
            overlay_chunk_overlap=overlay_chunk_overlap,
            retrieval_gateway_url=retrieval_gateway_url,
            statsd_host=statsd_host,
            statsd_port=statsd_port,
            job_id=job_id,
            run_id=run_id,
            rag_enabled=rag_enabled,
            qdrant_enabled=qdrant_enabled,
        )

    def resolved_transport(self, preferred: Optional[str]) -> str:
        if preferred in {"direct", "gateway"}:
            return preferred
        if self.retrieval_gateway_url:
            return "gateway"
        return "direct"

    def overlay_collection_name(self, run_id: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", run_id).strip("-") or "overlay"
        return f"{self.vector_collection}__overlay__{sanitized}"[:128]

    def as_filter_metadata(self) -> MutableMapping[str, str]:
        data: MutableMapping[str, str] = {}
        if self.job_id:
            data["job_id"] = self.job_id
        if self.run_id:
            data["run_id"] = self.run_id
        return data
