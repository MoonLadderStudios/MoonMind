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
