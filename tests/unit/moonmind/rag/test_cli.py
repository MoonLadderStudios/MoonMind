from pathlib import Path

import pytest

from moonmind.rag import cli as rag_cli
from moonmind.rag.context_pack import ContextItem, build_context_pack
from moonmind.rag.embedding import EmbeddingError


def test_top_level_cli_imports_without_schema_cycle():
    import moonmind.cli as moonmind_cli

    assert moonmind_cli.app is not None


def test_run_search_returns_context_pack_and_writes_json(
    monkeypatch,
    tmp_path: Path,
):
    calls: list[dict[str, object]] = []

    class StubSettings:
        similarity_top_k = 7

        def as_filter_metadata(self):
            return {"job_id": "job-123"}

        def resolved_transport(self, preferred):
            return preferred or "direct"

    pack = build_context_pack(
        items=[ContextItem(score=0.91, source="src/app.py", text="retrieved text")],
        filters={"repo": "moonmind"},
        budgets={},
        usage={"tokens": 42, "latency_ms": 8},
        transport="direct",
        telemetry_id="ctx-test",
        max_chars=1000,
    )

    class StubService:
        def __init__(self, *, settings, env):
            _ = settings, env

        def retrieve(self, **kwargs):
            calls.append(dict(kwargs))
            return pack

    monkeypatch.setattr(
        rag_cli.RagRuntimeSettings,
        "from_env",
        classmethod(lambda _cls, _source=None: StubSettings()),
    )
    monkeypatch.setattr(rag_cli, "ContextRetrievalService", StubService)

    output_path = tmp_path / "context-pack.json"
    result = rag_cli.run_search(
        query="How does worker retrieval work?",
        filter_args=["repo=moonmind"],
        budget_args=[],
        top_k=None,
        overlay_policy="include",
        transport=None,
        output_file=output_path,
    )

    assert result is pack
    assert output_path.exists()
    assert '"context_text"' in output_path.read_text(encoding="utf-8")
    assert calls[0]["top_k"] == 7
    assert calls[0]["filters"] == {"job_id": "job-123", "repo": "moonmind"}
    assert calls[0]["transport"] == "direct"


def test_run_search_reports_fallback_unavailable_when_semantic_fails_without_matches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class StubSettings:
        similarity_top_k = 7

        def as_filter_metadata(self):
            return {}

        def resolved_transport(self, preferred):
            return preferred or "direct"

    class StubService:
        def __init__(self, *, settings, env):
            _ = settings, env

        def retrieve(self, **kwargs):
            _ = kwargs
            raise EmbeddingError("GOOGLE_API_KEY is required for google embeddings")

    monkeypatch.setattr(
        rag_cli.RagRuntimeSettings,
        "from_env",
        classmethod(lambda _cls, _source=None: StubSettings()),
    )
    monkeypatch.setattr(rag_cli, "ContextRetrievalService", StubService)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(rag_cli.CliError, match="local_fallback_unavailable"):
        rag_cli.run_search(
            query="How does worker retrieval work?",
            filter_args=[],
            budget_args=[],
            top_k=None,
            overlay_policy="include",
            transport=None,
            output_file=None,
        )


def test_run_search_returns_local_fallback_context_pack_when_semantic_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class StubSettings:
        similarity_top_k = 7

        def as_filter_metadata(self):
            return {}

        def resolved_transport(self, preferred):
            return preferred or "direct"

    class StubService:
        def __init__(self, *, settings, env):
            _ = settings, env

        def retrieve(self, **kwargs):
            _ = kwargs
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(
        rag_cli.RagRuntimeSettings,
        "from_env",
        classmethod(lambda _cls, _source=None: StubSettings()),
    )
    monkeypatch.setattr(rag_cli, "ContextRetrievalService", StubService)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "WorkflowRag.md").write_text(
        "Follow-up retrieval can use local fallback search "
        "when qdrant is unavailable.\n",
        encoding="utf-8",
    )

    pack = rag_cli.run_search(
        query="follow-up retrieval local fallback qdrant unavailable",
        filter_args=[],
        budget_args=[],
        top_k=None,
        overlay_policy="include",
        transport=None,
        output_file=None,
    )

    assert pack.transport == "local_fallback"
    assert pack.initiation_mode == "session"
    assert pack.usage["fallback_reason"] == "qdrant_unavailable"
    assert pack.usage["item_count"] == 1
    assert pack.items[0].source == "docs/WorkflowRag.md"
