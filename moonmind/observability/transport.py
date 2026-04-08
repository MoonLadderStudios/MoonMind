"""Live log cross-process transport mechanisms."""

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from moonmind.schemas.agent_runtime_models import RunObservabilityEvent


class SpoolLogPublisher:
    """Publishes live log chunks by appending them to a workspace spool file."""

    def __init__(self, workspace_path: str, filename: str = "live_streams.spool") -> None:
        self._spool_path = Path(workspace_path) / filename
        # Ensure the filename is absolute or relative to a known path.
        self._spool_path.parent.mkdir(parents=True, exist_ok=True)

    def publish(self, chunk: RunObservabilityEvent) -> None:
        """Append a JSON-serialized observability event to the spool file."""
        payload = chunk.model_dump_json(by_alias=True, exclude_none=True)
        # Using open with 'a' guarantees O_APPEND semantics.
        # This allows multiple writers (if any) to safely append on POSIX,
        # but in our architecture, only the single supervisor writes to it.
        with open(self._spool_path, "a", encoding="utf-8") as f:
            f.write(payload + "\n")


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
        # It's possible the writer hasn't created the file yet.
        while not self._spool_path.exists():
            if self._stop_event.is_set():
                return
            await asyncio.sleep(0.05)

        with open(self._spool_path, "r", encoding="utf-8", errors="replace") as f:
            if start_at_end and since_sequence <= 0:
                f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(0.05)
                    continue

                line = line.strip()
                if not line:
                    continue

                try:
                    payload = json.loads(line)
                    chunk = RunObservabilityEvent.model_validate(payload)
                except Exception:
                    # Ignore corrupted line
                    continue

                if chunk.sequence <= since_sequence:
                    continue

                yield chunk
