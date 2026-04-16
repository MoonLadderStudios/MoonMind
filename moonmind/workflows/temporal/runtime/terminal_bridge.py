"Terminal PTY bridge startup and frame validation logic."

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
_MAX_RECORDED_TERMINAL_EVENTS = 256


class TerminalBridgeFrameError(ValueError):
    """Raised when a browser terminal frame is unsupported or unsafe."""


@dataclass
class TerminalBridgeConnection:
    """Validate browser terminal frames before they reach the auth runner PTY."""

    session_id: str
    terminal_bridge_id: str
    owner_user_id: str | None
    resize_events: deque[tuple[int, int]] = field(
        default_factory=lambda: deque(maxlen=_MAX_RECORDED_TERMINAL_EVENTS)
    )
    input_events: deque[str] = field(
        default_factory=lambda: deque(maxlen=_MAX_RECORDED_TERMINAL_EVENTS)
    )
    heartbeat_count: int = 0
    output_events: deque[str] = field(
        default_factory=lambda: deque(maxlen=_MAX_RECORDED_TERMINAL_EVENTS)
    )

    def handle_frame(self, frame: dict[str, Any]) -> dict[str, Any]:
        frame_type = str(frame.get("type", "")).strip()
        if frame_type == "resize":
            try:
                cols = int(frame.get("cols", 0))
                rows = int(frame.get("rows", 0))
            except (TypeError, ValueError) as exc:
                raise TerminalBridgeFrameError(
                    "resize dimensions must be integers"
                ) from exc
            if cols <= 0 or rows <= 0 or cols > 500 or rows > 500:
                raise TerminalBridgeFrameError("resize dimensions are out of range")
            self.resize_events.append((cols, rows))
            return {"type": "resize_ack", "cols": cols, "rows": rows}
        if frame_type == "input":
            data = frame.get("data")
            if not isinstance(data, str):
                raise TerminalBridgeFrameError("input frame data must be text")
            self.input_events.append(data)
            return {"type": "input_ack", "bytes": len(data.encode("utf-8"))}
        if frame_type == "heartbeat":
            self.heartbeat_count += 1
            return {"type": "heartbeat_ack"}
        if frame_type == "close":
            return {"type": "close_ack"}
        if frame_type == "output":
            data = frame.get("data")
            if not isinstance(data, str):
                raise TerminalBridgeFrameError("output frame data must be text")
            self.output_events.append(data)
            return {"type": "output_ack", "bytes": len(data.encode("utf-8"))}
        if frame_type in {"exec", "docker_exec", "task_terminal"}:
            raise TerminalBridgeFrameError(
                "generic Docker exec and task terminal frames are not supported"
            )
        raise TerminalBridgeFrameError(f"unsupported terminal frame type: {frame_type}")

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
