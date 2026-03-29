"""Integration tests for Live Logs Phase 7 Performance and Hardening."""

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

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
    """Simulate a publisher emitting 10,000 logs and verify subscriber yields them promptly."""
    publisher = ObservabilityPublisher()
    run_id = "test-perf-run"

    # Seed the publisher loop with events
    async def _emit_events():
        for i in range(10000):
            event = LogStreamEvent(
                sequence=i,
                stream=LogStreamType.stdout,
                offset=i * 10,
                timestamp=datetime.now(timezone.utc),
                text=f"line {i}\n"
            )
            await publisher.publish(run_id, event)
            if i % 100 == 0:
                # Yield control to prevent the subscriber's 1000-item queue from filling and dropping events
                await asyncio.sleep(0.001)
        # Give subscribers time to receive everything
        await asyncio.sleep(0.5)

    # Subscribe and read
    mock_request = MockRequest()
    chunks = []

    async def _consume_stream():
        try:
            async for chunk in log_stream_generator(
                run_id, request=mock_request, publisher=publisher
            ):
                chunks.append(chunk)
                if chunk.startswith("data:"):
                    try:
                        payload = json.loads(chunk[len("data:"):])
                        if payload.get("sequence") == 9999:
                            break
                    except json.JSONDecodeError:
                        # Intentionally ignore malformed/incomplete JSON data frames expected from SSE chunking
                        pass
        except asyncio.TimeoutError:
            pytest.fail("Consumer loop timed out unexpectedly")

    # Run publisher and consumer concurrently
    start = time.perf_counter()
    consume_task = asyncio.create_task(_consume_stream())
    await _emit_events()
    
    try:
        await asyncio.wait_for(consume_task, timeout=2.0)
    except asyncio.TimeoutError:
        consume_task.cancel()
        pytest.fail("Test timed out before receiving expected sequence")
    
    duration = time.perf_counter() - start

    assert len(chunks) > 0
    assert duration < 5.0  # Realistically should take way less than 5 seconds


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
