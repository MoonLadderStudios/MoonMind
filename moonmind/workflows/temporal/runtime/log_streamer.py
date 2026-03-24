"""Log streaming and diagnostics collection for managed runtime executions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from moonmind.workflows.temporal.runtime.output_parser import (
    ParsedOutput,
    PlainTextOutputParser,
    RuntimeOutputParser,
)



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
    ) -> tuple[str, str]:
        """Read an asyncio.StreamReader in chunks and write to an artifact file.

        Returns the storage-relative artifact reference and the decoded string content.
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
        return storage_ref, data.decode("utf-8", errors="replace")

    async def stream_and_parse(
        self,
        stdout_reader: asyncio.StreamReader | None,
        stderr_reader: asyncio.StreamReader | None,
        *,
        run_id: str,
        parser: RuntimeOutputParser | None = None,
    ) -> tuple[dict[str, str], str, str, ParsedOutput]:
        """Stream both stdout/stderr to artifacts and parse the output.

        Combines the two ``stream_to_artifact`` calls and runs the
        strategy's output parser over the decoded content.

        Returns ``(log_refs, stdout_content, stderr_content, parsed_output)``.
        """
        log_refs: dict[str, str] = {}
        stdout_content = ""
        stderr_content = ""

        if stdout_reader:
            ref, stdout_content = await self.stream_to_artifact(
                stdout_reader, run_id=run_id, stream_name="stdout",
            )
            log_refs["stdout"] = ref

        if stderr_reader:
            ref, stderr_content = await self.stream_to_artifact(
                stderr_reader, run_id=run_id, stream_name="stderr",
            )
            log_refs["stderr"] = ref

        effective_parser = parser or PlainTextOutputParser()
        parsed_output = effective_parser.parse(stdout_content, stderr_content)

        return log_refs, stdout_content, stderr_content, parsed_output

    def collect_diagnostics(
        self,
        *,
        run_id: str,
        exit_code: int | None,
        duration_seconds: float,
        log_refs: dict[str, str],
        parsed_output: ParsedOutput | None = None,
    ) -> str:
        """Write a diagnostics JSON bundle and return the storage reference."""
        diagnostics: dict[str, Any] = {
            "exit_code": exit_code,
            "duration_seconds": duration_seconds,
            "log_refs": log_refs,
        }
        if parsed_output is not None:
            diagnostics["parsed_output"] = {
                "has_structured_output": parsed_output.has_structured_output,
                "event_count": len(parsed_output.events),
                "error_messages": parsed_output.error_messages,
                "rate_limited": parsed_output.rate_limited,
            }
        data = json.dumps(diagnostics, indent=2).encode("utf-8")
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name="diagnostics.json",
            data=data,
        )
        return storage_ref
