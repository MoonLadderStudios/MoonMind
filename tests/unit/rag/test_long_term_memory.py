"""Unit tests for the Mem0 long-term memory adapter."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.rag.long_term_memory import LongTermMemoryError, LongTermMemoryService
from moonmind.rag.settings import RagRuntimeSettings


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


class _Mem0Stub:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append({"query": query, **kwargs})
        return {
            "results": [
                {
                    "memory": "Approved memory",
                    "id": "memory-1",
                    "score": None,
                    "similarity": "0.72",
                    "metadata": {"review_state": "approved"},
                }
            ]
        }

    def add(self, memory: str, **kwargs: Any) -> dict[str, str]:
        self.add_calls.append({"memory": memory, **kwargs})
        return {"id": "memory-1"}

    def update(self, memory_id: str, **kwargs: Any) -> dict[str, str]:
        self.update_calls.append({"memory_id": memory_id, **kwargs})
        return {"id": memory_id}


def test_search_uses_filters_and_preserves_system_metadata() -> None:
    client = _Mem0Stub()
    service = LongTermMemoryService(settings=_settings(), client=client)

    items, _latency_ms = service.search(
        query="How should RAG work?",
        repo="MoonLadderStudios/MoonMind",
        filters={
            "namespace_id": "attacker",
            "scope": "user",
            "review_state": "draft",
            "repo": "other/repo",
            "user_id": "other-user",
            "tag": "architecture",
        },
    )

    assert len(items) == 1
    assert items[0].score == 0.72
    assert client.search_calls == [
        {
            "query": "How should RAG work?",
            "filters": {
                "tag": "architecture",
                "namespace_id": "tenant-a",
                "scope": "project",
                "review_state": "approved",
                "user_id": "tenant-a:project:MoonLadderStudios:MoonMind",
                "repo": "MoonLadderStudios/MoonMind",
            },
        }
    ]


def test_add_or_update_requires_provenance_metadata() -> None:
    service = LongTermMemoryService(settings=_settings(), client=_Mem0Stub())

    with pytest.raises(LongTermMemoryError, match="provenance is required"):
        service.add_or_update(
            text="Stable convention.",
            repo="MoonLadderStudios/MoonMind",
            provenance={},
        )


def test_add_or_update_sends_required_mem0_metadata() -> None:
    client = _Mem0Stub()
    service = LongTermMemoryService(settings=_settings(), client=client)

    result = service.add_or_update(
        text="Stable convention.",
        repo="MoonLadderStudios/MoonMind",
        scope="team",
        review_state="draft",
        provenance={"workflowId": "wf-1", "taskRunId": "run-1"},
    )

    assert result == {"id": "memory-1"}
    assert client.add_calls == [
        {
            "memory": "Stable convention.",
            "user_id": "tenant-a:team:MoonLadderStudios:MoonMind",
            "metadata": {
                "namespace_id": "tenant-a",
                "repo": "MoonLadderStudios/MoonMind",
                "scope": "team",
                "review_state": "draft",
                "provenance": {"workflowId": "wf-1", "taskRunId": "run-1"},
            },
        }
    ]


def test_update_sends_text_and_metadata_only() -> None:
    client = _Mem0Stub()
    service = LongTermMemoryService(settings=_settings(), client=client)

    result = service.add_or_update(
        text="Updated memory",
        repo="MoonLadderStudios/MoonMind",
        provenance={"workflowId": "wf-1"},
        memory_id="memory-1",
    )

    assert result == {"id": "memory-1"}
    assert client.update_calls == [
        {
            "memory_id": "memory-1",
            "text": "Updated memory",
            "metadata": {
                "namespace_id": "tenant-a",
                "repo": "MoonLadderStudios/MoonMind",
                "scope": "project",
                "review_state": "draft",
                "provenance": {"workflowId": "wf-1"},
            },
        }
    ]
