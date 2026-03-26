"""Managed process lifecycle control utilities.

Provides helpers for graceful process cancellation following the
SIGTERM → grace period → SIGKILL pattern.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

logger = logging.getLogger(__name__)

DEFAULT_GRACE_SECONDS: float = 2.0


async def cancel_managed_process(
    process: asyncio.subprocess.Process,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> int | None:
    """Cancel a managed subprocess gracefully.

    1. Send SIGTERM (or ``terminate()`` on Windows).
    2. Wait up to *grace_seconds* for the process to exit.
    3. If still running, send SIGKILL (or ``kill()`` on Windows).

    Returns the process exit code, or ``None`` if the process could not be
    reaped within the grace period after SIGKILL.
    """
    if process.returncode is not None:
        logger.debug(
            "Process pid=%s already exited with code=%s",
            process.pid,
            process.returncode,
        )
        return process.returncode

    # Step 1: SIGTERM
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        logger.info(
            "Sent SIGTERM to process pid=%s, grace_seconds=%.1f",
            process.pid,
            grace_seconds,
        )
    except ProcessLookupError:
        logger.debug("Process pid=%s already gone before SIGTERM", process.pid)
        return process.returncode

    # Step 2: Wait for grace period.
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        logger.info(
            "Process pid=%s exited gracefully with code=%s",
            process.pid,
            process.returncode,
        )
        return process.returncode
    except asyncio.TimeoutError:
        pass

    # Step 3: SIGKILL
    try:
        process.kill()
        logger.warning(
            "Sent SIGKILL to process pid=%s after %.1fs grace timeout",
            process.pid,
            grace_seconds,
        )
    except ProcessLookupError:
        logger.debug("Process pid=%s already gone before SIGKILL", process.pid)
        return process.returncode

    # Brief wait for SIGKILL to take effect.
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.error(
            "Process pid=%s did not exit after SIGKILL", process.pid
        )
        return None

    return process.returncode
