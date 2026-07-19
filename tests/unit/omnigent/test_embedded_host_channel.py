from __future__ import annotations

import asyncio
import json

import pytest

from moonmind.omnigent.embedded_host_channel import (
    EmbeddedHostChannelError,
    EmbeddedHostChannelRegistry,
)


def _hello() -> str:
    return json.dumps({
        "kind": "host.hello", "version": "0.1.0", "frame_protocol_version": 1,
        "name": "stock-host", "runners": [],
    })


@pytest.mark.asyncio
async def test_channel_requires_hello_then_correlates_command_result() -> None:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    channel = EmbeddedHostChannelRegistry().connect(host_id="host-1", send_text=send)
    channel.accept_host_frame(_hello())
    frames = channel.adapter.frames
    pending = asyncio.create_task(channel.request(frames.HostStopRunnerFrame(
        request_id="req-1", runner_id="runner-1"
    )))
    await asyncio.sleep(0)
    assert json.loads(sent[0])["kind"] == "host.stop_runner"
    channel.accept_host_frame(json.dumps({
        "kind": "host.stop_runner_result", "request_id": "req-1", "status": "stopped"
    }))
    assert (await pending).status == "stopped"


def test_channel_rejects_non_hello_first_and_ignores_late_result() -> None:
    async def send(_text: str) -> None:
        pass

    channel = EmbeddedHostChannelRegistry().connect(host_id="host-1", send_text=send)
    with pytest.raises(EmbeddedHostChannelError, match="first frame"):
        channel.accept_host_frame(json.dumps({
            "kind": "host.runner_exited", "runner_id": "r", "error": "exit"
        }))
    channel.accept_host_frame(_hello())
    result = channel.accept_host_frame(json.dumps({
        "kind": "host.stop_runner_result", "request_id": "unknown", "status": "stopped"
    }))
    assert result.status == "stopped"


@pytest.mark.asyncio
async def test_reconnect_fails_pending_request_and_replaces_channel() -> None:
    async def send(_text: str) -> None:
        pass

    registry = EmbeddedHostChannelRegistry()
    first = registry.connect(host_id="host-1", send_text=send)
    first.accept_host_frame(_hello())
    frames = first.adapter.frames
    pending = asyncio.create_task(first.request(frames.HostStopRunnerFrame(
        request_id="req-1", runner_id="runner-1"
    )))
    await asyncio.sleep(0)
    second = registry.connect(host_id="host-1", send_text=send)
    with pytest.raises(EmbeddedHostChannelError, match="disconnected"):
        await pending
    assert second.accept_host_frame(_hello()) is second.hello
    assert registry.get_ready("host-1") is second


@pytest.mark.asyncio
async def test_launch_runner_uses_exact_host_and_rejects_identity_substitution() -> None:
    registry = EmbeddedHostChannelRegistry()
    sent: list[dict[str, object]] = []

    async def send(text: str) -> None:
        payload = json.loads(text)
        sent.append(payload)
        registry.get_ready("host-1").accept_host_frame(json.dumps({
            "kind": "host.launch_runner_result",
            "request_id": payload["request_id"],
            "status": "launched",
            "runner_id": "runner_attacker",
        }))

    channel = registry.connect(host_id="host-1", send_text=send)
    channel.accept_host_frame(_hello())
    with pytest.raises(EmbeddedHostChannelError, match="invalid runner identity"):
        await registry.launch_runner(
            host_id="host-1", workspace="/work/repo",
            session_id="session-1", harness="codex-native",
        )
    assert sent[0]["workspace"] == "/work/repo"
    assert sent[0]["session_id"] == "session-1"
    assert sent[0]["harness"] == "codex-native"
