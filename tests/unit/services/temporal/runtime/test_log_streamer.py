import asyncio
import json
from pathlib import Path

import pytest
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.output_parser import (
    NdjsonOutputParser,
    ParsedOutput,
    PlainTextOutputParser,
)


class _StubArtifactStorage:
    """Minimal file-based artifact storage for tests (replaces AgentQueueArtifactStorage)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(
        self, *, job_id: str, artifact_name: str, data: bytes
    ) -> tuple[Path, str]:
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref: str) -> Path:
        return self._root / ref


@pytest.fixture
def streamer(tmp_path):
    storage = _StubArtifactStorage(tmp_path)
    return RuntimeLogStreamer(storage), storage


@pytest.mark.asyncio
async def test_stream_writes_file(streamer, tmp_path):
    log_streamer, storage = streamer
    reader = asyncio.StreamReader()
    reader.feed_data(b"line 1\nline 2\n")
    reader.feed_eof()

    ref, content, events = await log_streamer.stream_to_artifact(
        reader, run_id="run-1", stream_name="stdout"
    )

    assert ref.endswith("stdout.log")
    resolved = storage.resolve_storage_path(ref)
    assert resolved.read_bytes() == b"line 1\nline 2\n"
    assert events == []


@pytest.mark.asyncio
async def test_stream_empty(streamer, tmp_path):
    log_streamer, storage = streamer
    reader = asyncio.StreamReader()
    reader.feed_eof()

    ref, content, events = await log_streamer.stream_to_artifact(
        reader, run_id="run-2", stream_name="stderr"
    )

    resolved = storage.resolve_storage_path(ref)
    assert resolved.read_bytes() == b""
    assert events == []


def test_diagnostics_json_structure(streamer, tmp_path):
    log_streamer, storage = streamer
    ref = log_streamer.collect_diagnostics(
        run_id="run-3",
        exit_code=0,
        duration_seconds=42.5,
        log_refs={"stdout": "run-3/stdout.log"},
        events=[{"type": "step", "status": "running"}],
    )

    resolved = storage.resolve_storage_path(ref)
    data = json.loads(resolved.read_text())
    assert data["exit_code"] == 0
    assert data["duration_seconds"] == 42.5
    assert data["log_refs"]["stdout"] == "run-3/stdout.log"
    assert len(data["events"]) == 1
    assert data["events"][0]["type"] == "step"
# ---------------------------------------------------------------------------
# stream_and_parse tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_and_parse_with_plain_text_parser(streamer):
    log_streamer, _ = streamer
    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(b"all good\n")
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_data(b"Error: oops\n")
    stderr_reader.feed_eof()

    log_refs, stdout, stderr, parsed = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-plain", parser=PlainTextOutputParser(),
    )

    assert "stdout" in log_refs
    assert "stderr" in log_refs
    assert stdout == "all good\n"
    assert stderr == "Error: oops\n"
    assert isinstance(parsed, ParsedOutput)
    assert not parsed.has_structured_output
    assert any("Error:" in m for m in parsed.error_messages)


@pytest.mark.asyncio
async def test_stream_and_parse_with_ndjson_parser(streamer):
    log_streamer, _ = streamer
    events = [
        {"type": "progress", "message": "Working..."},
        {"type": "result", "message": "Done"},
    ]
    ndjson = "\n".join(json.dumps(e) for e in events) + "\n"
    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(ndjson.encode())
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_eof()

    log_refs, stdout, stderr, parsed = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-ndjson", parser=NdjsonOutputParser(),
    )

    assert parsed.has_structured_output
    assert len(parsed.events) == 2
    assert not parsed.rate_limited


@pytest.mark.asyncio
async def test_stream_and_parse_detects_rate_limit(streamer):
    log_streamer, _ = streamer
    event = {"type": "error", "status_code": 429, "message": "Too many requests"}
    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(json.dumps(event).encode())
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_eof()

    _, _, _, parsed = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-rl", parser=NdjsonOutputParser(),
    )

    assert parsed.rate_limited
    assert len(parsed.error_messages) > 0


def test_diagnostics_includes_parsed_output(streamer):
    log_streamer, storage = streamer
    parsed = ParsedOutput(
        raw_text="test",
        events=[{"type": "ok"}],
        error_messages=["Error: something"],
        rate_limited=True,
        has_structured_output=True,
    )
    ref = log_streamer.collect_diagnostics(
        run_id="run-diag-parsed",
        exit_code=1,
        duration_seconds=10.0,
        log_refs={"stdout": "ref"},
        parsed_output=parsed,
    )

    resolved = storage.resolve_storage_path(ref)
    data = json.loads(resolved.read_text())
    assert "parsed_output" in data
    po = data["parsed_output"]
    assert po["has_structured_output"] is True
    assert po["event_count"] == 1
    assert po["rate_limited"] is True
    assert "Error: something" in po["error_messages"]


@pytest.mark.asyncio
async def test_stream_and_parse_no_parser_uses_default(streamer):
    """When no explicit parser is passed, PlainTextOutputParser is used."""
    log_streamer, _ = streamer
    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(b"hello\n")
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_eof()

    log_refs, stdout, stderr, parsed = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-default",
    )

    assert isinstance(parsed, ParsedOutput)
    assert not parsed.has_structured_output
    assert parsed.raw_text == "hello\n"
