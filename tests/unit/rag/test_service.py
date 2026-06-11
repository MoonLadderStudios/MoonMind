"""Unit tests for ContextRetrievalService budget enforcement (DOC-REQ-005)."""

from __future__ import annotations

import pytest

from moonmind.rag.context_pack import ContextItem
from moonmind.rag.qdrant_client import SearchResult
from moonmind.rag.service import ContextRetrievalService, RetrievalBudgetExceededError
from moonmind.rag.settings import RagRuntimeSettings

def _settings(**overrides: object) -> RagRuntimeSettings:
    defaults = dict(
        qdrant_url=None,
        qdrant_host="localhost",
        qdrant_port=6333,
        qdrant_api_key=None,
        vector_collection="test_collection",
        vector_collections=("test_collection",),
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
        job_id=None,
        run_id=None,
        rag_enabled=True,
        qdrant_enabled=True,
        memory_enabled=True,
        memory_planning="off",
        memory_history="off",
        memory_long_term="off",
        memory_fail_open=True,
        memory_context_budget_tokens=4096,
        planning_workspace_root=None,
        beads_command="bd",
        memory_namespace_id="default",
        mem0_api_key=None,
        mem0_user_id=None,
    )
    defaults.update(overrides)
    return RagRuntimeSettings(**defaults)

def test_normalize_budgets_extracts_valid_integers() -> None:
    result = ContextRetrievalService._normalize_budgets(
        {"tokens": 500, "latency_ms": "800", "extra": "ignored"}
    )
    assert result == {"tokens": 500, "latency_ms": 800}

def test_normalize_budgets_skips_invalid_values() -> None:
    result = ContextRetrievalService._normalize_budgets(
        {"tokens": "abc", "latency_ms": -5}
    )
    assert result == {}

def test_normalize_budgets_handles_empty() -> None:
    assert ContextRetrievalService._normalize_budgets({}) == {}

def test_enforce_token_budget_raises_when_exceeded() -> None:
    settings = _settings(overlay_chunk_chars=1200)
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    with pytest.raises(RetrievalBudgetExceededError, match="Token budget exceeded"):
        service._enforce_token_budget(
            query="test query",
            top_k=100,
            budgets={"tokens": 5000},
        )

def test_enforce_token_budget_passes_when_within_limits() -> None:
    settings = _settings(overlay_chunk_chars=100)
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    # Should not raise
    service._enforce_token_budget(
        query="test query",
        top_k=1,
        budgets={"tokens": 10000},
    )

def test_enforce_token_budget_noop_without_budget() -> None:
    settings = _settings()
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
    )
    # No tokens budget key → no enforcement
    service._enforce_token_budget(
        query="very long query " * 1000,
        top_k=100,
        budgets={},
    )

def test_retrieval_budget_error_has_budget_type() -> None:
    error = RetrievalBudgetExceededError("Too slow", budget_type="latency_ms")
    assert error.budget_type == "latency_ms"
    assert "Too slow" in str(error)

def test_resolved_transport_prefers_explicit() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    assert settings.resolved_transport("direct") == "direct"
    assert settings.resolved_transport("gateway") == "gateway"

def test_resolved_transport_defaults_to_gateway_when_url_set() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    assert settings.resolved_transport(None) == "gateway"

def test_resolved_transport_defaults_to_direct_when_no_url() -> None:
    settings = _settings(retrieval_gateway_url=None)
    assert settings.resolved_transport(None) == "direct"


class _StubEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str):
        self.calls.append(text)
        return [0.1, 0.2]


class _StubQdrant:
    def __init__(self) -> None:
        self.ensured: list[str | None] = []
        self.calls: list[dict[str, object]] = []

    def ensure_collection_ready(self, collection_name=None) -> None:
        self.ensured.append(collection_name)

    def collection_health(self, **kwargs):
        return []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        item = ContextItem(score=0.8, source="src/file.py", text="snippet")
        return SearchResult(items=[item], latency_ms=5.0)


class _StubLongTermMemory:
    def __init__(self, *, fail: bool = False, error: Exception | None = None) -> None:
        self.fail = fail
        self.error = error
        self.search_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.fail:
            from moonmind.rag.long_term_memory import LongTermMemoryError

            raise LongTermMemoryError("mem0 unavailable")
        item = ContextItem(
            score=0.95,
            source="mem0:memory-1",
            text="Always add provenance to long-term memories.",
            trust_class="approved",
            payload={"record_kind": "long_term_memory", "memory_provider": "mem0"},
        )
        return [item], 2.0

    def add_or_update(self, **kwargs):
        self.write_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.fail:
            from moonmind.rag.long_term_memory import LongTermMemoryError

            raise LongTermMemoryError("mem0 unavailable")
        return {"id": "memory-1"}


def test_retrieve_direct_flow_uses_embedding_and_qdrant_search() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    settings = _settings(run_id="run-xyz")
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
    )

    pack = service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        initiation_mode="session",
    )

    assert embedder.calls == ["How to integrate RAG?"]
    assert qdrant.ensured == ["test_collection"]
    assert len(qdrant.calls) == 1
    assert qdrant.calls[0]["collections"] == ("test_collection",)
    assert pack.transport == "direct"
    assert pack.initiation_mode == "session"
    assert pack.truncated is False
    assert pack.items
    assert "Retrieved Context" in pack.context_text


def test_retrieve_direct_flow_passes_multiple_collections() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    settings = _settings(
        vector_collection="primary",
        vector_collections=("primary", "docs", "support"),
    )
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
    )

    service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
    )

    assert qdrant.calls[0]["collections"] == ("primary", "docs", "support")


def test_retrieve_direct_flow_prepends_approved_long_term_memories() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    memory = _StubLongTermMemory()
    settings = _settings(
        run_id="run-xyz",
        memory_long_term="mem0",
        mem0_api_key="mem0-secret",
        memory_context_budget_tokens=400,
    )
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
        long_term_memory_service=memory,
    )

    pack = service.retrieve(
        query="How should memory work?",
        filters={"repo": "MoonLadderStudios/MoonMind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        initiation_mode="automatic",
    )

    assert pack.items[0].source == "mem0:memory-1"
    assert pack.items[0].payload["record_kind"] == "long_term_memory"
    assert memory.search_calls == [
        {
            "query": "How should memory work?",
            "repo": "MoonLadderStudios/MoonMind",
            "scope": "project",
            "limit": 1,
        }
    ]
    assert pack.usage["latency_ms"] == 7.0


def test_retrieve_direct_flow_fail_opens_when_mem0_unavailable() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    memory = _StubLongTermMemory(fail=True)
    settings = _settings(
        memory_long_term="mem0",
        mem0_api_key="mem0-secret",
        memory_fail_open=True,
    )
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
        long_term_memory_service=memory,
    )

    pack = service.retrieve(
        query="How should memory work?",
        filters={"repo": "MoonLadderStudios/MoonMind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
    )

    assert [item.source for item in pack.items] == ["src/file.py"]


def test_retrieve_direct_flow_fail_opens_for_unexpected_mem0_exception() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    memory = _StubLongTermMemory(error=TypeError("sdk shape changed"))
    settings = _settings(
        memory_long_term="mem0",
        mem0_api_key="mem0-secret",
        memory_fail_open=True,
    )
    service = ContextRetrievalService(
        settings=settings,
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
        long_term_memory_service=memory,
    )

    pack = service.retrieve(
        query="How should memory work?",
        filters={"repo": "MoonLadderStudios/MoonMind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
    )

    assert [item.source for item in pack.items] == ["src/file.py"]


def test_retrieve_gateway_flow_skips_embedding_and_preserves_contract_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")

    class _UnexpectedEmbeddingClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("embedding client should not initialize in gateway mode")

    class _GatewayResponse:
        def json(self):
            return {
                "items": [
                    {
                        "score": 0.9,
                        "source": "docs/spec.md",
                        "text": "gateway snippet",
                        "trust_class": "canonical",
                    }
                ],
                "filters": {"repo": "moonmind"},
                "budgets": {"tokens": 10},
                "usage": {"tokens": 8, "latency_ms": 4},
                "context_text": "### Retrieved Context\n1. docs/spec.md",
                "retrieved_at": "2026-04-24T00:00:00Z",
                "telemetry_id": "tid",
                "initiation_mode": "session",
                "truncated": False,
            }

        def raise_for_status(self):
            return None

    class _GatewayClient:
        last_instance = None

        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
            _GatewayClient.last_instance = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return _GatewayResponse()

    monkeypatch.setattr("moonmind.rag.service.EmbeddingClient", _UnexpectedEmbeddingClient)
    monkeypatch.setattr("moonmind.rag.service.httpx.Client", _GatewayClient)

    service = ContextRetrievalService(
        settings=settings,
        env={"MOONMIND_RETRIEVAL_TOKEN": "scoped-retrieval-token"},
        qdrant_client=_StubQdrant(),
    )
    pack = service.retrieve(
        query="gateway query",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={"tokens": 5000},
        transport="gateway",
        initiation_mode="session",
    )

    assert pack.transport == "gateway"
    assert _GatewayClient.last_instance is not None
    request_kwargs = _GatewayClient.last_instance.calls[0][1]
    assert request_kwargs["json"] == {
        "query": "gateway query",
        "filters": {"repo": "moonmind"},
        "top_k": 3,
        "overlay_policy": "skip",
        "budgets": {"tokens": 5000},
        "collections": ["test_collection"],
    }
    assert request_kwargs["headers"] == {
        "X-MoonMind-Retrieval-Token": "scoped-retrieval-token"
    }
    assert pack.initiation_mode == "session"
    assert pack.truncated is False
    assert pack.filters == {"repo": "moonmind"}
    assert pack.budgets == {"tokens": 10}
    assert pack.usage == {"tokens": 8, "latency_ms": 4}
    assert pack.items[0].source == "docs/spec.md"
    assert "Retrieved Context" in pack.context_text


class _StubPlanningAdapter:
    def __init__(self) -> None:
        self.refs: list[str] = []

    def prefetch(self, planning_ref: str) -> ContextItem:
        self.refs.append(planning_ref)
        return ContextItem(
            score=1.0,
            source=f"beads:{planning_ref}",
            text="Planning Memory (Beads)\nid: bd-123\ntitle: Implement Plane A",
            trust_class="planning",
            payload={"record_kind": "planning", "planning_ref": planning_ref},
        )


class _FailingPlanningAdapter:
    def prefetch(self, planning_ref: str) -> ContextItem:
        raise KeyError(planning_ref)


def test_retrieve_direct_flow_prefetches_planning_memory_when_enabled() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    planning = _StubPlanningAdapter()
    service = ContextRetrievalService(
        settings=_settings(memory_planning="beads"),
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=embedder,
        qdrant_client=qdrant,
        planning_adapter=planning,
    )

    pack = service.retrieve(
        query="How to integrate Planning Memory?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        planning_ref="bd-123",
    )

    assert planning.refs == ["bd-123"]
    assert pack.items[0].source == "beads:bd-123"
    assert pack.items[0].trust_class == "planning"
    assert pack.items[1].source == "src/file.py"
    assert "Planning Memory (Beads)" in pack.context_text


def test_prefetch_planning_context_fails_open_for_unexpected_adapter_error() -> None:
    service = ContextRetrievalService(
        settings=_settings(memory_planning="beads", memory_fail_open=True),
        env={"GOOGLE_API_KEY": "test"},
        qdrant_client=_StubQdrant(),
        planning_adapter=_FailingPlanningAdapter(),
    )

    assert service._prefetch_planning_context("bd-123") == []


def test_prefetch_planning_context_raises_unexpected_error_when_fail_closed() -> None:
    service = ContextRetrievalService(
        settings=_settings(memory_planning="beads", memory_fail_open=False),
        env={"GOOGLE_API_KEY": "test"},
        qdrant_client=_StubQdrant(),
        planning_adapter=_FailingPlanningAdapter(),
    )

    with pytest.raises(KeyError):
        service._prefetch_planning_context("bd-123")


def test_cap_planning_item_handles_nullable_payload() -> None:
    service = ContextRetrievalService(
        settings=_settings(memory_context_budget_tokens=1),
        env={"GOOGLE_API_KEY": "test"},
        qdrant_client=_StubQdrant(),
    )
    item = ContextItem(
        score=1.0,
        source="beads:bd-123",
        text="Planning context that exceeds the tiny budget",
        trust_class="planning",
    )
    item.payload = None  # type: ignore[assignment]

    capped = service._cap_planning_item(item)

    assert capped.payload == {}
    assert capped.text.endswith("[Planning context truncated]")


def test_retrieve_gateway_flow_forwards_planning_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")

    class _GatewayResponse:
        def json(self):
            return {
                "items": [],
                "filters": {"repo": "moonmind"},
                "budgets": {},
                "usage": {},
                "context_text": "",
                "retrieved_at": "2026-04-24T00:00:00Z",
                "telemetry_id": "tid",
            }

        def raise_for_status(self):
            return None

    class _GatewayClient:
        last_instance = None

        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
            _GatewayClient.last_instance = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return _GatewayResponse()

    monkeypatch.setattr("moonmind.rag.service.httpx.Client", _GatewayClient)
    service = ContextRetrievalService(
        settings=settings,
        env={"MOONMIND_RETRIEVAL_TOKEN": "scoped-retrieval-token"},
        qdrant_client=_StubQdrant(),
    )

    service.retrieve(
        query="gateway query",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="gateway",
        planning_ref="bd-123",
    )

    assert _GatewayClient.last_instance is not None
    request_kwargs = _GatewayClient.last_instance.calls[0][1]
    assert request_kwargs["json"]["planning_ref"] == "bd-123"


def test_add_or_update_long_term_memory_skips_when_disabled() -> None:
    service = ContextRetrievalService(
        settings=_settings(memory_enabled=False),
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=_StubEmbedder(),
        qdrant_client=_StubQdrant(),
    )

    result = service.add_or_update_long_term_memory(
        text="Use approved memory only.",
        repo="MoonLadderStudios/MoonMind",
        provenance={"workflowId": "wf-1"},
    )

    assert result == {"skipped": True, "reason": "memory_disabled"}


def test_add_or_update_long_term_memory_writes_with_provenance() -> None:
    memory = _StubLongTermMemory()
    service = ContextRetrievalService(
        settings=_settings(
            memory_long_term="mem0",
            mem0_api_key="mem0-secret",
        ),
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=_StubEmbedder(),
        qdrant_client=_StubQdrant(),
        long_term_memory_service=memory,
    )

    result = service.add_or_update_long_term_memory(
        text="Use approved memory only.",
        repo="MoonLadderStudios/MoonMind",
        review_state="draft",
        provenance={"workflowId": "wf-1", "agentRunId": "run-1"},
    )

    assert result == {"id": "memory-1"}
    assert memory.write_calls == [
        {
            "text": "Use approved memory only.",
            "repo": "MoonLadderStudios/MoonMind",
            "scope": "project",
            "review_state": "draft",
            "provenance": {"workflowId": "wf-1", "agentRunId": "run-1"},
            "memory_id": None,
        }
    ]


def test_add_or_update_long_term_memory_fail_opens_for_unexpected_exception() -> None:
    memory = _StubLongTermMemory(error=AttributeError("sdk shape changed"))
    service = ContextRetrievalService(
        settings=_settings(
            memory_long_term="mem0",
            mem0_api_key="mem0-secret",
            memory_fail_open=True,
        ),
        env={"GOOGLE_API_KEY": "test"},
        embedding_client=_StubEmbedder(),
        qdrant_client=_StubQdrant(),
        long_term_memory_service=memory,
    )

    result = service.add_or_update_long_term_memory(
        text="Use approved memory only.",
        repo="MoonLadderStudios/MoonMind",
        provenance={"workflowId": "wf-1"},
    )

    assert result == {"skipped": True, "reason": "long_term_memory_unavailable"}


def test_retrieve_direct_flow_does_not_serialize_secret_env_values() -> None:
    embedder = _StubEmbedder()
    qdrant = _StubQdrant()
    service = ContextRetrievalService(
        settings=_settings(run_id="run-secret-safe"),
        env={
            "GOOGLE_API_KEY": "google-secret-key",
            "OPENAI_API_KEY": "openai-secret-key",
            "MOONMIND_RETRIEVAL_TOKEN": "retrieval-token-secret",
        },
        embedding_client=embedder,
        qdrant_client=qdrant,
    )

    pack = service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
        initiation_mode="automatic",
    )

    serialized = pack.to_json()
    assert "google-secret-key" not in serialized
    assert "openai-secret-key" not in serialized
    assert "retrieval-token-secret" not in serialized
