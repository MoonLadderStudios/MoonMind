"""Overlay indexing utilities for in-progress worker edits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, MutableMapping, Sequence

from moonmind.rag.embedding import EmbeddingClient
from moonmind.rag.qdrant_client import RagQdrantClient
from moonmind.rag.settings import RagRuntimeSettings


@dataclass(slots=True)
class OverlayChunk:
    path: Path
    offset_start: int
    offset_end: int
    text: str

    @property
    def chunk_hash(self) -> str:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"


def chunk_file(path: Path, *, chunk_chars: int, overlap: int) -> Iterable[OverlayChunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text:
        return []
    end = len(text)
    cursor = 0
    results: list[OverlayChunk] = []
    while cursor < end:
        chunk_end = min(end, cursor + chunk_chars)
        chunk = OverlayChunk(
            path=path,
            offset_start=cursor,
            offset_end=chunk_end,
            text=text[cursor:chunk_end],
        )
        results.append(chunk)
        if chunk_end >= end:
            break
        cursor = max(chunk_end - overlap, cursor + 1)
    return results


def upsert_overlay_files(
    *,
    files: Sequence[Path],
    run_id: str,
    settings: RagRuntimeSettings,
    embedder: EmbeddingClient,
    qdrant: RagQdrantClient,
) -> int:
    collection_name = settings.overlay_collection_name(run_id)
    qdrant.ensure_overlay_collection(collection_name)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=settings.overlay_ttl_hours)
    ).isoformat()
    total_chunks = 0
    for file_path in files:
        chunks = list(
            chunk_file(
                file_path,
                chunk_chars=settings.overlay_chunk_chars,
                overlap=settings.overlay_chunk_overlap,
            )
        )
        if not chunks:
            continue
        payloads: List[MutableMapping[str, object]] = []
        vectors: List[List[float]] = []
        for chunk in chunks:
            vector = embedder.embed(chunk.text)
            vectors.append(vector)
            payloads.append(
                {
                    "path": str(chunk.path),
                    "offset_start": chunk.offset_start,
                    "offset_end": chunk.offset_end,
                    "chunk_hash": chunk.chunk_hash,
                    "trust_class": "workspace_overlay",
                    "run_id": run_id,
                    "expires_at": expires_at,
                    "text": chunk.text,
                }
            )
        qdrant.upsert_overlay_vectors(
            collection_name=collection_name,
            vectors=vectors,
            payloads=payloads,
        )
        total_chunks += len(chunks)
    return total_chunks
