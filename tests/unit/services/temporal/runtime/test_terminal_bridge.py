"""Unit tests for OAuth terminal bridge frame handling."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.terminal_bridge import (
    InMemoryPtyAdapter,
    TerminalBridgeConnection,
    TerminalBridgeFrameError,
)


def test_terminal_bridge_handles_resize_input_output_and_heartbeat() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_frames",
        terminal_bridge_id="br_oas_terminal_frames",
        owner_user_id="user-1",
    )

    assert bridge.handle_frame({"type": "resize", "cols": 120, "rows": 36}) == {
        "type": "resize_ack",
        "cols": 120,
        "rows": 36,
    }
    assert bridge.handle_frame({"type": "input", "data": "codex login\n"}) == {
        "type": "input_ack",
        "bytes": 12,
    }
    assert bridge.handle_frame({"type": "output", "data": "Open browser\n"}) == {
        "type": "output_ack",
        "bytes": 13,
    }
    assert bridge.handle_frame({"type": "heartbeat"}) == {"type": "heartbeat_ack"}
    assert list(bridge.resize_events) == [(120, 36)]
    assert list(bridge.input_events) == ["codex login\n"]
    assert list(bridge.output_events) == ["Open browser\n"]
    assert bridge.heartbeat_count == 1


def test_terminal_bridge_rejects_malformed_resize_dimensions() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_bad_resize",
        terminal_bridge_id="br_oas_terminal_bad_resize",
        owner_user_id="user-1",
    )

    with pytest.raises(TerminalBridgeFrameError, match="resize dimensions must be integers"):
        bridge.handle_frame({"type": "resize", "cols": "wide", "rows": 36})


def test_terminal_bridge_keeps_bounded_event_metadata() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_bounded",
        terminal_bridge_id="br_oas_terminal_bounded",
        owner_user_id="user-1",
    )

    for index in range(300):
        bridge.handle_frame({"type": "input", "data": f"line-{index}\n"})

    assert len(bridge.input_events) == 256
    assert bridge.input_events[0] == "line-44\n"
    assert bridge.input_events[-1] == "line-299\n"


def test_terminal_bridge_rejects_generic_exec_frames() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_exec",
        terminal_bridge_id="br_oas_terminal_exec",
        owner_user_id="user-1",
    )

    with pytest.raises(TerminalBridgeFrameError, match="generic Docker exec"):
        bridge.handle_frame({"type": "docker_exec", "command": "sh"})


@pytest.mark.asyncio
async def test_terminal_bridge_forwards_input_and_resize_to_pty_adapter() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_pty",
        terminal_bridge_id="br_oas_terminal_pty",
        owner_user_id="user-1",
    )
    pty = InMemoryPtyAdapter(output_chunks=[b"Open https://example.test/login\r\n"])

    await pty.connect()
    assert await bridge.handle_frame_for_pty(
        {"type": "input", "data": "codex login\n"}, pty
    ) == {"type": "input_ack", "bytes": 12}
    assert await bridge.handle_frame_for_pty(
        {"type": "resize", "cols": 132, "rows": 40}, pty
    ) == {"type": "resize_ack", "cols": 132, "rows": 40}
    assert await bridge.handle_frame_for_pty({"type": "heartbeat"}, pty) == {
        "type": "heartbeat_ack"
    }

    assert pty.written == [b"codex login\n"]
    assert pty.resizes == [(132, 40)]
    assert bridge.input_event_count == 1
    assert bridge.output_event_count == 0
    assert bridge.heartbeat_count == 1
    assert list(bridge.input_events) == ["codex login\n"]


@pytest.mark.asyncio
async def test_terminal_bridge_streams_output_without_persisting_raw_output() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_output",
        terminal_bridge_id="br_oas_terminal_output",
        owner_user_id="user-1",
    )
    pty = InMemoryPtyAdapter(
        output_chunks=[
            b"Paste code from browser\r\n",
            b"token=sk-test-secret should not be metadata\r\n",
        ]
    )
    sent: list[bytes] = []

    await pty.connect()
    await bridge.stream_pty_output(pty, sent.append)

    assert sent == [
        b"Paste code from browser\r\n",
        b"token=sk-test-secret should not be metadata\r\n",
    ]
    assert bridge.output_event_count == 2
    assert list(bridge.output_events) == []
