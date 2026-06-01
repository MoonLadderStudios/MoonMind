from pathlib import Path

from moonmind.manifest.incremental import (
    IndexState,
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
    assert loaded.sources["local"].documents == {"a.txt": documents[0].content_hash}
