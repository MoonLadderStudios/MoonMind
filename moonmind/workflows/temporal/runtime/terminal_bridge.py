"Terminal PTY bridge startup and frame validation logic."

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import os
import shlex
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)
_MAX_RECORDED_TERMINAL_EVENTS = 256


class TerminalBridgeFrameError(ValueError):
    """Raised when a browser terminal frame is unsupported or unsafe."""


class PtyAdapter(Protocol):
    """Runtime adapter for one OAuth auth-runner PTY connection."""

    async def connect(self) -> None:
        """Open the PTY connection."""

    async def write_bytes(self, data: bytes) -> None:
        """Write terminal input bytes to the PTY."""

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY."""

    async def output_chunks(self) -> AsyncIterator[bytes]:
        """Yield terminal output chunks from the PTY."""

    async def close(self) -> None:
        """Close PTY resources."""


class InMemoryPtyAdapter:
    """Deterministic PTY adapter for unit tests."""

    def __init__(self, *, output_chunks: list[bytes] | None = None) -> None:
        self.output_chunks_source = list(output_chunks or [])
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def write_bytes(self, data: bytes) -> None:
        self.written.append(data)

    async def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    async def output_chunks(self) -> AsyncIterator[bytes]:
        for chunk in self.output_chunks_source:
            yield chunk

    async def close(self) -> None:
        self.closed = True


def _provider_bootstrap_shell_command(runtime_id: str) -> str:
    from moonmind.workflows.temporal.runtime.providers.registry import get_provider

    provider = get_provider(runtime_id)
    if provider is None:
        raise ValueError(f"Unsupported OAuth runtime: {runtime_id}")
    command = provider.get("bootstrap_command") or []
    if not command:
        raise ValueError(f"OAuth runtime {runtime_id} has no bootstrap command")
    return " ".join(shlex.quote(str(part)) for part in command)


class DockerExecPtyAdapter:
    """Docker exec PTY adapter for the OAuth auth-runner container."""

    def __init__(self, *, container_name: str, runtime_id: str) -> None:
        self.container_name = container_name
        self.runtime_id = runtime_id
        self._client: Any | None = None
        self._raw_sock: Any | None = None
        self._exec_id: str | None = None

    async def connect(self) -> None:
        import docker

        self._client = docker.from_env()
        container = self._client.containers.get(self.container_name)
        exec_command = _provider_bootstrap_shell_command(self.runtime_id)
        exec_instance = self._client.api.exec_create(
            container=container.id,
            cmd=["/bin/sh", "-lc", exec_command],
            tty=True,
            stdin=True,
            stdout=True,
            stderr=True,
        )
        self._exec_id = exec_instance["Id"]
        sock = self._client.api.exec_start(self._exec_id, socket=True, tty=True)
        self._raw_sock = sock._sock if hasattr(sock, "_sock") else sock
        self._raw_sock.setblocking(False)

    async def write_bytes(self, data: bytes) -> None:
        if self._raw_sock is None:
            raise RuntimeError("PTY adapter is not connected")
        loop = asyncio.get_running_loop()
        await loop.sock_sendall(self._raw_sock, data)

    async def resize(self, cols: int, rows: int) -> None:
        if self._client is None or self._exec_id is None:
            raise RuntimeError("PTY adapter is not connected")
        self._client.api.exec_resize(self._exec_id, height=rows, width=cols)

    async def output_chunks(self) -> AsyncIterator[bytes]:
        if self._raw_sock is None:
            raise RuntimeError("PTY adapter is not connected")
        loop = asyncio.get_running_loop()
        while True:
            data = await loop.sock_recv(self._raw_sock, 4096)
            if not data:
                break
            if isinstance(data, bytes):
                yield data
            else:
                yield str(data).encode("utf-8")

    async def close(self) -> None:
        if self._raw_sock is not None:
            self._raw_sock.close()
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()


def create_docker_exec_pty_adapter(
    *, container_name: str, runtime_id: str
) -> DockerExecPtyAdapter:
    return DockerExecPtyAdapter(container_name=container_name, runtime_id=runtime_id)


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
    input_event_count: int = 0
    output_event_count: int = 0

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
            self.input_event_count += 1
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
            self.output_event_count += 1
            return {"type": "output_ack", "bytes": len(data.encode("utf-8"))}
        if frame_type in {"exec", "docker_exec", "task_terminal"}:
            raise TerminalBridgeFrameError(
                "generic Docker exec and task terminal frames are not supported"
            )
        raise TerminalBridgeFrameError(f"unsupported terminal frame type: {frame_type}")

    async def handle_frame_for_pty(
        self, frame: dict[str, Any], pty: PtyAdapter
    ) -> dict[str, Any]:
        frame_type = str(frame.get("type", "")).strip()
        response = self.handle_frame(frame)
        if frame_type == "input":
            await pty.write_bytes(str(frame["data"]).encode("utf-8"))
        elif frame_type == "resize":
            await pty.resize(int(response["cols"]), int(response["rows"]))
        return response

    async def stream_pty_output(
        self,
        pty: PtyAdapter,
        send_output: Callable[[bytes], Awaitable[None] | None],
    ) -> None:
        async for chunk in pty.output_chunks():
            self.output_event_count += 1
            result = send_output(chunk)
            if result is not None:
                await result

    def safe_metadata(self) -> dict[str, int]:
        metadata: dict[str, int] = {
            "terminal_heartbeat_count": self.heartbeat_count,
            "terminal_input_event_count": self.input_event_count,
            "terminal_output_event_count": self.output_event_count,
        }
        if self.resize_events:
            cols, rows = self.resize_events[-1]
            metadata["terminal_last_cols"] = cols
            metadata["terminal_last_rows"] = rows
        return metadata

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
