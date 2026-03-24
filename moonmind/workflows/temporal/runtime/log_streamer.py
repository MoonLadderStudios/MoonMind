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

        # Stream stdout and stderr concurrently to avoid buffer-related
        # issues and performance regressions on heavy output processes.
        stdout_task = (
            asyncio.create_task(
                self.stream_to_artifact(stdout_reader, run_id=run_id, stream_name="stdout", parser=parser)
            )
            if stdout_reader
            else None
        )
        stderr_task = (
            asyncio.create_task(
                self.stream_to_artifact(stderr_reader, run_id=run_id, stream_name="stderr", parser=parser)
            )
            if stderr_reader
            else None
        )

        events: list[dict] = []
        if stdout_task:
            ref, stdout_content, stdout_events = await stdout_task
            log_refs["stdout"] = ref
            events.extend(stdout_events)

        if stderr_task:
            ref, stderr_content, stderr_events = await stderr_task
            log_refs["stderr"] = ref
            events.extend(stderr_events)

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
