"""Shared bounded subprocess execution for trusted runtime adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

from moonmind.utils.logging import redact_sensitive_text


async def run_runtime_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout_seconds: float | None = None,
    output_limit_bytes: int | None = None,
    on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> tuple[int, bytes, bytes]:
    """Run one trusted command with cancellation, timeout, redaction, and bounds.

    The caller retains command-specific error classification.  This shared layer
    owns process termination so cancelled or timed-out activities cannot leave a
    CLI child running, and ensures retained command output is safe for evidence.
    """

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=(asyncio.subprocess.PIPE if input_bytes is not None else None),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )

    def sanitize(payload: bytes) -> bytes:
        text = redact_sensitive_text(payload.decode("utf-8", errors="replace"))
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
        oversized = False
        while chunk := await reader.read(8192):
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, remaining = pending.partition(b"\n")
                pending = bytearray(remaining)
                # Never publish a fragment of an oversized line: splitting a
                # credential across fragments would defeat text redaction.
                safe = (
                    b"[oversized output line omitted]\n"
                    if oversized or len(line) > 65536
                    else sanitize(bytes(line) + b"\n")
                )
                oversized = False
                retain(retained, safe)
                try:
                    await on_output(name, safe)
                except Exception:
                    pass  # Auxiliary evidence cannot change command success.
            if len(pending) > 65536:
                pending.clear()
                oversized = True
        if pending or oversized:
            safe = (
                b"[oversized output line omitted]\n"
                if oversized
                else sanitize(bytes(pending))
            )
            retain(retained, safe)
            try:
                await on_output(name, safe)
            except Exception:
                pass  # Auxiliary evidence cannot change command success.
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

    communication_task = asyncio.create_task(
        process.communicate(input=input_bytes)
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
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        communication_task.cancel()
        await asyncio.gather(communication_task, return_exceptions=True)
        raise

    return int(process.returncode or 0), sanitize(stdout), sanitize(stderr)


__all__ = ["run_runtime_command"]
