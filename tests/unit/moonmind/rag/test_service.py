from moonmind.rag.context_pack import ContextItem
from moonmind.rag.qdrant_client import SearchResult
from moonmind.rag.service import ContextRetrievalService, RetrievalBudgetExceededError
from moonmind.rag.settings import RagRuntimeSettings


class StubEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str):
        self.calls.append(text)
        return [0.1, 0.2]


class StubQdrant:
    def __init__(self) -> None:
        self.ensured = False
        self.calls: list[dict] = []

    def ensure_collection_ready(self):
        self.ensured = True

    def search(self, **kwargs):
        self.calls.append(kwargs)
        item = ContextItem(score=0.8, source="src/file.py", text="snippet")
        return SearchResult(items=[item], latency_ms=5.0)


def test_context_retrieval_service_direct_flow(monkeypatch):
    env = {
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "GOOGLE_EMBEDDING_DIMENSIONS": "2",
        "MOONMIND_RUN_ID": "run-xyz",
    }
    settings = RagRuntimeSettings.from_env(env)
    service = ContextRetrievalService(
        settings=settings,
        env=env,
        embedding_client=StubEmbedder(),
        qdrant_client=StubQdrant(),
    )
    pack = service.retrieve(
        query="How to integrate RAG?",
        filters={"repo": "moonmind"},
        top_k=3,
        overlay_policy="skip",
        budgets={},
        transport="direct",
    )
    assert pack.items
    assert pack.transport == "direct"
    assert "Retrieved Context" in pack.context_text


def test_context_retrieval_service_honors_overlay_mode_non_collection():
    env = {
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "GOOGLE_EMBEDDING_DIMENSIONS": "2",
        "MOONMIND_RUN_ID": "run-xyz",
        "RAG_OVERLAY_MODE": "inline",
    }
    settings = RagRuntimeSettings.from_env(env)
    qdrant = StubQdrant()
    service = ContextRetrievalService(
        settings=settings,
        env=env,
        embedding_client=StubEmbedder(),
        qdrant_client=qdrant,
    )

    service.retrieve(
        query="overlay check",
        filters={"repo": "moonmind"},
        top_k=2,
        overlay_policy="include",
        budgets={},
        transport="direct",
    )

    assert qdrant.calls
    assert qdrant.calls[0]["overlay_collection"] is None


def test_context_retrieval_service_enforces_token_budget():
    env = {
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "GOOGLE_EMBEDDING_DIMENSIONS": "2",
    }
    settings = RagRuntimeSettings.from_env(env)
    service = ContextRetrievalService(
        settings=settings,
        env=env,
        embedding_client=StubEmbedder(),
        qdrant_client=StubQdrant(),
    )

    try:
        service.retrieve(
            query="budget check",
            filters={},
            top_k=4,
            overlay_policy="skip",
            budgets={"tokens": 2},
            transport="direct",
        )
    except RetrievalBudgetExceededError as exc:
        assert exc.budget_type == "tokens"
    else:
        raise AssertionError("expected RetrievalBudgetExceededError")
