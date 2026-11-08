"""Shared bounded subprocess execution for trusted runtime adapters."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence

from moonmind.utils.logging import command_secret_continuation, redact_command_output


async def run_runtime_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout_seconds: float | None = None,
    output_limit_bytes: int | None = None,
    on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    binary_stdout_limit_bytes: int | None = None,
) -> tuple[int, bytes, bytes]:
    """Run one trusted command with cancellation, timeout, redaction, and bounds.

    The caller retains command-specific error classification.  This shared layer
    owns process termination so cancelled or timed-out activities cannot leave a
    CLI child running, and ensures retained command output is safe for evidence.
    Bounded binary stdout is only for parsers of framed trusted data; callers
    must validate it and sanitize parsed diagnostics before publication. It is
    never streamed to an output callback. Stderr remains redacted text.
    """

    if binary_stdout_limit_bytes is not None:
        if type(binary_stdout_limit_bytes) is not int or binary_stdout_limit_bytes <= 0:
            raise ValueError("binary stdout requires a positive byte bound")
        if on_output is not None:
            raise ValueError("binary stdout cannot be streamed as diagnostic text")
        if output_limit_bytes is None:
            output_limit_bytes = 65536

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=(asyncio.subprocess.PIPE if input_bytes is not None else None),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )

    def sanitize(payload: bytes) -> bytes:
        text = redact_command_output(payload.decode("utf-8", errors="replace"))
        encoded = text.encode("utf-8")
        if output_limit_bytes is not None:
            return encoded[: max(0, output_limit_bytes)]
        return encoded

    def retain(retained: bytearray, safe: bytes) -> None:
        # Streamed output keeps its tail: a long-running command reports its
        # terminal error last, after any amount of progress output.
        retained.extend(safe)
        if output_limit_bytes is not None and len(retained) > output_limit_bytes:
            del retained[: len(retained) - max(0, output_limit_bytes)]
            # Drop a split UTF-8 sequence so re-sanitizing cannot grow the tail.
            while retained and 0x80 <= retained[0] <= 0xBF:
                del retained[0]

    async def read_stream(name: str, reader: asyncio.StreamReader) -> bytes:
        retained = bytearray()
        pending = bytearray()
        frame = bytearray()
        json_record = False
        continuation = None
        withheld = False
        limit = 65536
        omission = b"[streamed output withheld: incomplete or excessive record]\n"

        async def publish(safe: bytes) -> None:
            retain(retained, safe)
            try:
                if on_output is not None:
                    await on_output(name, safe)
            except Exception:
                pass  # Auxiliary evidence cannot change command success.

        async def consume(line: bytes, *, eof: bool = False) -> None:
            nonlocal json_record, withheld, continuation
            if len(frame) + len(line) > limit:
                # Once the context is lost, even a later ordinary-looking
                # line could be a secret body. Drain without retaining it.
                withheld = True
                frame.clear()
                await publish(sanitize(omission))
                return
            frame.extend(line)
            line_text = line.decode("utf-8", errors="replace")
            if (
                ((json_record or continuation is not None) and not line_text.strip())
                or (
                    continuation not in {None, "\n"}
                    and continuation.casefold() not in line_text.casefold()
                )
            ):
                # Whitespace cannot finish an already-open credential or JSON
                # record. Likewise, quoted/key bodies need their delimiter.
                # Retain these lines without rescanning the growing frame.
                if eof:
                    await publish(sanitize(omission))
                    frame.clear()
                return
            text = frame.decode("utf-8", errors="replace")
            incomplete_json = False
            complete_json = False
            if json_record or text.lstrip().startswith(("{", "[")):
                try:
                    json.loads(text)
                    complete_json = True
                except json.JSONDecodeError as error:
                    # Bracketed progress such as '[build] running' is not a
                    # partial JSON record and must be delivered promptly.
                    incomplete_json = (
                        json_record
                        or error.pos >= len(text.rstrip())
                        or error.msg.startswith("Unterminated string")
                    )
                except (ValueError, RecursionError):
                    pass  # The canonical redactor fails closed on its bounds.
            json_record = incomplete_json
            # Complete JSON belongs wholly to the structured visitor, including
            # strings whose contents merely resemble unfinished credentials.
            continuation = None if complete_json else command_secret_continuation(text)
            if incomplete_json or continuation is not None:
                if eof:
                    await publish(sanitize(omission))
                    frame.clear()
                return
            await publish(sanitize(bytes(frame)))
            frame.clear()

        while chunk := await reader.read(8192):
            if withheld:
                continue
            pending.extend(chunk)
            while b"\n" in pending and not withheld:
                line, _, remaining = pending.partition(b"\n")
                pending = bytearray(remaining)
                await consume(bytes(line) + b"\n")
            if withheld or len(frame) + len(pending) > limit:
                pending.clear()
                frame.clear()
                if not withheld:
                    withheld = True
                    await publish(sanitize(omission))
        if not withheld and (pending or frame):
            await consume(bytes(pending), eof=True)
        return bytes(retained)

    async def communicate_progress() -> tuple[bytes, bytes]:
        if process.stdin is not None:
            process.stdin.write(input_bytes or b"")
            await process.stdin.drain()
            process.stdin.close()
        stdout, stderr = await asyncio.gather(
            read_stream("stdout", process.stdout), read_stream("stderr", process.stderr)
        )
        await process.wait()
        return stdout, stderr

    async def read_binary_stdout() -> bytes:
        retained = bytearray()
        while chunk := await process.stdout.read(8192):
            if len(retained) + len(chunk) > binary_stdout_limit_bytes:
                raise RuntimeError("binary stdout exceeds its byte bound")
            retained.extend(chunk)
        return bytes(retained)

    async def communicate_binary() -> tuple[bytes, bytes]:
        if process.stdin is not None:
            process.stdin.write(input_bytes or b"")
            await process.stdin.drain()
            process.stdin.close()
        readers = [
            asyncio.create_task(read_binary_stdout()),
            asyncio.create_task(read_stream("stderr", process.stderr)),
        ]
        try:
            stdout, stderr = await asyncio.gather(*readers)
            await process.wait()
            return stdout, stderr
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    communication_task = asyncio.create_task(
        communicate_binary()
        if binary_stdout_limit_bytes is not None
        else process.communicate(input=input_bytes)
        if on_output is None
        else communicate_progress()
    )
    try:
        communication = communication_task
        if timeout_seconds is None:
            stdout, stderr = await communication
        else:
            stdout, stderr = await asyncio.wait_for(
                communication, timeout=timeout_seconds
            )
    except (TimeoutError, asyncio.CancelledError, RuntimeError):
        if process.returncode is None:
            process.kill()
        communication_task.cancel()
        await asyncio.gather(communication_task, return_exceptions=True)

        # A full pipe can keep asyncio's process transport paused after kill.
        # Join readers first, then discard remaining bytes before waiting; none
        # of these failure-path bytes are retained or published as diagnostics.
        async def discard(reader):
            while await reader.read(8192):
                pass

        await asyncio.gather(discard(process.stdout), discard(process.stderr))
        await process.wait()
        raise

    return (
        int(process.returncode or 0),
        stdout if binary_stdout_limit_bytes is not None else sanitize(stdout),
        sanitize(stderr),
    )


__all__ = ["run_runtime_command"]
