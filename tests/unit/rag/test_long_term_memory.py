"""Unit tests for Mem0-backed long-term memory integration (MM-764)."""

from __future__ import annotations

import pytest

from moonmind.rag.long_term_memory import LongTermMemoryError, LongTermMemoryService
from moonmind.rag.settings import RagRuntimeSettings


class _Mem0Client:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, dict[str, object]]] = []
        self.add_calls: list[tuple[str, dict[str, object]]] = []
        self.update_calls: list[tuple[str, dict[str, object]]] = []

    def search(self, query: str, **kwargs):
        self.search_calls.append((query, kwargs))
        return {
            "results": [
                {
                    "id": "memory-1",
                    "memory": "Prefer approved memories during retrieval.",
                    "score": 0.87,
                    "metadata": {
                        "review_state": "approved",
                        "repo": "MoonLadderStudios/MoonMind",
                    },
                },
                {"memory": ""},
            ]
        }

    def add(self, memory: str, **kwargs):
        self.add_calls.append((memory, kwargs))
        return {"id": "memory-2"}

    def update(self, memory_id: str, **kwargs):
        self.update_calls.append((memory_id, kwargs))
        return {"id": memory_id}


def _settings(**overrides: object) -> RagRuntimeSettings:
    defaults = dict(
        qdrant_url=None,
        qdrant_host="localhost",
        qdrant_port=6333,
        qdrant_api_key=None,
        vector_collection="test_collection",
        embedding_provider="google",
        embedding_model="test-model",
        embedding_dimensions=768,
        similarity_top_k=5,
        max_context_chars=8000,
        overlay_mode="collection",
        overlay_ttl_hours=24,
        overlay_chunk_chars=1200,
        overlay_chunk_overlap=120,
        retrieval_gateway_url=None,
        statsd_host=None,
        statsd_port=None,
        job_id="job-1",
        run_id="run-1",
        rag_enabled=True,
        qdrant_enabled=True,
        memory_enabled=True,
        memory_long_term="mem0",
        memory_fail_open=True,
        memory_context_budget_tokens=None,
        memory_namespace_id="tenant-a",
        mem0_api_key="mem0-secret",
        mem0_user_id=None,
    )
    defaults.update(overrides)
    return RagRuntimeSettings(**defaults)


def test_search_filters_to_approved_repo_scoped_memories() -> None:
    client = _Mem0Client()
    service = LongTermMemoryService(settings=_settings(), client=client)

    items, latency_ms = service.search(
        query="memory practices",
        repo="MoonLadderStudios/MoonMind",
        limit=2,
    )

    assert latency_ms >= 0
    assert len(items) == 1
    assert items[0].source == "mem0:memory-1"
    assert items[0].trust_class == "approved"
    assert items[0].payload["record_kind"] == "long_term_memory"
    assert client.search_calls == [
        (
            "memory practices",
            {
                "user_id": "tenant-a:project:MoonLadderStudios:MoonMind",
                "metadata": {
                    "namespace_id": "tenant-a",
                    "scope": "project",
                    "review_state": "approved",
                    "repo": "MoonLadderStudios/MoonMind",
                },
                "limit": 2,
            },
        )
    ]


def test_add_or_update_requires_provenance_metadata() -> None:
    service = LongTermMemoryService(settings=_settings(), client=_Mem0Client())

    with pytest.raises(LongTermMemoryError, match="provenance is required"):
        service.add_or_update(
            text="Stable convention.",
            repo="MoonLadderStudios/MoonMind",
            provenance={},
        )


def test_add_or_update_sends_required_mem0_metadata() -> None:
    client = _Mem0Client()
    service = LongTermMemoryService(settings=_settings(), client=client)

    result = service.add_or_update(
        text="Stable convention.",
        repo="MoonLadderStudios/MoonMind",
        scope="team",
        review_state="draft",
        provenance={"workflowId": "wf-1", "taskRunId": "run-1"},
    )

    assert result == {"id": "memory-2"}
    assert client.add_calls == [
        (
            "Stable convention.",
            {
                "user_id": "tenant-a:team:MoonLadderStudios:MoonMind",
                "metadata": {
                    "namespace_id": "tenant-a",
                    "repo": "MoonLadderStudios/MoonMind",
                    "scope": "team",
                    "review_state": "draft",
                    "provenance": {"workflowId": "wf-1", "taskRunId": "run-1"},
                },
            },
        )
    ]
