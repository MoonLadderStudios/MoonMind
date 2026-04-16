import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from moonmind.schemas.agent_runtime_models import RunObservabilityEvent
from moonmind.workflows.temporal.runtime import log_streamer as log_streamer_module
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.output_parser import (
    GeminiCliOutputParser,
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
async def test_stream_redacts_secret_like_values_from_artifacts_and_live_events(
    streamer,
):
    log_streamer, storage = streamer
    mock_publisher = Mock()
    log_streamer.publisher = mock_publisher
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890 token=abc123\n"
    )
    reader.feed_eof()

    ref, content, events = await log_streamer.stream_to_artifact(
        reader, run_id="run-redact", stream_name="stdout"
    )

    resolved = storage.resolve_storage_path(ref)
    persisted = resolved.read_text(encoding="utf-8")
    assert "ghp_" not in persisted
    assert "abc123" not in persisted
    assert "ghp_" not in content
    assert events == []
    published = mock_publisher.publish.call_args_list[0][0][0]
    assert "ghp_" not in published.text
    assert "abc123" not in published.text


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


def test_diagnostics_json_includes_annotations(streamer, tmp_path):
    log_streamer, storage = streamer
    annotations = [
        {"annotation_type": "run_started", "text": "Supervisor: managed run started."},
        {"annotation_type": "run_classified_completed", "text": "Supervisor: run classified as completed."},
    ]
    ref = log_streamer.collect_diagnostics(
        run_id="run-4",
        exit_code=0,
        duration_seconds=1.2,
        log_refs={"stdout": "run-4/stdout.log"},
        annotations=annotations,
        events=[{"type": "step", "status": "running"}],
    )

    resolved = storage.resolve_storage_path(ref)
    data = json.loads(resolved.read_text())
    assert len(data["annotations"]) == 2
    assert data["annotations"][0]["annotation_type"] == "run_started"


@pytest.mark.asyncio
async def test_emit_system_annotation_preserves_global_sequence_with_stream_chunks(streamer):
    log_streamer, _ = streamer
    mock_publisher = Mock()
    log_streamer.publisher = mock_publisher
    log_streamer._sequence_counter = 0

    log_streamer.emit_system_annotation(
        run_id="run-seq",
        workspace_path=None,
        text="Supervisor: managed run started.",
        annotation_type="run_started",
    )

    reader = asyncio.StreamReader()
    reader.feed_data(b"hello\n")
    reader.feed_eof()

    await log_streamer.stream_to_artifact(
        reader,
        run_id="run-seq",
        stream_name="stdout",
    )

    published_chunks = [
        call_args[0][0] for call_args in mock_publisher.publish.call_args_list
    ]
    assert [chunk.stream for chunk in published_chunks] == ["system", "stdout"]
    assert [chunk.sequence for chunk in published_chunks] == [1, 2]
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

    log_refs, stdout, stderr, parsed, events = await log_streamer.stream_and_parse(
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

    log_refs, stdout, stderr, parsed, events = await log_streamer.stream_and_parse(
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

    _, _, _, parsed, events = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-rl", parser=NdjsonOutputParser(),
    )

    assert parsed.rate_limited
    assert len(parsed.error_messages) > 0


@pytest.mark.asyncio
async def test_stream_and_parse_invokes_event_callback(streamer):
    log_streamer, _ = streamer
    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(
        b"Attempt 6 failed with status 429. Retrying with backoff...\n"
    )
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_eof()
    seen_events: list[dict] = []

    async def _callback(events: list[dict]) -> None:
        seen_events.extend(events)

    _, _, _, parsed, events = await log_streamer.stream_and_parse(
        stdout_reader,
        stderr_reader,
        run_id="run-parse-gemini-callback",
        parser=GeminiCliOutputParser(),
        event_callback=_callback,
    )

    assert parsed.rate_limited
    assert any(event.get("type") == "rate_limit" for event in seen_events)


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

    log_refs, stdout, stderr, parsed, events = await log_streamer.stream_and_parse(
        stdout_reader, stderr_reader,
        run_id="run-parse-default",
    )

    assert isinstance(parsed, ParsedOutput)
    assert not parsed.has_structured_output
    assert parsed.raw_text == "hello\n"


@pytest.mark.asyncio
async def test_stream_to_artifact_calls_publisher(streamer):
    from unittest.mock import Mock
    from moonmind.schemas.agent_runtime_models import LiveLogChunk

    log_streamer, _ = streamer
    mock_publisher = Mock()
    log_streamer.publisher = mock_publisher

    log_streamer._sequence_counter = 0

    reader = asyncio.StreamReader()
    reader.feed_data(b"chunk1\n")
    reader.feed_data(b"chunk2\n")
    reader.feed_eof()

    await log_streamer.stream_to_artifact(reader, run_id="run-pub", stream_name="stdout")

    assert mock_publisher.publish.call_count >= 1

    published_chunks = [
        call_args[0][0] for call_args in mock_publisher.publish.call_args_list
    ]

    for i, chunk in enumerate(published_chunks, start=1):
        assert isinstance(chunk, LiveLogChunk)
        assert chunk.stream == "stdout"
        assert chunk.sequence == i

    combined_text = "".join(chunk.text for chunk in published_chunks)
    assert combined_text == "chunk1\nchunk2\n"


def test_emit_observability_event_publishes_session_metadata(streamer):
    log_streamer, _ = streamer
    mock_publisher = Mock()
    log_streamer.publisher = mock_publisher

    log_streamer.emit_observability_event(
        run_id="run-session-event",
        workspace_path=None,
        stream="session",
        text="Session cleared.",
        kind="session_reset_boundary",
        session_id="sess-1",
        session_epoch=2,
        container_id="ctr-1",
        thread_id="thread-2",
        turn_id="turn-7",
        active_turn_id=None,
        metadata={"reason": "operator_reset"},
    )

    published_chunk = mock_publisher.publish.call_args[0][0]
    assert isinstance(published_chunk, RunObservabilityEvent)
    assert published_chunk.run_id == "run-session-event"
    assert published_chunk.stream == "session"
    assert published_chunk.kind == "session_reset_boundary"
    assert published_chunk.session_id == "sess-1"
    assert published_chunk.session_epoch == 2
    assert published_chunk.thread_id == "thread-2"
    assert published_chunk.metadata["reason"] == "operator_reset"

    persisted_events = log_streamer.consume_observability_events("run-session-event")
    assert persisted_events[0]["runId"] == "run-session-event"
    assert persisted_events[0]["sessionId"] == "sess-1"
    assert persisted_events[0]["sessionEpoch"] == 2
    assert "session_id" not in persisted_events[0]


def test_persist_observability_events_promotes_spool_to_artifact(
    streamer,
    tmp_path: Path,
) -> None:
    log_streamer, storage = streamer
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    spool_path = workspace_path / "live_streams.spool"
    spool_payload = "\n".join(
        [
            json.dumps(
                {
                    "runId": "run-journal",
                    "sequence": 1,
                    "stream": "stdout",
                    "text": "hello\n",
                    "timestamp": "2026-04-08T00:00:00Z",
                    "kind": "stdout_chunk",
                }
            ),
            json.dumps(
                {
                    "runId": "run-journal",
                    "sequence": 2,
                    "stream": "session",
                    "text": "Session started.",
                    "timestamp": "2026-04-08T00:00:01Z",
                    "kind": "session_started",
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                }
            ),
        ]
    )
    spool_path.write_text(spool_payload + "\n", encoding="utf-8")

    ref = log_streamer.persist_observability_events(
        run_id="run-journal",
        workspace_path=str(workspace_path),
    )

    assert ref == "run-journal/observability.events.jsonl"
    persisted = storage.resolve_storage_path(ref).read_text(encoding="utf-8")
    assert persisted == spool_payload + "\n"


def test_persist_observability_events_returns_none_on_io_error(
    streamer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_streamer, _ = streamer
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    spool_path = workspace_path / "live_streams.spool"
    spool_path.write_text("{}", encoding="utf-8")

    def _raise_io_error(*args: object, **kwargs: object) -> object:
        raise OSError("disk error")

    monkeypatch.setattr(Path, "open", _raise_io_error)

    ref = log_streamer.persist_observability_events(
        run_id="run-journal",
        workspace_path=str(workspace_path),
    )

    assert ref is None


def test_emit_system_annotation_keeps_annotations_out_of_observability_events(streamer):
    log_streamer, _ = streamer

    log_streamer.emit_system_annotation(
        run_id="run-annotations",
        workspace_path=None,
        text="Supervisor started.",
        annotation_type="run_started",
    )

    annotations = log_streamer.consume_annotations("run-annotations")
    observability_events = log_streamer.consume_observability_events("run-annotations")

    assert len(annotations) == 1
    assert annotations[0]["annotation_type"] == "run_started"
    assert observability_events == []


def test_observability_publish_reuses_one_spool_publisher_per_workspace(
    streamer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_streamer, _ = streamer
    created_publishers: list[object] = []

    class _FakePublisher:
        def __init__(self, workspace_path: str) -> None:
            self.workspace_path = workspace_path
            self.published: list[object] = []
            created_publishers.append(self)

        def publish(self, chunk: object) -> None:
            self.published.append(chunk)

    monkeypatch.setattr(log_streamer_module, "SpoolLogPublisher", _FakePublisher)

    log_streamer.emit_observability_event(
        run_id="run-cache",
        workspace_path="/tmp/workspace",
        stream="session",
        text="event one",
        kind="session_started",
    )
    log_streamer.emit_observability_event(
        run_id="run-cache",
        workspace_path="/tmp/workspace",
        stream="session",
        text="event two",
        kind="session_started",
    )

    assert len(created_publishers) == 1
    assert len(created_publishers[0].published) == 2


@pytest.mark.asyncio
async def test_stream_and_parse_uses_one_global_sequence_across_streams(tmp_path):
    from unittest.mock import Mock

    storage = _StubArtifactStorage(tmp_path)
    log_streamer = RuntimeLogStreamer(storage)
    mock_publisher = Mock()
    log_streamer.publisher = mock_publisher

    stdout_reader = asyncio.StreamReader()
    stdout_reader.feed_data(b"out-1\n")
    stdout_reader.feed_eof()
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_data(b"err-1\n")
    stderr_reader.feed_eof()

    await log_streamer.stream_and_parse(
        stdout_reader,
        stderr_reader,
        run_id="run-global-seq",
    )

    published_chunks = [
        call_args[0][0] for call_args in mock_publisher.publish.call_args_list
    ]
    assert [chunk.sequence for chunk in published_chunks] == [1, 2]
    assert {chunk.stream for chunk in published_chunks} == {"stdout", "stderr"}
