import asyncio
from collections import deque
from typing import Dict, Optional, AsyncGenerator

from .models import LogStreamEvent


class ObservabilityPublisher:
    """
    In-memory publisher managing pub/sub channels for active managed runs.
    Maintains a bounded history buffer to allow graceful reconnects via `since`.
    """

    def __init__(self, max_history: int = 5000):
        self._channels: Dict[str, list[asyncio.Queue[LogStreamEvent]]] = {}
        self._histories: Dict[str, deque[LogStreamEvent]] = {}
        self.max_history = max_history

    def _ensure_run(self, run_id: str):
        if run_id not in self._channels:
            self._channels[run_id] = []
            self._histories[run_id] = deque(maxlen=self.max_history)

    async def publish(self, run_id: str, event: LogStreamEvent) -> None:
        """Fans out an event to all subscribers and appends it to history."""
        self._ensure_run(run_id)

        # Maintain history bound via deque maxlen (O(1) append + automatic eviction)
        self._histories[run_id].append(event)

        # Fan out - iterate over shallow copy to avoid mutation during iteration
        for q in list(self._channels[run_id]):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Intentionally drop event for slow subscriber when queue is full to avoid blocking.
                pass

    async def subscribe(self, run_id: str, since: Optional[int] = None) -> AsyncGenerator[LogStreamEvent, None]:
        """
        Yields events for a specific run ID.
        If `since` is provided, fetches history elements >= `since` before yielding live elements.
        """
        self._ensure_run(run_id)

        # Fast-forward history if needed
        if since is not None:
            for past_event in self._histories[run_id]:
                if past_event.sequence >= since:
                    yield past_event

        # Subscribe to live updates
        q: asyncio.Queue[LogStreamEvent] = asyncio.Queue(maxsize=1000)
        self._channels[run_id].append(q)

        try:
            while True:
                event = await q.get()
                yield event
        finally:
            if q in self._channels[run_id]:
                self._channels[run_id].remove(q)
            # Clean up run state when last subscriber leaves
            if not self._channels[run_id]:
                self._channels.pop(run_id, None)
                self._histories.pop(run_id, None)

# Default global instance
publisher = ObservabilityPublisher()
