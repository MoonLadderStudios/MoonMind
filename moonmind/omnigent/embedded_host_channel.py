"""Live transport state for the embedded stock Omnigent host channel.

Sockets are deliberately process-local; durable session/lease ownership remains
in :mod:`bridge_store`.  Reconnect replaces the live sender for the same
authenticated host, while request correlation is bounded to the connection.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from moonmind.omnigent.host_protocol_adapter import OmnigentHostProtocolAdapter
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter


SendText = Callable[[str], Awaitable[None]]


class EmbeddedHostChannelError(RuntimeError):
    """A host channel violated lifecycle or correlation rules."""


@dataclass(slots=True)
class EmbeddedRunnerChannel:
    """One live stock-runner HTTP tunnel using the pinned frame codec."""

    runner_id: str
    send_text: SendText
    frames: Any
    hello: Any
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = {"future": future, "status": None, "body": []}
        frame = self.frames.RequestFrame(
            id=request_id,
            method="POST",
            path=path,
            headers=[["content-type", "application/json"]],
            body=json.dumps(payload, separators=(",", ":")),
        )
        try:
            await self.send_text(self.frames.encode_frame(frame))
            return await asyncio.wait_for(future, 30.0)
        finally:
            self._pending.pop(request_id, None)

    def accept_frame(self, text: str) -> None:
        try:
            frame = self.frames.decode_frame(text)
        except ValueError as exc:
            raise EmbeddedHostChannelError("runner frame was rejected") from exc
        request_id = str(getattr(frame, "id", "") or "")
        pending = self._pending.get(request_id)
        if pending is None:
            return
        if isinstance(frame, self.frames.ResponseHeadFrame):
            pending["status"] = frame.status
        elif isinstance(frame, self.frames.ResponseBodyFrame):
            pending["body"].append(self.frames.decode_body(frame.body, frame.encoding))
        elif isinstance(frame, self.frames.ResponseEndFrame):
            status = pending["status"]
            body = b"".join(pending["body"])
            if status is None:
                error = EmbeddedHostChannelError("runner response ended without headers")
                pending["future"].set_exception(error)
            elif status < 200 or status >= 300:
                pending["future"].set_exception(
                    EmbeddedHostChannelError(f"runner request failed with HTTP {status}")
                )
            else:
                try:
                    value = json.loads(body or b"{}")
                except (TypeError, ValueError) as exc:
                    pending["future"].set_exception(
                        EmbeddedHostChannelError("runner returned invalid JSON")
                    )
                else:
                    pending["future"].set_result(value)

    def disconnect(self) -> None:
        for pending in self._pending.values():
            future = pending["future"]
            if not future.done():
                future.set_exception(EmbeddedHostChannelError("runner disconnected"))
        self._pending.clear()


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
        self._runners: dict[str, EmbeddedRunnerChannel] = {}

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

    async def stop_runner(self, *, host_id: str, runner_id: str) -> None:
        """Stop a runner on its assigned host using the pinned host protocol."""

        channel = self.get_ready(host_id)
        frame = channel.adapter.frames.HostStopRunnerFrame(
            request_id=f"stop_{secrets.token_hex(16)}",
            runner_id=runner_id,
        )
        result = await channel.request(frame)
        if result.status != "stopped":
            error = str(getattr(result, "error", "") or "").strip()
            raise EmbeddedHostChannelError(
                f"host rejected runner stop{': ' + error if error else ''}"
            )
        self._runner_tokens.pop(runner_id, None)

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

    def revoke_runner_binding(self, runner_id: str) -> None:
        """Invalidate a terminal runner's credential and live tunnel.

        Reconnects remain possible while the runner is active, but an exit
        frame is authoritative: its launch credential must never authenticate
        a later, replayed tunnel.
        """

        self._runner_tokens.pop(runner_id, None)
        channel = self._runners.pop(runner_id, None)
        if channel is not None:
            channel.disconnect()

    def connect_runner(
        self, *, runner_id: str, send_text: SendText, hello_text: str
    ) -> EmbeddedRunnerChannel:
        """Register a newest-wins runner tunnel after binding-token auth."""

        from moonmind.omnigent.runner_protocol_adapter import runner_frames

        frames = runner_frames()
        try:
            hello = frames.decode_frame(hello_text)
        except ValueError as exc:
            raise EmbeddedHostChannelError("runner hello was rejected") from exc
        if not isinstance(hello, frames.HelloFrame):
            raise EmbeddedHostChannelError("runner hello must be the first frame")
        if hello.frame_protocol_version != 1:
            raise EmbeddedHostChannelError("runner frame protocol major is incompatible")
        previous = self._runners.get(runner_id)
        if previous is not None:
            previous.disconnect()
        channel = EmbeddedRunnerChannel(runner_id, send_text, frames, hello)
        self._runners[runner_id] = channel
        return channel

    def disconnect_runner(self, channel: EmbeddedRunnerChannel) -> None:
        channel.disconnect()
        if self._runners.get(channel.runner_id) is channel:
            self._runners.pop(channel.runner_id, None)

    async def post_runner_event(
        self, *, runner_id: str, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        channel = self._runners.get(runner_id)
        if channel is None:
            raise EmbeddedHostChannelError("assigned runner is not connected")
        return await channel.post_json(f"/v1/sessions/{session_id}/events", payload)


embedded_host_channels = EmbeddedHostChannelRegistry()

__all__ = [
    "EmbeddedHostChannel",
    "EmbeddedHostChannelError",
    "EmbeddedHostChannelRegistry",
    "EmbeddedRunnerChannel",
    "embedded_host_channels",
]
