"Terminal PTY bridge startup logic."

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

async def start_terminal_bridge_container(
    session_id: str,
    runtime_id: str,
    volume_ref: str,
    volume_mount_path: str,
    session_ttl: int,
) -> dict[str, Any]:
    """Start an auth container that exposes a bridge for PTY websocket connections."""
    
    # In a real implementation, this would spin up a specialized docker 
    # container that accepts a websocket connection to a PTY.
    # For Phase 5, we satisfy the temporal workflow by returning connection metadata.
    
    container_name = f"moonmind_auth_{session_id}"
    logger.info("Starting auth runner container %s for %s", container_name, session_id)
    
    runner_image = os.environ.get("MOONMIND_OAUTH_RUNNER_IMAGE", "alpine:3.19")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "-d", "--rm",
            "--name", container_name,
            "--label", "moonmind.oauth_session=true",
            "--label", f"moonmind.oauth_session_id={session_id}",
            "--label", f"moonmind.runtime_id={runtime_id}",
            "-v", f"{volume_ref}:{volume_mount_path}",
            runner_image, "sleep", str(session_ttl),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        logger.error(
            "Failed to start auth container %s: docker CLI not found on PATH",
            container_name,
        )
        raise RuntimeError(
            "Docker CLI is not available on this worker. "
            "Ensure Docker is installed and 'docker' is on the PATH, "
            "or configure a different terminal bridge backend."
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError("Timed out while starting auth container")

    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to start auth container: {stderr.decode(errors='replace')}"
        )
        
    return {
        "container_name": container_name,
        "terminal_session_id": f"term_{session_id}",
        "terminal_bridge_id": f"br_{session_id}",
        "session_transport": "moonmind_pty_ws",
    }
