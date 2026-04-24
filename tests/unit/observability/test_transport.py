"""Unit tests for the Live Log spool transport."""

import asyncio
from pathlib import Path

import pytest

from moonmind.observability.transport import SpoolLogPublisher, SpoolLogReader
from moonmind.schemas.agent_runtime_models import LiveLogChunk

def test_spool_log_publisher_appends_json_chunks(tmp_path: Path) -> None:
    # Mimic the /work/agent_jobs/run-123 directory
    workspace_dir = tmp_path / "run-123"
    workspace_dir.mkdir()
    
    publisher = SpoolLogPublisher(workspace_path=str(workspace_dir))
    
    chunk1 = LiveLogChunk(
        runId="run-123",
        sequence=1,
        stream="stdout",
        text="Hello\n",
        timestamp="2026-03-31T00:00:00Z",
        offset=0,
    )
    chunk2 = LiveLogChunk(
        runId="run-123",
        sequence=2,
        stream="stderr",
        text="Error!\n",
        timestamp="2026-03-31T00:00:01Z",
        offset=6,
        sessionId="sess-1",
    )
    
    publisher.publish(chunk1)
    publisher.publish(chunk2)
    
    spool_file = workspace_dir / "live_streams.spool"
    assert spool_file.exists()
    
    import json
    lines = spool_file.read_text().splitlines()
    assert len(lines) == 2
    
    parsed1 = json.loads(lines[0])
    assert parsed1["sequence"] == 1
    assert parsed1["stream"] == "stdout"
    assert parsed1["runId"] == "run-123"

    parsed2 = json.loads(lines[1])
    assert parsed2["sequence"] == 2
    assert parsed2["stream"] == "stderr"
    assert parsed2["runId"] == "run-123"
    assert parsed2["sessionId"] == "sess-1"

@pytest.mark.asyncio
async def test_spool_log_reader_tails_file(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "run-reader"
    workspace_dir.mkdir()
    publisher = SpoolLogPublisher(workspace_path=str(workspace_dir))
    
    # Write initial data
    publisher.publish(LiveLogChunk(sequence=1, stream="stdout", text="A", timestamp="0", offset=0))
    
    reader = SpoolLogReader(workspace_path=str(workspace_dir))
    
    # Run the generator task
    chunks = []
    
    async def consume():
        async for chunk in reader.follow():
            chunks.append(chunk)
            if len(chunks) == 2:
                reader.stop()
                
    consumer_task = asyncio.create_task(consume())
    
    # Yield control so consumer hits the end of file and waits
    await asyncio.sleep(0.01)
    
    # Write second piece of data while the consumer is waiting
    publisher.publish(LiveLogChunk(sequence=2, stream="stderr", text="B", timestamp="1", offset=1))
    
    await asyncio.wait_for(consumer_task, timeout=2.0)
    
    assert len(chunks) == 2
    assert chunks[0].sequence == 1
    assert chunks[1].sequence == 2
    assert chunks[1].stream == "stderr"

@pytest.mark.asyncio
async def test_spool_log_reader_respects_since_sequence(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "run-since"
    workspace_dir.mkdir()
    publisher = SpoolLogPublisher(workspace_path=str(workspace_dir))
    
    publisher.publish(LiveLogChunk(sequence=1, stream="stdout", text="1", timestamp="0", offset=0))
    publisher.publish(LiveLogChunk(sequence=2, stream="stdout", text="2", timestamp="1", offset=0))
    publisher.publish(LiveLogChunk(sequence=3, stream="stdout", text="3", timestamp="2", offset=0))
    
    reader = SpoolLogReader(workspace_path=str(workspace_dir))
    reader.stop() # stop immediately so it doesn't hang after reading the buffer
    
    chunks = [c async for c in reader.follow(since_sequence=2)]
    
    assert len(chunks) == 1
    assert chunks[0].sequence == 3

@pytest.mark.asyncio
async def test_spool_log_reader_without_since_starts_at_end_when_requested(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "run-tail-end"
    workspace_dir.mkdir()
    publisher = SpoolLogPublisher(workspace_path=str(workspace_dir))

    publisher.publish(LiveLogChunk(sequence=1, stream="stdout", text="1", timestamp="0", offset=0))
    publisher.publish(LiveLogChunk(sequence=2, stream="stdout", text="2", timestamp="1", offset=1))

    reader = SpoolLogReader(workspace_path=str(workspace_dir))
    chunks = []

    async def consume() -> None:
        async for chunk in reader.follow(start_at_end=True):
            chunks.append(chunk)
            reader.stop()

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    publisher.publish(LiveLogChunk(sequence=3, stream="stderr", text="3", timestamp="2", offset=2))

    await asyncio.wait_for(consumer_task, timeout=2.0)

    assert len(chunks) == 1
    assert chunks[0].sequence == 3
