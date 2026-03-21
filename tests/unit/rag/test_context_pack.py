"""Unit tests for ContextPack schema and serialization (DOC-REQ-001, DOC-REQ-003)."""

from __future__ import annotations

from moonmind.rag.context_pack import (
    ContextItem,
    ContextPack,
    build_context_pack,
    build_context_text,
)


def _make_item(**overrides: object) -> ContextItem:
    defaults = {
        "score": 0.82,
        "source": "services/api.py",
        "text": "def hello(): return 42",
        "offset_start": 100,
        "offset_end": 200,
        "trust_class": "canonical",
        "chunk_hash": "sha256:abc123",
    }
    defaults.update(overrides)
    return ContextItem(**defaults)


def test_context_item_to_dict_contains_all_fields() -> None:
    item = _make_item()
    data = item.to_dict()

    assert data["score"] == 0.82
    assert data["source"] == "services/api.py"
    assert data["text"] == "def hello(): return 42"
    assert data["offset_start"] == 100
    assert data["offset_end"] == 200
    assert data["trust_class"] == "canonical"
    assert data["chunk_hash"] == "sha256:abc123"


def test_context_pack_to_json_roundtrips() -> None:
    item = _make_item()
    pack = ContextPack(
        items=[item],
        filters={"repo": "moonmind"},
        budgets={"tokens": 500},
        usage={"tokens": 10, "latency_ms": 42.0},
        transport="direct",
        context_text="### Retrieved Context\n...",
        retrieved_at="2026-03-20T12:00:00Z",
        telemetry_id="abc123",
    )

    json_str = pack.to_json()
    assert '"context_text"' in json_str
    assert '"items"' in json_str
    assert '"transport": "direct"' in json_str

    data = pack.to_dict()
    assert len(data["items"]) == 1
    assert data["filters"]["repo"] == "moonmind"
    assert data["budgets"]["tokens"] == 500
    assert data["telemetry_id"] == "abc123"


def test_build_context_text_formats_markdown_with_citations() -> None:
    items = [
        _make_item(score=0.9, source="a.py", text="line a"),
        _make_item(score=0.8, source="b.py", text="line b"),
    ]

    text = build_context_text(items, max_chars=10000)

    assert text.startswith("### Retrieved Context")
    assert "a.py (score: 0.900, trust: canonical)" in text
    assert "b.py (score: 0.800, trust: canonical)" in text
    assert "line a" in text
    assert "line b" in text


def test_build_context_text_truncates_when_exceeding_max_chars() -> None:
    items = [
        _make_item(source=f"file_{i}.py", text="x" * 500)
        for i in range(20)
    ]

    text = build_context_text(items, max_chars=200)

    assert "[Context truncated]" in text


def test_build_context_text_reports_empty_when_no_items() -> None:
    text = build_context_text([], max_chars=1000)

    assert "No context retrieved" in text


def test_build_context_pack_populates_all_fields() -> None:
    item = _make_item()
    pack = build_context_pack(
        items=[item],
        filters={"repo": "moonmind"},
        budgets={"tokens": 500},
        usage={"tokens": 10},
        transport="direct",
        telemetry_id="tel-1",
        max_chars=10000,
    )

    assert pack.transport == "direct"
    assert pack.telemetry_id == "tel-1"
    assert len(pack.items) == 1
    assert pack.retrieved_at  # should be ISO timestamp
    assert "### Retrieved Context" in pack.context_text
