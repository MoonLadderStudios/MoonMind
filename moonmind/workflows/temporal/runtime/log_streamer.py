"""Log streaming and diagnostics collection for managed runtime executions."""

from __future__ import annotations

import asyncio
import json
import inspect
from typing import Any

from datetime import datetime, timezone

from moonmind.schemas.agent_runtime_models import LiveLogChunk
from moonmind.observability.transport import SpoolLogPublisher
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

    def __init__(self, artifact_storage: Any, publisher: Any | None = None) -> None:
        self._storage = artifact_storage
        self.publisher = publisher
        self._sequence_counter = 0
        self._annotations_by_run: dict[str, list[dict[str, Any]]] = {}

    def _next_sequence(self) -> int:
        self._sequence_counter += 1
        return self._sequence_counter

    async def stream_to_artifact(
        self,
        reader: asyncio.StreamReader,
        *,
        run_id: str,
        stream_name: str,
        workspace_path: str | None = None,
        parser: RuntimeOutputParser | None = None,
        chunk_callback: Any | None = None,
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
        current_offset: int = 0
        live_publisher = self.publisher
        if live_publisher is None and workspace_path:
            live_publisher = SpoolLogPublisher(workspace_path=workspace_path)

        while True:
            chunk = await reader.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            
            chunk_length = len(chunk)
            chunks.append(chunk)
            text_content = chunk.decode("utf-8", errors="replace")
            if chunk_callback is not None:
                callback_result = chunk_callback(stream_name, text_content)
                if inspect.isawaitable(callback_result):
                    await callback_result
            
            if live_publisher is not None:
                try:
                    obj = LiveLogChunk(
                        sequence=self._next_sequence(),
                        stream=stream_name,  # type: ignore
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        text=text_content,
                        offset=current_offset,
                    )
                    live_publisher.publish(obj)
                except Exception:
                    # Failsafe: publishers must not crash the workflow
                    pass
            
            current_offset += chunk_length

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
                            _ = await callback_result

        # Flush any remaining partial line to the parser (no trailing newline).
        if parser is not None and _line_buf:
            parsed_events = parser.parse_stream_chunk(_line_buf)
            events.extend(parsed_events)
            if parsed_events and event_callback is not None:
                callback_result = event_callback(parsed_events)
                if inspect.isawaitable(callback_result):
                    _ = await callback_result

        data = b"".join(chunks)
        artifact_name = f"{stream_name}.log"
        _, storage_ref = self._storage.write_artifact(
            job_id=run_id,
            artifact_name=artifact_name,
            data=data,
        )
        return storage_ref, data.decode("utf-8", errors="replace"), events

    def emit_system_annotation(
        self,
        *,
        run_id: str,
        workspace_path: str | None,
        text: str,
        metadata: dict[str, Any] | None = None,
        annotation_type: str | None = None,
    ) -> None:
        """Emit a MoonMind-owned system event into the shared sequence space."""
        if metadata is None:
            metadata = {}
        text = str(text or "").strip()
        if not text:
            return

        sequence = self._next_sequence()
        annotation_record = {
            "annotation_type": annotation_type or metadata.get("annotation_type", "system"),
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "metadata": metadata,
        }
        self._annotations_by_run.setdefault(run_id, []).append(annotation_record)

        live_publisher = self.publisher
        if live_publisher is None and workspace_path:
            live_publisher = SpoolLogPublisher(workspace_path=workspace_path)

        if live_publisher is None:
            return

        try:
            obj = LiveLogChunk(
                sequence=sequence,
                stream="system",
                timestamp=datetime.now(timezone.utc).isoformat(),
                text=text,
                offset=None,
            )
            live_publisher.publish(obj)
        except Exception:
            # Failures in observability publishing are non-fatal for runtime control.
            return

    def consume_annotations(self, run_id: str) -> list[dict[str, Any]]:
        """Return and clear persisted in-memory supervisor annotations for a run."""
        annotations = self._annotations_by_run.pop(run_id, None)
        return list(annotations or [])

    async def stream_and_parse(
        self,
        stdout_reader: asyncio.StreamReader | None,
        stderr_reader: asyncio.StreamReader | None,
        *,
        run_id: str,
        workspace_path: str | None = None,
        parser: RuntimeOutputParser | None = None,
        chunk_callback: Any | None = None,
        event_callback: Any | None = None,
    ) -> tuple[dict[str, str], str, str, ParsedOutput, list[dict]]:
        """Stream both stdout/stderr to artifacts and parse the output.

        Combines the two ``stream_to_artifact`` calls and runs the
        strategy's output parser over the decoded content.

        Returns ``(log_refs, stdout_content, stderr_content, parsed_output, events)``.
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
                    workspace_path=workspace_path,
                    parser=parser,
                    chunk_callback=chunk_callback,
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
                    workspace_path=workspace_path,
                    parser=parser,
                    chunk_callback=chunk_callback,
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

        return log_refs, stdout_content, stderr_content, parsed_output, events

    def collect_diagnostics(
        self,
        *,
        run_id: str,
        exit_code: int | None,
        duration_seconds: float,
        log_refs: dict[str, str],
        annotations: list[dict] | None = None,
        parsed_output: ParsedOutput | None = None,
        events: list[dict] | None = None,
    ) -> str:
        """Write a diagnostics JSON bundle and return the storage reference."""
        diagnostics: dict[str, Any] = {
            "exit_code": exit_code,
            "duration_seconds": duration_seconds,
            "log_refs": log_refs,
        }
        if annotations is not None:
            diagnostics["annotations"] = annotations
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
