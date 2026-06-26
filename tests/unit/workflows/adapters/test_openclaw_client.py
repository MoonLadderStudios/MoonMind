"""Unit tests for OpenClaw HTTP client SSE parsing helpers."""

from __future__ import annotations

from moonmind.workflows.adapters.openclaw_client import parse_sse_lines_for_deltas

def test_parse_sse_lines_extracts_delta_content() -> None:
    lines = [
        "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}",
        "",
        "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}",
        "data: [DONE]",
    ]
    assert parse_sse_lines_for_deltas(lines) == ["Hello", " world"]

def test_parse_sse_skips_malformed_json() -> None:
    lines = [
        "data: not-json",
        "data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}",
    ]
    assert parse_sse_lines_for_deltas(lines) == ["x"]

def test_parse_sse_done_stops() -> None:
    lines = [
        "data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}",
        "data: [DONE]",
        "data: {\"choices\":[{\"delta\":{\"content\":\"b\"}}]}",
    ]
    assert parse_sse_lines_for_deltas(lines) == ["a"]
