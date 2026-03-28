"""Log streaming and diagnostics collection for managed runtime executions."""

from __future__ import annotations

import asyncio
import json
import inspect
from typing import Any

from moonmind.workflows.temporal.runtime.output_parser import (
    ParsedOutput,
    PlainTextOutputParser,
    RuntimeOutputParser,
)

# Read chunks of 64KB at a time.  Larger than readline()'s default 64KB
# *per-line* limit but without any per-line restriction, so processes that
# produce long lines (base64, JSON blobs, our large-output tests) never
# trigger LimitOverrunError.
_STREAM_CHUNK_SIZE = 65536


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
        event_callback: Any | None = None,
    ) -> tuple[str, str, list[dict]]:
        """Read an asyncio.StreamReader in fixed-size chunks and write to an artifact file.

        Uses ``reader.read(_STREAM_CHUNK_SIZE)`` instead of ``readline()`` to
        avoid the 64KB-per-line limit imposed by asyncio's StreamReader.

        When a *parser* is provided, decoded text is line-buffered before being
        passed to ``parser.parse_stream_chunk()`` so that the parser always
        receives complete newline-delimited lines — matching the contract
        expected by ``NdjsonOutputParser`` and similar line-oriented parsers.

        Returns the storage-relative artifact reference, the decoded string content,
        and a list of structured events extracted during streaming if a parser is provided.
        """
        chunks: list[bytes] = []
        events: list[dict] = []
        # Carry-over buffer for incomplete lines when the parser is active.
        _line_buf: str = ""

        while True:
            chunk = await reader.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
            if parser is not None:
                # Accumulate decoded text and split by newlines so that the
                # parser always receives whole lines, not raw read() chunks
                # that may straddle a newline boundary.
                decoded_chunk = chunk.decode("utf-8", errors="replace")
                _line_buf += decoded_chunk
                *complete_lines, _line_buf = _line_buf.split("\n")
                for line in complete_lines:
                    line_with_nl = line + "\n"
                    parsed_events = parser.parse_stream_chunk(line_with_nl)
                    events.extend(parsed_events)
                    if parsed_events and event_callback is not None:
                        callback_result = event_callback(parsed_events)
                        if inspect.isawaitable(callback_result):
                            await callback_result

        # Flush any remaining partial line to the parser (no trailing newline).
        if parser is not None and _line_buf:
            parsed_events = parser.parse_stream_chunk(_line_buf)
            events.extend(parsed_events)
            if parsed_events and event_callback is not None:
                callback_result = event_callback(parsed_events)
                if inspect.isawaitable(callback_result):
                    await callback_result

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
        event_callback: Any | None = None,
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
                self.stream_to_artifact(
                    stdout_reader,
                    run_id=run_id,
                    stream_name="stdout",
                    parser=parser,
                    event_callback=event_callback,
                )
            )
            if stdout_reader
            else None
        )
        stderr_task = (
            asyncio.create_task(
                self.stream_to_artifact(
                    stderr_reader,
                    run_id=run_id,
                    stream_name="stderr",
                    parser=parser,
                    event_callback=event_callback,
                )
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
