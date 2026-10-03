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


@pytest.mark.parametrize("kind", ["leaf_link", "parent_link", "hardlink"])
def test_spool_publisher_never_modifies_file_outside_workspace(tmp_path, kind):
    import os

    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "live_streams.spool"
    victim.write_text("private contents\n")
    workspace = tmp_path / "workspace"
    if kind == "parent_link":
        workspace.symlink_to(outside, target_is_directory=True)
    else:
        workspace.mkdir()
        target = workspace / "live_streams.spool"
        if kind == "hardlink":
            os.link(victim, target)
        else:
            target.symlink_to(victim)
    with pytest.raises(OSError):
        publisher = SpoolLogPublisher(str(workspace))
        publisher.publish(
            LiveLogChunk(
                sequence=1, stream="stdout", text="new", timestamp="0", offset=0
            )
        )
    assert victim.read_text() == "private contents\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_link", [False, True])
async def test_spool_reader_never_publishes_link_target(tmp_path, parent_link):
    outside = tmp_path / "outside"
    outside.mkdir()
    SpoolLogPublisher(str(outside)).publish(
        LiveLogChunk(
            sequence=1,
            stream="stdout",
            text="private contents",
            timestamp="0",
            offset=0,
        )
    )
    workspace = tmp_path / "workspace"
    if parent_link:
        workspace.symlink_to(outside, target_is_directory=True)
    else:
        workspace.mkdir()
        (workspace / "live_streams.spool").symlink_to(outside / "live_streams.spool")
    reader = SpoolLogReader(str(workspace))
    reader.stop()
    assert [chunk async for chunk in reader.follow()] == []


def test_spool_append_is_pinned_when_parent_path_is_replaced(tmp_path, monkeypatch):
    import os

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "live_streams.spool"
    victim.write_text("private contents\n")
    real_open = os.open

    def replace_parent(path, flags, *args, **kwargs):
        if path == "live_streams.spool" and "dir_fd" in kwargs:
            workspace.rename(tmp_path / "detached")
            workspace.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_parent)
    SpoolLogPublisher(str(workspace)).publish(
        LiveLogChunk(sequence=1, stream="stdout", text="new", timestamp="0", offset=0)
    )
    assert victim.read_text() == "private contents\n"
    assert (tmp_path / "detached" / "live_streams.spool").is_file()


@pytest.mark.asyncio
async def test_spool_reader_retains_partial_append_across_polls_and_restart(tmp_path):
    chunk = LiveLogChunk(
        sequence=1, stream="stdout", text="partial €", timestamp="0", offset=0
    )
    data = (chunk.model_dump_json(by_alias=True, exclude_none=True) + "\n").encode()
    spool = tmp_path / "live_streams.spool"
    split = data.index("€".encode()) + 1
    spool.write_bytes(data[:split])
    reader = SpoolLogReader(str(tmp_path))

    async def consume():
        async for result in reader.follow():
            reader.stop()
            return result
        raise AssertionError("Spool reader stopped before yielding the appended chunk")

    consuming = asyncio.create_task(consume())
    await asyncio.sleep(0.06)
    with spool.open("ab") as stream:
        stream.write(data[split:])
    assert (await asyncio.wait_for(consuming, timeout=2)).text == "partial €"
    SpoolLogPublisher(str(tmp_path)).publish(
        LiveLogChunk(
            sequence=2, stream="stdout", text="resumed", timestamp="1", offset=9
        )
    )
    restarted = SpoolLogReader(str(tmp_path))
    restarted.stop()
    assert [chunk.text async for chunk in restarted.follow(since_sequence=1)] == [
        "resumed"
    ]
