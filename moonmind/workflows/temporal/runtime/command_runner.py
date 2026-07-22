"""Shared bounded subprocess execution for trusted runtime adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from moonmind.utils.logging import redact_sensitive_text


async def run_runtime_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    output_limit_bytes: int | None = None,
) -> tuple[int, bytes, bytes]:
    """Run one trusted command with cancellation, timeout, redaction, and bounds.

    The caller retains command-specific error classification.  This shared layer
    owns process termination so cancelled or timed-out activities cannot leave a
    CLI child running, and ensures retained command output is safe for evidence.
    """

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )
    try:
        communication = process.communicate()
        if timeout_seconds is None:
            stdout, stderr = await communication
        else:
            stdout, stderr = await asyncio.wait_for(
                communication, timeout=timeout_seconds
            )
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise

    def sanitize(payload: bytes) -> bytes:
        text = redact_sensitive_text(payload.decode("utf-8", errors="replace"))
        encoded = text.encode("utf-8")
        if output_limit_bytes is not None:
            return encoded[: max(0, output_limit_bytes)]
        return encoded

    return int(process.returncode or 0), sanitize(stdout), sanitize(stderr)


__all__ = ["run_runtime_command"]
