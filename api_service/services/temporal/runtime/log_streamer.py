"""Log streaming and diagnostics collection for managed runtime executions."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Union

from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage


class RuntimeLogStreamer:
    """Streams subprocess output to artifact storage and collects diagnostics."""

    CHUNK_SIZE = 64 * 1024  # 64KB

    def __init__(self, artifact_storage: AgentQueueArtifactStorage) -> None:
        self._storage = artifact_storage

    async def stream_to_artifact(
        self,
        reader: asyncio.StreamReader,
        *,
        run_id: str,
        stream_name: str,
    ) -> str:
        """Read an asyncio.StreamReader in chunks and write to an artifact file.

        Returns the storage-relative artifact reference.
        """
        chunks: list[bytes] = []
        while True:
            chunk = await reader.read(self.CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)

        data = b"".join(chunks)
        artifact_name = f"{stream_name}.log"
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name=artifact_name,
            data=data,
        )
        return storage_ref

    def collect_diagnostics(
        self,
        *,
        run_id: str,
        exit_code: int | None,
        duration_seconds: float,
        log_refs: dict[str, str],
    ) -> str:
        """Write a diagnostics JSON bundle and return the storage reference."""
        diagnostics: dict[str, Any] = {
            "exit_code": exit_code,
            "duration_seconds": duration_seconds,
            "log_refs": log_refs,
        }
        data = json.dumps(diagnostics, indent=2).encode("utf-8")
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name="diagnostics.json",
            data=data,
        )
        return storage_ref
