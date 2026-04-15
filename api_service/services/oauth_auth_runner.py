"""Shared OAuth auth runner lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def stop_auth_runner_container(
    *,
    session_id: str,
    container_name: str | None,
) -> dict[str, object]:
    """Stop and remove the short-lived OAuth auth runner container."""
    if not container_name:
        logger.info("No container to stop for session %s", session_id)
        return {"session_id": session_id, "stopped": False, "reason": "no_container"}

    try:
        stop_proc = await asyncio.create_subprocess_exec(
            "docker",
            "stop",
            "-t",
            "5",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(stop_proc.communicate(), timeout=30)
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as exc:
        logger.warning("Failed to stop container %s: %s", container_name, exc)

    try:
        rm_proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(rm_proc.communicate(), timeout=15)
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as exc:
        logger.warning("Failed to remove container %s: %s", container_name, exc)

    logger.info(
        "Stopped auth runner container %s for session %s",
        container_name,
        session_id,
    )
    return {
        "session_id": session_id,
        "stopped": True,
        "container_name": container_name,
    }
