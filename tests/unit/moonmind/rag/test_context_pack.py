from moonmind.rag.context_pack import ContextItem, build_context_pack


def test_build_context_pack_truncates_long_text():
    items = [
        ContextItem(score=0.9, source="a.py", text="line" * 1000),
        ContextItem(score=0.8, source="b.py", text="short"),
    ]
    pack = build_context_pack(
        items=items,
        filters={"repo": "moonmind"},
        budgets={"tokens": 1200},
        usage={"tokens": 500, "latency_ms": 20},
        transport="direct",
        telemetry_id="ctx123",
        max_chars=120,
    )
    assert pack.context_text.startswith("### Retrieved Context")
    assert "[Context truncated]" in pack.context_text
    assert pack.items[0].source == "a.py"
