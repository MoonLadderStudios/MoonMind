import threading
from types import SimpleNamespace

from qdrant_client.http import models as qmodels

from moonmind.rag.qdrant_client import RagQdrantClient

def _point(
    *,
    score: float,
    path: str,
    chunk_hash: str,
    text: str,
    expires_at: str | None = None,
) -> qmodels.ScoredPoint:
    payload = {
        "path": path,
        "chunk_hash": chunk_hash,
        "text": text,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return qmodels.ScoredPoint(
        id=f"{path}:{chunk_hash}:{score}",
        version=1,
        score=score,
        payload=payload,
        vector=None,
    )

def _client() -> RagQdrantClient:
    client = RagQdrantClient.__new__(RagQdrantClient)
    client.collection = "repo-main"
    client.overlay_mode = "collection"
    client.overlay_ttl_hours = 24
    client.overlay_chunk_chars = 1200
    client.overlay_chunk_overlap = 120
    client._embedding_dimensions = None  # type: ignore[attr-defined]
    client._client = None  # type: ignore[attr-defined]
    return client

def test_search_caps_results_to_top_k_after_overlay_merge():
    client = _client()

    class FakeQdrant:
        def search(self, *, collection_name, **kwargs):
            _ = kwargs
            if collection_name == "repo-main__overlay__run":
                return [
                    _point(
                        score=0.99,
                        path="src/overlay_a.py",
                        chunk_hash="ov-a",
                        text="overlay a",
                    ),
                    _point(
                        score=0.98,
                        path="src/overlay_b.py",
                        chunk_hash="ov-b",
                        text="overlay b",
                    ),
                ]
            if collection_name == "repo-main":
                return [
                    _point(
                        score=0.97,
                        path="src/canon_a.py",
                        chunk_hash="ca-a",
                        text="canon a",
                    ),
                    _point(
                        score=0.96,
                        path="src/canon_b.py",
                        chunk_hash="ca-b",
                        text="canon b",
                    ),
                ]
            raise AssertionError("unexpected collection")

    client._client = FakeQdrant()  # type: ignore[assignment]
    result = client.search(
        query_vector=[0.1, 0.2],
        filters={"repo": "moonmind"},
        top_k=2,
        overlay_policy="include",
        overlay_collection="repo-main__overlay__run",
        trust_overrides=None,
    )

    assert len(result.items) == 2
    assert result.items[0].source == "src/overlay_a.py"
    assert result.items[1].source == "src/overlay_b.py"

def test_search_uses_query_points_when_search_api_is_unavailable():
    client = _client()
    calls: list[str] = []

    class QueryResponse:
        def __init__(self, points):
            self.points = points

    class FakeQdrant:
        def query_points(self, *, collection_name, **kwargs):
            _ = kwargs
            calls.append(collection_name)
            if collection_name == "repo-main__overlay__run":
                return QueryResponse(
                    [
                        _point(
                            score=0.99,
                            path="src/overlay_a.py",
                            chunk_hash="ov-a",
                            text="overlay a",
                        )
                    ]
                )
            if collection_name == "repo-main":
                return QueryResponse(
                    [
                        _point(
                            score=0.80,
                            path="src/canon_a.py",
                            chunk_hash="ca-a",
                            text="canon a",
                        )
                    ]
                )
            raise AssertionError("unexpected collection")

    client._client = FakeQdrant()  # type: ignore[assignment]
    result = client.search(
        query_vector=[0.1, 0.2],
        filters={"repo": "moonmind"},
        top_k=2,
        overlay_policy="include",
        overlay_collection="repo-main__overlay__run",
        trust_overrides=None,
    )

    assert calls == ["repo-main", "repo-main__overlay__run"]
    assert [item.source for item in result.items] == [
        "src/overlay_a.py",
        "src/canon_a.py",
    ]

def test_search_federates_multiple_canonical_collections_by_score():
    client = _client()
    calls: list[str] = []

    class FakeQdrant:
        def search(self, *, collection_name, **kwargs):
            _ = kwargs
            calls.append(collection_name)
            if collection_name == "repo-main":
                return [
                    _point(
                        score=0.72,
                        path="src/repo.py",
                        chunk_hash="repo",
                        text="repo result",
                    )
                ]
            if collection_name == "docs-main":
                return [
                    _point(
                        score=0.95,
                        path="docs/rag.md",
                        chunk_hash="docs",
                        text="docs result",
                    )
                ]
            raise AssertionError("unexpected collection")

    client._client = FakeQdrant()  # type: ignore[assignment]
    result = client.search(
        query_vector=[0.1, 0.2],
        filters={"repo": "moonmind"},
        top_k=2,
        collections=["repo-main", "docs-main"],
        overlay_policy="skip",
        overlay_collection=None,
        trust_overrides=None,
    )

    assert set(calls) == {"repo-main", "docs-main"}
    assert [item.source for item in result.items] == ["docs/rag.md", "src/repo.py"]
    assert [item.payload["collection"] for item in result.items] == [
        "docs-main",
        "repo-main",
    ]

def test_search_runs_multiple_canonical_collections_concurrently():
    client = _client()
    started: list[str] = []
    started_lock = threading.Lock()
    both_started = threading.Event()

    def search_collection_points(*, collection_name, **kwargs):
        _ = kwargs
        with started_lock:
            started.append(collection_name)
            if len(started) == 2:
                both_started.set()
        assert both_started.wait(timeout=1.0)
        return [
            _point(
                score=0.8,
                path=f"src/{collection_name}.py",
                chunk_hash=collection_name,
                text=collection_name,
            )
        ]

    client._search_collection_points = search_collection_points  # type: ignore[method-assign]

    result = client.search(
        query_vector=[0.1, 0.2],
        filters={"repo": "moonmind"},
        top_k=2,
        collections=["repo-main", "docs-main"],
        overlay_policy="skip",
        overlay_collection=None,
        trust_overrides=None,
    )

    assert set(started) == {"repo-main", "docs-main"}
    assert len(result.items) == 2

def test_collection_health_reports_counts_and_dimensions():
    client = _client()

    class _Vectors:
        size = 768

    class _Params:
        vectors = _Vectors()

    class _Config:
        params = _Params()

    class _Info:
        status = "green"
        points_count = 12
        vectors_count = 12
        indexed_vectors_count = 10
        config = _Config()

    class FakeQdrant:
        def get_collection(self, name):
            assert name == "repo-main"
            return _Info()

    client._client = FakeQdrant()  # type: ignore[assignment]

    result = client.collection_health(collection_names=("repo-main",))

    assert result[0].name == "repo-main"
    assert result[0].status == "green"
    assert result[0].points_count == 12
    assert result[0].indexed_vectors_count == 10
    assert result[0].dimensions == 768
    assert result[0].freshness == "ready"

def test_collection_health_reports_transport_exceptions_as_unavailable():
    client = _client()

    class FakeQdrant:
        def get_collection(self, name):
            raise TimeoutError(f"{name} timed out")

    client._client = FakeQdrant()  # type: ignore[assignment]

    result = client.collection_health(collection_names=("repo-main",))

    assert result[0].name == "repo-main"
    assert result[0].status == "unavailable"
    assert "timed out" in result[0].error

def test_merge_results_skips_expired_overlay_chunks():
    client = _client()
    expired_overlay = _point(
        score=0.99,
        path="src/file.py",
        chunk_hash="same",
        text="overlay",
        expires_at="2000-01-01T00:00:00Z",
    )
    canonical = _point(
        score=0.80,
        path="src/file.py",
        chunk_hash="same",
        text="canonical",
    )

    items = client._merge_results([expired_overlay], [canonical], trust_overrides=None)

    assert len(items) == 1
    assert items[0].trust_class == "canonical"
    assert items[0].text == "canonical"

def test_merge_results_keeps_multiple_chunks_when_hash_missing():
    client = _client()
    first = qmodels.ScoredPoint(
        id="pt-1",
        version=1,
        score=0.7,
        payload={
            "path": "src/file.py",
            "text": "one",
            "offset_start": 0,
            "offset_end": 10,
        },
        vector=None,
    )
    second = qmodels.ScoredPoint(
        id="pt-2",
        version=1,
        score=0.8,
        payload={
            "path": "src/file.py",
            "text": "two",
            "offset_start": 11,
            "offset_end": 20,
        },
        vector=None,
    )

    items = client._merge_results([], [first, second], trust_overrides=None)

    assert len(items) == 2
    assert {item.text for item in items} == {"one", "two"}


def test_upsert_memory_vectors_targets_canonical_collection_with_supplied_ids():
    client = _client()
    calls: list[dict[str, object]] = []

    class FakeQdrant:
        def upsert(self, *, collection_name, points):
            calls.append({"collection_name": collection_name, "points": points})

    client._client = FakeQdrant()  # type: ignore[assignment]

    client.upsert_memory_vectors(
        vectors=[[0.1, 0.2]],
        payloads=[{"record_kind": "run_digest", "text": "digest"}],
        ids=["1f0d3e0c-d534-54f5-9a4f-15b5bd3e61c4"],
    )

    assert calls[0]["collection_name"] == "repo-main"
    batch = calls[0]["points"]
    assert batch.ids == ["1f0d3e0c-d534-54f5-9a4f-15b5bd3e61c4"]
    assert batch.payloads == [{"record_kind": "run_digest", "text": "digest"}]


def test_upsert_memory_vectors_rejects_mismatched_batches():
    client = _client()

    try:
        client.upsert_memory_vectors(
            vectors=[[0.1, 0.2]],
            payloads=[],
            ids=["1f0d3e0c-d534-54f5-9a4f-15b5bd3e61c4"],
        )
    except ValueError as exc:
        assert "matching lengths" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_index_health_lists_collections_counts_and_freshness():
    client = _client()

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(
                collections=[
                    SimpleNamespace(name="repo-b"),
                    SimpleNamespace(name="repo-a"),
                ]
            )

        def get_collection(self, collection_name):
            counts = {
                "repo-a": (3, 2, 1, "green"),
                "repo-b": (0, 0, 0, "yellow"),
            }
            points_count, indexed_count, segments_count, status = counts[collection_name]
            return SimpleNamespace(
                status=status,
                points_count=points_count,
                indexed_vectors_count=indexed_count,
                segments_count=segments_count,
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors=SimpleNamespace(size=768, distance="Cosine")
                    )
                ),
            )

        def scroll(self, *, collection_name, **kwargs):
            _ = kwargs
            if collection_name == "repo-a":
                return (
                    [
                        SimpleNamespace(payload={"indexed_at": "2026-05-01T10:00:00Z"}),
                        SimpleNamespace(
                            payload={
                                "metadata": {
                                    "source_updated_at": "2026-05-02T12:30:00+00:00"
                                }
                            }
                        ),
                    ],
                    None,
                )
            return ([], None)

    client._client = FakeQdrant()  # type: ignore[assignment]

    result = client.index_health()

    assert result.total_collections == 2
    assert result.total_points == 3
    assert [collection.name for collection in result.collections] == ["repo-a", "repo-b"]
    first = result.collections[0]
    assert first.points_count == 3
    assert first.indexed_vectors_count == 2
    assert first.segments_count == 1
    assert first.vector_size == 768
    assert first.vector_distance == "Cosine"
    assert first.freshness_status == "known"
    assert first.freshness_at == "2026-05-02T12:30:00+00:00"
    assert first.freshness_source == "metadata.source_updated_at"
    assert result.collections[1].freshness_status == "empty"

def test_index_health_reports_unknown_freshness_when_payload_has_no_timestamp():
    client = _client()

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name="repo-main")])

        def get_collection(self, collection_name):
            _ = collection_name
            return SimpleNamespace(
                status="green",
                points_count=1,
                indexed_vectors_count=1,
                segments_count=1,
                config=SimpleNamespace(params=SimpleNamespace(vectors=None)),
            )

        def scroll(self, *, collection_name, **kwargs):
            _ = collection_name, kwargs
            return ([SimpleNamespace(payload={"path": "src/a.py"})], None)

    client._client = FakeQdrant()  # type: ignore[assignment]

    result = client.index_health()

    assert result.collections[0].freshness_status == "unknown"
    assert result.collections[0].freshness_at is None
    assert result.collections[0].freshness_source is None


def test_index_health_keeps_healthy_collections_when_one_collection_fails():
    client = _client()
    scroll_calls: list[str] = []

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(
                collections=[
                    SimpleNamespace(name="broken"),
                    SimpleNamespace(name="empty"),
                    SimpleNamespace(name="healthy"),
                ]
            )

        def get_collection(self, collection_name):
            if collection_name == "broken":
                raise RuntimeError("collection disappeared")
            points_count = 0 if collection_name == "empty" else 1
            return SimpleNamespace(
                status="green",
                points_count=points_count,
                indexed_vectors_count=points_count,
                segments_count=1,
                config=SimpleNamespace(params=SimpleNamespace(vectors=None)),
            )

        def scroll(self, *, collection_name, **kwargs):
            _ = kwargs
            scroll_calls.append(collection_name)
            return (
                [SimpleNamespace(payload={"indexed_at": "2026-05-03T00:00:00Z"})],
                None,
            )

    client._client = FakeQdrant()  # type: ignore[assignment]

    result = client.index_health()

    assert [collection.name for collection in result.collections] == [
        "broken",
        "empty",
        "healthy",
    ]
    assert result.collections[0].status == "unavailable"
    assert result.collections[0].freshness_status == "unknown"
    assert result.collections[1].freshness_status == "empty"
    assert result.collections[2].freshness_status == "known"
    assert scroll_calls == ["healthy"]


def test_collection_freshness_pages_until_latest_timestamp_is_found():
    client = _client()
    offsets: list[object | None] = []

    class FakeQdrant:
        def scroll(self, *, collection_name, **kwargs):
            _ = collection_name
            offsets.append(kwargs.get("offset"))
            if kwargs.get("offset") is None:
                return (
                    [SimpleNamespace(payload={"indexed_at": "2026-05-01T00:00:00Z"})],
                    "next-page",
                )
            return (
                [SimpleNamespace(payload={"indexed_at": "2026-05-04T00:00:00Z"})],
                None,
            )

    client._client = FakeQdrant()  # type: ignore[assignment]

    freshness_at, freshness_source = client._collection_freshness(
        collection_name="repo-main",
        limit=1,
    )

    assert offsets == [None, "next-page"]
    assert freshness_at == "2026-05-04T00:00:00+00:00"
    assert freshness_source == "indexed_at"


def test_canonical_vectors_use_supplied_ids_and_delete_by_point_ids():
    client = _client()
    calls = []

    class FakeQdrant:
        def upsert(self, *, collection_name, points):
            calls.append(("upsert", collection_name, points.ids, points.vectors))

        def delete(self, *, collection_name, points_selector):
            calls.append(("delete", collection_name, points_selector.points))

    client._client = FakeQdrant()  # type: ignore[assignment]

    client.upsert_canonical_vectors(
        collection_name="repo-main",
        ids=["point-a"],
        vectors=[[0.1, 0.2]],
        payloads=[{"path": "a.txt"}],
    )
    client.delete_vectors(collection_name="repo-main", point_ids=["point-old"])

    assert calls == [
        ("upsert", "repo-main", ["point-a"], [[0.1, 0.2]]),
        ("delete", "repo-main", ["point-old"]),
    ]
