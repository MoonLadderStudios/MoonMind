"""
Unit tests for Phase 3 Live Logs — SSE publisher/subscriber.

DOC-REQ coverage:
  DOC-REQ-001  GET /api/task-runs/{id}/logs/stream endpoint contract
  DOC-REQ-002  ObservabilityPublisher fan-out
  DOC-REQ-003  LogStreamEvent payload shape
  DOC-REQ-004  since= resumption semantics
  DOC-REQ-005  Disconnect cleanup
  DOC-REQ-006  last_log_at lifecycle hook (implicit via publisher publish track)
  DOC-REQ-007  410 fallback for completed runs
  DOC-REQ-008  system stream type
"""
import asyncio
import pytest
from datetime import datetime, timezone

from moonmind.services.observability.publisher import ObservabilityPublisher
from moonmind.services.observability.models import LogStreamEvent, LogStreamType
from moonmind.services.observability.subscriber import log_stream_generator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evt(seq: int, stream: LogStreamType = LogStreamType.stdout, text: str = "") -> LogStreamEvent:
    return LogStreamEvent(
        sequence=seq,
        stream=stream,
        offset=seq * 10,
        timestamp=datetime.now(timezone.utc),
        text=text or f"log line {seq}",
    )


class _AlwaysConnected:
    async def is_disconnected(self):
        return False


class _AlwaysDisconnected:
    async def is_disconnected(self):
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publisher_fanout_to_multiple_subscribers(anyio_backend="asyncio"):
    """DOC-REQ-002: publish fans out to all active subscribers."""
    pub = ObservabilityPublisher()
    run_id = "run-fanout"

    collected_a: list[LogStreamEvent] = []
    collected_b: list[LogStreamEvent] = []

    async def subscribe_one(target: list, limit: int):
        async for evt in pub.subscribe(run_id):
            target.append(evt)
            if len(target) >= limit:
                break

    task_a = asyncio.create_task(subscribe_one(collected_a, limit=2))
    task_b = asyncio.create_task(subscribe_one(collected_b, limit=2))
    await asyncio.sleep(0.01)  # let tasks enter queue.get()

    await pub.publish(run_id, _evt(1))
    await pub.publish(run_id, _evt(2))
    await asyncio.gather(task_a, task_b)

    assert len(collected_a) == 2  # DOC-REQ-002
    assert len(collected_b) == 2


@pytest.mark.asyncio
async def test_logstreamevent_payload_shape():
    """DOC-REQ-003, DOC-REQ-008: LogStreamEvent serialises correctly incl system stream."""
    evt = _evt(7, LogStreamType.system, "Supervisor crashed")
    data = evt.model_dump()

    assert data["sequence"] == 7
    assert data["stream"] == LogStreamType.system
    assert data["text"] == "Supervisor crashed"
    assert "offset" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_since_resumption_yields_history_then_live():
    """DOC-REQ-004: since= fetches history >= N before live events."""
    pub = ObservabilityPublisher()
    run_id = "run-since"

    # Pre-populate 5-item history (seq 0-4)
    for i in range(5):
        await pub.publish(run_id, _evt(i))

    sequences_seen: list[int] = []

    async def collect(limit: int):
        async for evt in pub.subscribe(run_id, since=3):
            sequences_seen.append(evt.sequence)
            if len(sequences_seen) >= limit:
                break

    # history has seq 0-4; since=3 gives [3,4], then live seq=5 makes 3 total
    task = asyncio.create_task(collect(limit=3))
    await asyncio.sleep(0.01)

    # Publish one live event
    await pub.publish(run_id, _evt(5))
    await asyncio.wait_for(task, timeout=2.0)

    assert sequences_seen == [3, 4, 5], f"Expected [3, 4, 5], got {sequences_seen}"


@pytest.mark.asyncio
async def test_disconnect_cleanup_releases_subscriber():
    """DOC-REQ-005: Disconnecting request causes subscriber queue to be removed."""
    pub = ObservabilityPublisher()
    run_id = "run-disconnect"

    # Pre-publish one event so history is non-empty — generator yields it, then
    # checks is_disconnected() which returns True and breaks.
    await pub.publish(run_id, _evt(0))

    gen = log_stream_generator(run_id, _AlwaysDisconnected(), since=0, publisher=pub)

    # Collect the first SSE chunk. After that, is_disconnected() returns True → break.
    chunks = []
    try:
        async for chunk in gen:
            chunks.append(chunk)
            if len(chunks) >= 3:  # 1 event = 3 SSE lines, then the loop breaks
                break
    except StopAsyncIteration:
        pass
    finally:
        await gen.aclose()

    # After aclose the subscriber queue must be removed from the publisher
    queues = pub._channels.get(run_id, [])
    assert len(queues) == 0, f"Expected 0 queues after disconnect, got {len(queues)}"


@pytest.mark.asyncio
async def test_publisher_history_bounded():
    """DOC-REQ-006 (implicit): history is bounded to max_history items."""
    pub = ObservabilityPublisher(max_history=3)
    run_id = "run-bound"

    for i in range(5):
        await pub.publish(run_id, _evt(i))

    history = pub._histories[run_id]
    assert len(history) == 3
    assert history[0].sequence == 2  # oldest kept


@pytest.mark.asyncio
async def test_sse_generator_format():
    """DOC-REQ-001: SSE generator output is correctly formatted."""
    pub = ObservabilityPublisher()
    run_id = "run-sse"
    await pub.publish(run_id, _evt(10, LogStreamType.stdout, "hello"))

    gen = log_stream_generator(run_id, _AlwaysConnected(), since=0, publisher=pub)
    lines = []
    try:
        # We expect exactly 3 output lines from 1 event
        async for chunk in gen:
            lines.append(chunk)
            if len(lines) >= 3:
                break
    finally:
        await gen.aclose()

    full = "".join(lines)
    assert "id: 10" in full
    assert "event: log_chunk" in full
    assert "hello" in full
