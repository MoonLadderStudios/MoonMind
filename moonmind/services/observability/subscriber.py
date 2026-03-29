from typing import AsyncGenerator, Optional

from fastapi import Request

from moonmind.services.observability.publisher import publisher as _default_publisher, ObservabilityPublisher
from moonmind.services.observability.models import LogStreamEvent


async def log_stream_generator(
    run_id: str,
    request: Request,
    since: Optional[int] = None,
    publisher: Optional[ObservabilityPublisher] = None,
) -> AsyncGenerator[str, None]:
    """
    Subscribes to a task run's log stream publisher and yields SSE-formatted strings.
    Handles disconnection events cleanly pulling the subscriber queue from memory.

    Args:
        run_id: UUID string for the task run.
        request: FastAPI Request object — used to detect client disconnects.
        since: Optional sequence number to resume from history.
        publisher: Optionally inject an ObservabilityPublisher (for testing).
    """
    pub = publisher or _default_publisher

    async for event in pub.subscribe(run_id, since=since):
        if await request.is_disconnected():
            break

        # SSE Format — three lines per event
        yield f"id: {event.sequence}\n"
        yield f"event: log_chunk\n"
        yield f"data: {event.model_dump_json()}\n\n"
