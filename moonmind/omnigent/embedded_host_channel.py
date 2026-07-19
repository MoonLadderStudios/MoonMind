"""Live transport state for the embedded stock Omnigent host channel.

Sockets are deliberately process-local; durable session/lease ownership remains
in :mod:`bridge_store`.  Reconnect replaces the live sender for the same
authenticated host, while request correlation is bounded to the connection.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from moonmind.omnigent.host_protocol_adapter import OmnigentHostProtocolAdapter
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter


SendText = Callable[[str], Awaitable[None]]


class EmbeddedHostChannelError(RuntimeError):
    """A host channel violated lifecycle or correlation rules."""


@dataclass(slots=True)
class EmbeddedHostChannel:
    host_id: str
    send_text: SendText
    adapter: OmnigentHostProtocolAdapter
    hello: Any | None = None
    _pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)

    def accept_host_frame(self, text: str) -> Any:
        frame = self.adapter.decode_host_frame(text)
        frames = self.adapter.frames
        if self.hello is None:
            if not isinstance(frame, frames.HostHelloFrame):
                raise EmbeddedHostChannelError("host hello must be the first frame")
            self.hello = frame
            return frame
        if isinstance(frame, frames.HostHelloFrame):
            raise EmbeddedHostChannelError("duplicate host hello on one connection")
        request_id = str(getattr(frame, "request_id", "") or "")
        if request_id:
            future = self._pending.pop(request_id, None)
            if future is not None and not future.done():
                future.set_result(frame)
        return frame

    async def request(self, frame: Any, *, timeout_seconds: float = 30.0) -> Any:
        if self.hello is None:
            raise EmbeddedHostChannelError("host is not ready")
        request_id = str(getattr(frame, "request_id", "") or "")
        if not request_id or request_id in self._pending:
            raise EmbeddedHostChannelError("command request id is missing or active")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send_text(self.adapter.encode_server_frame(frame))
            return await asyncio.wait_for(future, timeout_seconds)
        finally:
            self._pending.pop(request_id, None)

    def disconnect(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(EmbeddedHostChannelError("host disconnected"))
        self._pending.clear()


class EmbeddedHostChannelRegistry:
    """Process-local index of authenticated live host connections."""

    def __init__(self) -> None:
        self._channels: dict[str, EmbeddedHostChannel] = {}
        self._runner_tokens: dict[str, str] = {}

    def connect(self, *, host_id: str, send_text: SendText) -> EmbeddedHostChannel:
        previous = self._channels.get(host_id)
        if previous is not None:
            previous.disconnect()
        channel = EmbeddedHostChannel(
            host_id=host_id,
            send_text=send_text,
            adapter=OmnigentHostProtocolAdapter(),
        )
        self._channels[host_id] = channel
        return channel

    def disconnect(self, channel: EmbeddedHostChannel) -> None:
        channel.disconnect()
        if self._channels.get(channel.host_id) is channel:
            self._channels.pop(channel.host_id, None)

    def get_ready(self, host_id: str) -> EmbeddedHostChannel:
        channel = self._channels.get(host_id)
        if channel is None or channel.hello is None:
            raise EmbeddedHostChannelError("assigned host is not connected and ready")
        return channel

    async def launch_runner(
        self, *, host_id: str, workspace: str, session_id: str, harness: str
    ) -> str:
        """Launch on the exact authenticated host and verify its bound identity."""

        channel = self.get_ready(host_id)
        binding_token = secrets.token_urlsafe(32)
        identity = OmnigentHostAuthAdapter(
            allowed_tokens=frozenset({binding_token})
        ).runner_id_for_binding_token(binding_token)
        frame = channel.adapter.frames.HostLaunchRunnerFrame(
            request_id=f"launch_{secrets.token_hex(16)}",
            binding_token=binding_token,
            workspace=workspace,
            session_id=session_id,
            harness=harness,
        )
        result = await channel.request(frame)
        if result.status != "launched":
            error_code = str(getattr(result, "error_code", "") or "").strip()
            error = str(getattr(result, "error", "") or "").strip()
            detail = ": ".join(part for part in (error_code, error) if part)
            raise EmbeddedHostChannelError(
                f"host rejected runner launch{': ' + detail if detail else ''}"
            )
        if result.runner_id != identity:
            raise EmbeddedHostChannelError("host returned an invalid runner identity")
        self._runner_tokens[identity] = binding_token
        return identity

    def authenticate_runner(self, *, runner_id: str, headers: Any) -> str:
        """Verify a spawned runner against its host-launch binding token."""

        token = self._runner_tokens.get(runner_id)
        if token is None:
            raise EmbeddedHostChannelError("runner launch binding is unavailable")
        identity = OmnigentHostAuthAdapter(
            allowed_tokens=frozenset({token})
        ).verify(headers)
        if identity.runner_id != runner_id:
            raise EmbeddedHostChannelError("runner id does not match launch binding")
        return identity.runner_id


embedded_host_channels = EmbeddedHostChannelRegistry()

__all__ = [
    "EmbeddedHostChannel",
    "EmbeddedHostChannelError",
    "EmbeddedHostChannelRegistry",
    "embedded_host_channels",
]
