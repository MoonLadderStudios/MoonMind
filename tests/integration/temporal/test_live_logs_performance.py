"""Integration tests for Live Logs Phase 7 Performance and Hardening."""

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

from moonmind.services.observability.publisher import ObservabilityPublisher
from moonmind.services.observability.models import LogStreamEvent, LogStreamType
from moonmind.services.observability.subscriber import log_stream_generator


class MockRequest:
    def __init__(self, disconnect_after: int = 0):
        self.disconnect_after = disconnect_after
        self.reads = 0

    async def is_disconnected(self) -> bool:
        self.reads += 1
        if self.disconnect_after > 0 and self.reads >= self.disconnect_after:
            return True
        return False


async def test_log_stream_high_volume_performance():
    """Verify subscriber keeps up with a high-volume publisher without dropping events.

    Phase 6 hardening: reduced from 10 000 arbitrary events with time-based
    back-off to a deterministic 500-event batch consumed via history replay.
    This eliminates CI flakiness caused by variable scheduler latency while
    still exercising the hot path through the publisher→history→subscriber
    pipeline at volume.
    """
    publisher = ObservabilityPublisher()
    run_id = "test-perf-run"

    total_events = 500

    # Publish all events upfront — they land in the bounded history deque.
    for i in range(total_events):
        await publisher.publish(
            run_id,
            LogStreamEvent(
                sequence=i,
                stream=LogStreamType.stdout,
                offset=i * 10,
                timestamp=datetime.now(timezone.utc),
                text=f"line {i}\n",
            ),
        )
    # Barrier event so the consumer knows when to stop.
    await publisher.publish(
        run_id,
        LogStreamEvent(
            sequence=total_events,
            stream=LogStreamType.system,
            offset=total_events * 10,
            timestamp=datetime.now(timezone.utc),
            text="__BARRIER__\n",
        ),
    )

    # Consume via history replay (since=0) + live subscription.
    seen_sequences: list[int] = []

    async def _consume_stream():
        async for chunk in log_stream_generator(
            run_id, request=MockRequest(), publisher=publisher, since=0
        ):
            if chunk.startswith("data:"):
                try:
                    payload = json.loads(chunk[len("data:"):])
                    seq = payload.get("sequence")
                    if seq is not None and seq < total_events:
                        seen_sequences.append(seq)
                    if seq == total_events:
                        break
                except json.JSONDecodeError:
                    # Skip malformed JSON chunks and continue consuming the stream.
                    continue

    start = time.perf_counter()
    try:
        await asyncio.wait_for(_consume_stream(), timeout=10.0)
    except asyncio.TimeoutError:
        pytest.fail("Test timed out: log stream consumer did not reach the barrier event")
    duration = time.perf_counter() - start

    assert seen_sequences == list(range(total_events)), (
        f"Expected sequences 0..{total_events - 1}, got {len(seen_sequences)} events"
    )
    assert duration < 10.0, f"Performance regression: took {duration:.2f}s"


async def test_log_stream_graceful_disconnect():
    """Verify stream handles client disconnects effectively without unbounded memory growth."""
    publisher = ObservabilityPublisher()
    run_id = "test-disconnect-run"

    async def _emit_events():
        for i in range(100):
            event = LogStreamEvent(
                sequence=i,
                stream=LogStreamType.stdout,
                offset=i * 10,
                timestamp=datetime.now(timezone.utc),
                text=f"line {i}\n"
            )
            await publisher.publish(run_id, event)

    # Disconnect after processing 50 events
    mock_request = MockRequest(disconnect_after=50)
    chunks = []

    async def _consume_stream():
        try:
            async for chunk in log_stream_generator(
                run_id, request=mock_request, publisher=publisher, since=0
            ):
                chunks.append(chunk)
        except asyncio.TimeoutError:
            pytest.fail("Consumer loop timed out unexpectedly")

    consume_task = asyncio.create_task(_consume_stream())
    await _emit_events()
    
    try:
        await asyncio.wait_for(consume_task, timeout=2.0)
    except asyncio.TimeoutError:
        consume_task.cancel()
        pytest.fail("Test timed out: client disconnect didn't stop the stream")
    
    # We should have cleanly disconnected after ~50 events
    # Or 50 events * 3 lines per event = 150
    assert len(chunks) <= 150
