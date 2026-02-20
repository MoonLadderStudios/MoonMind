from pathlib import Path

from moonmind.rag import cli as rag_cli
from moonmind.rag.context_pack import ContextItem, build_context_pack


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
