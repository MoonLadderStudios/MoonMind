"""Live log cross-process transport mechanisms."""

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from moonmind.schemas.agent_runtime_models import RunObservabilityEvent
from moonmind.utils.workspace_paths import append_text, open_regular_file

logger = logging.getLogger(__name__)

class SpoolLogPublisher:
    """Publishes live log chunks by appending them to a workspace spool file."""

    def __init__(self, workspace_path: str, filename: str = "live_streams.spool") -> None:
        self._spool_path = Path(workspace_path) / filename

    def publish(self, chunk: RunObservabilityEvent) -> None:
        """Append a JSON-serialized observability event to the spool file."""
        payload = chunk.model_dump_json(by_alias=True, exclude_none=True)
        append_text(self._spool_path, payload + "\n")

class SpoolLogReader:
    """Consumes live log chunks by tailing the spool file."""

    def __init__(self, workspace_path: str, filename: str = "live_streams.spool") -> None:
        self._spool_path = Path(workspace_path) / filename
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Signal the tailing loop to stop at the next iteration."""
        self._stop_event.set()

    async def follow(
        self,
        since_sequence: int = 0,
        *,
        start_at_end: bool = False,
    ) -> AsyncIterator[RunObservabilityEvent]:
        """Asynchronously follow the spool file, yielding new chunks.

        If since_sequence is provided, any chunk with sequence <= since_sequence
        will be silently skipped.
        """
        # Opening the same pinned file as the writer preserves normal append
        # and replay behavior without following an agent-controlled link.
        while True:
            try:
                with open_regular_file(self._spool_path) as stream:
                    if start_at_end and since_sequence <= 0:
                        stream.seek(0, 2)
                    pending = b""
                    while True:
                        line = stream.readline()
                        if not line:
                            if self._stop_event.is_set():
                                return
                            await asyncio.sleep(0.05)
                            continue
                        pending += line
                        if not pending.endswith(b"\n"):
                            continue
                        complete, pending = pending, b""
                        try:
                            payload = json.loads(
                                complete.decode("utf-8", errors="replace")
                            )
                            chunk = RunObservabilityEvent.model_validate(payload)
                        except (ValueError, TypeError):
                            # Ignore a corrupt complete record, not a partial append.
                            continue
                        if chunk.sequence > since_sequence:
                            yield chunk
            except FileNotFoundError:
                # The writer may not have created the file yet.
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(0.05)
            except OSError:
                logger.warning(
                    "Live log spool is unavailable or unsafe: %s", self._spool_path
                )
                return
