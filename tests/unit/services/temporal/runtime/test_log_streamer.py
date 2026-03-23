import asyncio
import json
from pathlib import Path

import pytest
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer


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

    ref = await log_streamer.stream_to_artifact(
        reader, run_id="run-1", stream_name="stdout"
    )

    assert ref.endswith("stdout.log")
    resolved = storage.resolve_storage_path(ref)
    assert resolved.read_bytes() == b"line 1\nline 2\n"


@pytest.mark.asyncio
async def test_stream_empty(streamer, tmp_path):
    log_streamer, storage = streamer
    reader = asyncio.StreamReader()
    reader.feed_eof()

    ref = await log_streamer.stream_to_artifact(
        reader, run_id="run-2", stream_name="stderr"
    )

    resolved = storage.resolve_storage_path(ref)
    assert resolved.read_bytes() == b""


def test_diagnostics_json_structure(streamer, tmp_path):
    log_streamer, storage = streamer
    ref = log_streamer.collect_diagnostics(
        run_id="run-3",
        exit_code=0,
        duration_seconds=42.5,
        log_refs={"stdout": "run-3/stdout.log"},
    )

    resolved = storage.resolve_storage_path(ref)
    data = json.loads(resolved.read_text())
    assert data["exit_code"] == 0
    assert data["duration_seconds"] == 42.5
    assert data["log_refs"]["stdout"] == "run-3/stdout.log"
