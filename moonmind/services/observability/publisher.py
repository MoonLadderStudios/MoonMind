import asyncio
import uuid
from typing import Dict, List, Optional, AsyncGenerator

from .models import LogStreamEvent


class ObservabilityPublisher:
    """
    In-memory publisher managing pub/sub channels for active managed runs.
    Maintains a bounded history buffer to allow graceful reconnects via `since`.
    """

    def __init__(self, max_history: int = 5000):
        self._channels: Dict[str, List[asyncio.Queue[LogStreamEvent]]] = {}
        self._histories: Dict[str, List[LogStreamEvent]] = {}
        self.max_history = max_history

    def _ensure_run(self, run_id: str):
        if run_id not in self._channels:
            self._channels[run_id] = []
            self._histories[run_id] = []

    async def publish(self, run_id: str, event: LogStreamEvent) -> None:
        """Fans out an event to all subscribers and appends it to history."""
        self._ensure_run(run_id)
        
        # Maintain history bound
        history = self._histories[run_id]
        history.append(event)
        if len(history) > self.max_history:
            history.pop(0)

        # Fan out
        for q in self._channels[run_id]:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
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
            if not self._channels[run_id] and not self._histories[run_id]:
                self._channels.pop(run_id, None)

# Default global instance
publisher = ObservabilityPublisher()
