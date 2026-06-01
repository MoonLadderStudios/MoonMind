"""Incremental manifest indexing helpers for RAG document refreshes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Protocol, Sequence

from moonmind.schemas.manifest_v0_models import SplitterConfig


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "default"


@dataclass(slots=True)
class SourceDocument:
    source_id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return _sha256_text(self.text)


@dataclass(slots=True)
class IndexedChunk:
    point_id: str
    source_id: str
    document_id: str
    chunk_hash: str
    offset_start: int
    offset_end: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> MutableMapping[str, Any]:
        payload: MutableMapping[str, Any] = dict(self.metadata)
        payload.update(
            {
                "source_id": self.source_id,
                "document_id": self.document_id,
                "offset_start": self.offset_start,
                "offset_end": self.offset_end,
                "chunk_hash": self.chunk_hash,
                "trust_class": "canonical",
                "text": self.text,
            }
        )
        return payload


@dataclass(slots=True)
class SourceSnapshot:
    source_id: str
    state_hash: str
    cursor: dict[str, Any]
    documents: dict[str, str] = field(default_factory=dict)
    document_chunks: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, source_id: str, raw: Mapping[str, Any]) -> "SourceSnapshot":
        return cls(
            source_id=source_id,
            state_hash=str(raw.get("state_hash") or ""),
            cursor=dict(raw.get("cursor") or {}),
            documents={
                str(key): str(value)
                for key, value in dict(raw.get("documents") or {}).items()
            },
            document_chunks={
                str(key): [str(item) for item in value]
                for key, value in dict(raw.get("document_chunks") or {}).items()
                if isinstance(value, list)
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_hash": self.state_hash,
            "cursor": self.cursor,
            "documents": dict(sorted(self.documents.items())),
            "document_chunks": {
                key: list(value)
                for key, value in sorted(self.document_chunks.items())
            },
        }


@dataclass(slots=True)
class IndexState:
    manifest_name: str
    index_name: str
    sources: dict[str, SourceSnapshot] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        manifest_name: str,
        index_name: str,
    ) -> "IndexState":
        if not path.exists():
            return cls(manifest_name=manifest_name, index_name=index_name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        sources = {
            source_id: SourceSnapshot.from_dict(source_id, source_raw)
            for source_id, source_raw in dict(raw.get("sources") or {}).items()
        }
        return cls(
            manifest_name=str(raw.get("manifest_name") or manifest_name),
            index_name=str(raw.get("index_name") or index_name),
            sources=sources,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "manifest_name": self.manifest_name,
            "index_name": self.index_name,
            "sources": {
                source_id: snapshot.to_dict()
                for source_id, snapshot in sorted(self.sources.items())
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(slots=True)
class IncrementalChangeSet:
    source_id: str
    state_hash: str
    changed_documents: list[SourceDocument]
    unchanged_document_ids: list[str]
    deleted_document_ids: list[str]
    stale_point_ids: list[str]
    chunks: list[IndexedChunk]
    next_snapshot: SourceSnapshot

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_documents or self.deleted_document_ids)


class IncrementalIndexWriter(Protocol):
    def delete_points(self, point_ids: Sequence[str]) -> None:
        ...

    def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None:
        ...


def default_state_path(*, manifest_name: str, index_name: str) -> Path:
    return (
        Path(".moonmind")
        / "manifest_index_state"
        / f"{_safe_segment(manifest_name)}__{_safe_segment(index_name)}.json"
    )


def state_hash(cursor: Mapping[str, Any]) -> str:
    return _sha256_text(_stable_json(cursor))


def document_id_for(
    *,
    source_id: str,
    text: str,
    metadata: Mapping[str, Any],
    ordinal: int,
) -> str:
    for key in (
        "document_id",
        "doc_id",
        "id",
        "file_path",
        "path",
        "source",
        "url",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{source_id}:{ordinal}:{_sha256_text(text)}"


def source_documents(
    *,
    source_id: str,
    raw_documents: Sequence[tuple[str, Mapping[str, Any]]],
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for ordinal, (text, metadata) in enumerate(raw_documents):
        normalized_metadata = dict(metadata)
        documents.append(
            SourceDocument(
                source_id=source_id,
                document_id=document_id_for(
                    source_id=source_id,
                    text=text,
                    metadata=normalized_metadata,
                    ordinal=ordinal,
                ),
                text=text,
                metadata=normalized_metadata,
            )
        )
    return documents


def chunk_document(
    document: SourceDocument,
    *,
    splitter: SplitterConfig | None,
) -> list[IndexedChunk]:
    chunk_size = splitter.chunkSize if splitter else 1000
    overlap = splitter.chunkOverlap if splitter else 100
    if overlap >= chunk_size:
        overlap = max(0, chunk_size - 1)
    if not document.text:
        return []

    chunks: list[IndexedChunk] = []
    cursor = 0
    end = len(document.text)
    while cursor < end:
        chunk_end = min(end, cursor + chunk_size)
        text = document.text[cursor:chunk_end]
        chunk_hash = _sha256_text(text)
        point_seed = "|".join(
            [
                document.source_id,
                document.document_id,
                str(cursor),
                str(chunk_end),
                chunk_hash,
            ]
        )
        chunks.append(
            IndexedChunk(
                point_id=_sha256_text(point_seed),
                source_id=document.source_id,
                document_id=document.document_id,
                chunk_hash=chunk_hash,
                offset_start=cursor,
                offset_end=chunk_end,
                text=text,
                metadata=document.metadata,
            )
        )
        if chunk_end >= end:
            break
        cursor = max(chunk_end - overlap, cursor + 1)
    return chunks


def build_changeset(
    *,
    source_id: str,
    cursor: Mapping[str, Any],
    documents: Sequence[SourceDocument],
    previous: SourceSnapshot | None,
    splitter: SplitterConfig | None,
) -> IncrementalChangeSet:
    current_hash = state_hash(cursor)
    previous_documents = previous.documents if previous else {}
    current_documents = {document.document_id: document for document in documents}
    current_hashes = {
        document_id: document.content_hash
        for document_id, document in current_documents.items()
    }

    changed_documents = [
        document
        for document_id, document in current_documents.items()
        if previous_documents.get(document_id) != document.content_hash
    ]
    unchanged_document_ids = sorted(
        document_id
        for document_id in current_documents
        if previous_documents.get(document_id) == current_hashes[document_id]
    )
    deleted_document_ids = sorted(
        document_id
        for document_id in previous_documents
        if document_id not in current_documents
    )

    stale_point_ids: list[str] = []
    if previous:
        for document_id in deleted_document_ids:
            stale_point_ids.extend(previous.document_chunks.get(document_id, []))
        for document in changed_documents:
            stale_point_ids.extend(previous.document_chunks.get(document.document_id, []))

    new_chunks_by_document: dict[str, list[str]] = {}
    chunks: list[IndexedChunk] = []
    for document in changed_documents:
        document_chunks = chunk_document(document, splitter=splitter)
        chunks.extend(document_chunks)
        new_chunks_by_document[document.document_id] = [
            chunk.point_id for chunk in document_chunks
        ]

    next_document_chunks: dict[str, list[str]] = {}
    if previous:
        for document_id in unchanged_document_ids:
            next_document_chunks[document_id] = list(
                previous.document_chunks.get(document_id, [])
            )
    next_document_chunks.update(new_chunks_by_document)

    return IncrementalChangeSet(
        source_id=source_id,
        state_hash=current_hash,
        changed_documents=changed_documents,
        unchanged_document_ids=unchanged_document_ids,
        deleted_document_ids=deleted_document_ids,
        stale_point_ids=sorted(dict.fromkeys(stale_point_ids)),
        chunks=chunks,
        next_snapshot=SourceSnapshot(
            source_id=source_id,
            state_hash=current_hash,
            cursor=dict(cursor),
            documents=current_hashes,
            document_chunks=next_document_chunks,
        ),
    )


class QdrantIncrementalIndexWriter:
    def __init__(self, *, qdrant: Any, embedder: Any, collection_name: str) -> None:
        self._qdrant = qdrant
        self._embedder = embedder
        self._collection_name = collection_name

    def delete_points(self, point_ids: Sequence[str]) -> None:
        if not point_ids:
            return
        self._qdrant.delete_vectors(
            collection_name=self._collection_name,
            point_ids=list(point_ids),
        )

    def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None:
        if not chunks:
            return
        vectors = [self._embedder.embed(chunk.text) for chunk in chunks]
        self._qdrant.upsert_canonical_vectors(
            collection_name=self._collection_name,
            ids=[chunk.point_id for chunk in chunks],
            vectors=vectors,
            payloads=[chunk.payload() for chunk in chunks],
        )
