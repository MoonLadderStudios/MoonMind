"""Log streaming and diagnostics collection for managed runtime executions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from moonmind.workflows.temporal.runtime.output_parser import RuntimeOutputParser


class RuntimeLogStreamer:
    """Streams subprocess output to artifact storage and collects diagnostics."""

    CHUNK_SIZE = 64 * 1024  # 64KB

    def __init__(self, artifact_storage: Any) -> None:
        self._storage = artifact_storage

    async def stream_to_artifact(
        self,
        reader: asyncio.StreamReader,
        *,
        run_id: str,
        stream_name: str,
        parser: RuntimeOutputParser | None = None,
    ) -> tuple[str, str, list[dict]]:
        """Read an asyncio.StreamReader line by line and write to an artifact file.

        Returns the storage-relative artifact reference, the decoded string content,
        and a list of structured events extracted during streaming if a parser is provided.
        """
        chunks: list[bytes] = []
        events: list[dict] = []

        while True:
            line = await reader.readline()
            if not line:
                break
            chunks.append(line)
            if parser is not None:
                decoded_line = line.decode("utf-8", errors="replace")
                parsed_events = parser.parse_stream_chunk(decoded_line)
                events.extend(parsed_events)

        data = b"".join(chunks)
        artifact_name = f"{stream_name}.log"
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name=artifact_name,
            data=data,
        )
        return storage_ref, data.decode("utf-8", errors="replace"), events

    def collect_diagnostics(
        self,
        *,
        run_id: str,
        exit_code: int | None,
        duration_seconds: float,
        log_refs: dict[str, str],
        events: list[dict] | None = None,
    ) -> str:
        """Write a diagnostics JSON bundle and return the storage reference."""
        diagnostics: dict[str, Any] = {
            "exit_code": exit_code,
            "duration_seconds": duration_seconds,
            "log_refs": log_refs,
        }
        if events is not None:
            diagnostics["events"] = events

        data = json.dumps(diagnostics, indent=2).encode("utf-8")
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name="diagnostics.json",
            data=data,
        )
        return storage_ref
