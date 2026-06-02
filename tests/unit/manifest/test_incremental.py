import uuid
from pathlib import Path

from moonmind.manifest.incremental import (
    IndexState,
    QdrantIncrementalIndexWriter,
    build_changeset,
    source_documents,
)
from moonmind.schemas.manifest_v0_models import SplitterConfig


def test_changeset_detects_changed_and_deleted_documents() -> None:
    splitter = SplitterConfig(chunkSize=20, chunkOverlap=0)
    first_documents = source_documents(
        source_id="local",
        raw_documents=[
            ("alpha", {"file_path": "a.txt"}),
            ("beta", {"file_path": "b.txt"}),
        ],
    )
    first = build_changeset(
        source_id="local",
        cursor={"run": 1},
        documents=first_documents,
        previous=None,
        splitter=splitter,
    )

    second_documents = source_documents(
        source_id="local",
        raw_documents=[
            ("alpha changed", {"file_path": "a.txt"}),
        ],
    )
    second = build_changeset(
        source_id="local",
        cursor={"run": 2},
        documents=second_documents,
        previous=first.next_snapshot,
        splitter=splitter,
    )

    assert [doc.document_id for doc in second.changed_documents] == ["a.txt"]
    assert second.deleted_document_ids == ["b.txt"]
    assert second.stale_point_ids == [
        *first.next_snapshot.document_chunks["a.txt"],
        *first.next_snapshot.document_chunks["b.txt"],
    ]
    assert len(second.chunks) == 1
    assert second.next_snapshot.documents.keys() == {"a.txt"}


def test_index_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    documents = source_documents(
        source_id="local",
        raw_documents=[("alpha", {"file_path": "a.txt"})],
    )
    changeset = build_changeset(
        source_id="local",
        cursor={"files": ["a.txt"]},
        documents=documents,
        previous=None,
        splitter=None,
    )
    state = IndexState(manifest_name="m", index_name="idx")
    state.sources["local"] = changeset.next_snapshot
    state.save(path)

    loaded = IndexState.load(path, manifest_name="m", index_name="idx")

    assert loaded.manifest_name == "m"
    assert loaded.index_name == "idx"
    assert loaded.sources["local"].documents == state.sources["local"].documents


def test_chunk_point_ids_are_valid_deterministic_uuids() -> None:
    splitter = SplitterConfig(chunkSize=5, chunkOverlap=0)
    document = source_documents(
        source_id="local",
        raw_documents=[("alpha beta", {"file_path": "a.txt"})],
    )[0]

    first = build_changeset(
        source_id="local",
        cursor={"run": 1},
        documents=[document],
        previous=None,
        splitter=splitter,
    )
    second = build_changeset(
        source_id="local",
        cursor={"run": 1},
        documents=[document],
        previous=None,
        splitter=splitter,
    )

    assert [chunk.point_id for chunk in first.chunks] == [
        chunk.point_id for chunk in second.chunks
    ]
    for chunk in first.chunks:
        assert str(uuid.UUID(chunk.point_id)) == chunk.point_id


def test_splitter_change_invalidates_existing_chunks() -> None:
    documents = source_documents(
        source_id="local",
        raw_documents=[("alpha beta gamma", {"file_path": "a.txt"})],
    )
    first = build_changeset(
        source_id="local",
        cursor={"run": 1},
        documents=documents,
        previous=None,
        splitter=SplitterConfig(chunkSize=8, chunkOverlap=0),
    )

    second = build_changeset(
        source_id="local",
        cursor={"run": 2},
        documents=documents,
        previous=first.next_snapshot,
        splitter=SplitterConfig(chunkSize=5, chunkOverlap=0),
    )

    assert [doc.document_id for doc in second.changed_documents] == ["a.txt"]
    assert second.stale_point_ids == first.next_snapshot.document_chunks["a.txt"]
    assert second.chunks


class _RecordingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [float(len(text))]


class _RecordingQdrant:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []

    def upsert_canonical_vectors(self, **kwargs: object) -> None:
        self.upserts.append(kwargs)


def test_qdrant_writer_batches_upserts() -> None:
    documents = source_documents(
        source_id="local",
        raw_documents=[("alpha beta gamma delta epsilon zeta", {"file_path": "a.txt"})],
    )
    changeset = build_changeset(
        source_id="local",
        cursor={"run": 1},
        documents=documents,
        previous=None,
        splitter=SplitterConfig(chunkSize=5, chunkOverlap=0),
    )
    qdrant = _RecordingQdrant()
    embedder = _RecordingEmbedder()
    writer = QdrantIncrementalIndexWriter(
        qdrant=qdrant,
        embedder=embedder,
        collection_name="docs",
    )

    writer.upsert_chunks(changeset.chunks, batch_size=2)

    assert len(qdrant.upserts) > 1
    assert all(len(upsert["ids"]) <= 2 for upsert in qdrant.upserts)
    assert len(embedder.texts) == len(changeset.chunks)
