"""Unit tests for overlay chunking and dedup (DOC-REQ-007)."""

from __future__ import annotations

from pathlib import Path

from moonmind.rag.overlay import OverlayChunk, chunk_file

def test_overlay_chunk_hash_is_deterministic() -> None:
    chunk = OverlayChunk(
        path=Path("test.py"),
        offset_start=0,
        offset_end=10,
        text="hello world",
    )
    hash1 = chunk.chunk_hash
    hash2 = chunk.chunk_hash

    assert hash1 == hash2
    assert hash1.startswith("sha256:")

def test_overlay_chunk_hash_differs_for_different_text() -> None:
    chunk_a = OverlayChunk(
        path=Path("test.py"), offset_start=0, offset_end=5, text="hello"
    )
    chunk_b = OverlayChunk(
        path=Path("test.py"), offset_start=0, offset_end=5, text="world"
    )

    assert chunk_a.chunk_hash != chunk_b.chunk_hash

def test_chunk_file_splits_content(tmp_path: Path) -> None:
    file = tmp_path / "test.py"
    file.write_text("a" * 100, encoding="utf-8")

    chunks = list(chunk_file(file, chunk_chars=30, overlap=10))

    assert len(chunks) >= 3
    assert all(isinstance(c, OverlayChunk) for c in chunks)
    assert chunks[0].offset_start == 0
    assert chunks[0].offset_end == 30
    # Second chunk starts at 30-10=20 (overlap)
    assert chunks[1].offset_start == 20

def test_chunk_file_returns_empty_for_empty_file(tmp_path: Path) -> None:
    file = tmp_path / "empty.py"
    file.write_text("", encoding="utf-8")

    result = list(chunk_file(file, chunk_chars=30, overlap=10))
    assert result == []

def test_chunk_file_single_chunk_for_small_file(tmp_path: Path) -> None:
    file = tmp_path / "small.py"
    file.write_text("tiny", encoding="utf-8")

    chunks = list(chunk_file(file, chunk_chars=100, overlap=10))
    assert len(chunks) == 1
    assert chunks[0].text == "tiny"
    assert chunks[0].offset_start == 0
    assert chunks[0].offset_end == 4
